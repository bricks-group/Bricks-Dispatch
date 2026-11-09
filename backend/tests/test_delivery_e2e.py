"""End-to-end delivery chain in test mode (Kam 2026-11-09).

Runs the real code path: upload done -> QC -> fulfillment gate hold -> editor
approval bound to the exact file set -> delivery -> email + push once ->
vendor payout once -> late files held for a fresh sign-off -> retries, dead
letter and stuck-order alerts. The database is an in-memory stand-in
(tests/_fake_supabase.py); Stripe is a recorder in test-key mode; every email
and push lands in an outbox and is addressed to raph@brxs.ca. httpx is stubbed
by conftest, so nothing leaves the machine.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests._fake_supabase import FakeSupabase

KAM = "raph@brxs.ca"


def run(c):
    return asyncio.run(c)


class Chain:
    def __init__(self, monkeypatch):
        from app.routers import admin_ops, post_production as pp
        from app.services import (client_comms, delivery_ledger as dl, editor_ledger, payout_service,
                                  payout_source, probation_hold, qc_hold_notify)
        import app.services.stripe_connect_service as scs
        self.pp, self.admin, self.dl = pp, admin_ops, dl
        self.db = FakeSupabase()
        self.outbox: list[dict] = []
        self.pushes: list[dict] = []
        self.transfers: list[dict] = []
        self.fail_delivery_email = False
        self.fail_stripe: str | None = None
        self.clock = [datetime.now(timezone.utc)]
        monkeypatch.setenv("FULFILLMENT_GATE", "1")

        async def get_db():
            return self.db

        async def send(to, subject, html, text=None, from_addr=None, idempotency_key=None):
            if self.fail_delivery_email and str(idempotency_key or "").startswith("delivery-email"):
                return False
            self.outbox.append({"to": to, "subject": subject, "key": idempotency_key})
            return True

        for name, mod in list(sys.modules.items()):
            if not name.startswith("app.") or mod is None:
                continue
            if getattr(mod, "get_db", None) is not None and name != "app.shared.database":
                monkeypatch.setattr(mod, "get_db", get_db, raising=False)
            if getattr(mod, "send_email_via_resend", None) is not None:
                monkeypatch.setattr(mod, "send_email_via_resend", send, raising=False)
        import app.shared.database as database
        monkeypatch.setattr(database, "get_db", get_db)

        async def push(email, address, when, photographer, job_id, *, title_word="", url=None):
            self.pushes.append({"to": email, "job_id": job_id, "title": title_word, "url": url})
            return {"sent": 1}

        async def share(job_id):
            return f"https://app.brxs.ca/share/test-{job_id}"

        monkeypatch.setattr(client_comms, "_push_agent_new_booking", push)
        monkeypatch.setattr(client_comms, "_client_share_link", share)
        monkeypatch.setattr(client_comms, "_agent_allows", lambda *a, **k: True)

        async def transfer(**kw):
            if self.fail_stripe:
                raise RuntimeError(self.fail_stripe)
            await asyncio.sleep(0)
            self.transfers.append(kw)
            return f"tr_test_{len(self.transfers)}"

        monkeypatch.setattr(scs, "handle_payout_to_vendor", transfer)

        # Test mode: a sandbox booking with a Stripe TEST key is allowed to
        # "transfer" (to the recorder). A live key would block it (checked below).
        async def source(db, job):
            return {"source": "stripe", "livemode": False, "charge_id": "ch_test_1"}

        monkeypatch.setattr(payout_source, "booking_source", source)
        monkeypatch.setattr(payout_source, "stripe_key_mode", lambda: False)

        async def no_hold(db, job, now=None):
            return None

        monkeypatch.setattr(probation_hold, "hold_for_payout", no_hold)

        async def rate(db, package_id, sqft, tier):
            return 15000

        monkeypatch.setattr(payout_service, "get_fixed_payout_amount", rate)

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(editor_ledger, "record_editor_cost", noop)
        monkeypatch.setattr(qc_hold_notify, "notify_owner_qc_hold", noop)

        async def qc(job_id):
            n = 0
            for u in self.db.rows("uploads", job_id=job_id):
                if not u.get("qc_status") or u["qc_status"] == "pending":
                    u["qc_status"] = "passed"; n += 1
            return {"passed": n}

        monkeypatch.setattr(pp, "run_qc_for_job", qc)

        async def sign(path, *a, **k):
            return f"https://store.test/sign/{path}?token={len(self.outbox)}-{id(path)}"

        monkeypatch.setattr(pp, "get_signed_download_url", sign)

        async def admin(*a, **k):
            return "kam-admin"

        monkeypatch.setattr(admin_ops, "_require_admin", admin)
        monkeypatch.setattr(dl, "_now", lambda: self.clock[0])

    def add_job(self, jid, files=("a", "b")):
        now = datetime.now(timezone.utc).isoformat()
        self.db.tables.setdefault("jobs", []).append({
            "id": jid, "booking_id": f"bk-{jid}", "vendor_id": "default_vendor", "assignee_id": "m1",
            "package_id": "photos_only", "status": "processing", "agent_email": KAM, "agent_name": "Kam",
            "address": "1 Test Lane", "rack_price": 30000, "created_at": now, "updated_at": now,
        })
        self.db.tables.setdefault("vendor_members", [{"id": "m1", "user_id": "m1", "tier": 2, "role": "contractor"}])
        for f in files:
            self.add_file(jid, f)

    def add_file(self, jid, f):
        self.db.tables.setdefault("uploads", []).append({
            "id": f"{jid}-{f}", "job_id": jid, "filename": f"{f}.jpg", "file_url": f"jobs/{jid}/{f}.jpg",
            "processed_url": f"jobs/{jid}/{f}-edited.jpg", "processing_status": "done", "qc_status": None,
            "provider": "inhouse",
        })

    def job(self, jid):
        return self.db.one("jobs", id=jid)

    def gate(self, jid):
        return self.pp._saved_gate(self.job(jid))

    def approve(self, jid, h):
        return run(self.admin.editor_approve_job(jid, self.admin.EditorApproveBody(manifest_hash=h)))

    def emails(self, prefix):
        return [m for m in self.outbox if str(m["key"] or "").startswith(prefix)]

    def payouts(self, jid):
        return self.db.rows("payouts", job_id=jid)


@pytest.fixture
def chain(monkeypatch):
    return Chain(monkeypatch)


def _refused(c, jid, h, code):
    with pytest.raises(HTTPException) as e:
        c.approve(jid, h)
    assert e.value.status_code == 409 and code in str(e.value.detail)


def test_full_chain_hold_approve_deliver_once_late_files(chain):
    c, J = chain, "job-e2e-1"
    c.add_job(J)

    # 1. Upload done -> QC -> held for the editor. Nothing delivered, paid or emailed.
    run(c.pp._finalize_job_if_complete(J))
    assert c.job(J)["status"] == "processing"
    assert not c.db.rows("media_delivery", job_id=J) and not c.payouts(J) and not c.transfers
    g = c.gate(J)
    assert g["ready"] is False and g["manifest_count"] == 2 and g["manifest_hash"]
    assert not c.emails("delivery-email") and not c.pushes

    # 2. Approving a page that showed a different set is refused; nothing moves.
    _refused(c, J, "0" * 64, "payload_changed")
    assert not c.job(J).get("editor_approved_at")

    # 3. Approve the exact set -> delivered, emailed once, pushed once, paid once.
    out = c.approve(J, g["manifest_hash"])
    assert out["job_status"] == "delivered"
    md = c.db.one("media_delivery", job_id=J)
    assert len(md["delivery_urls"]) == 2 and md["notification_status"] == "sent"
    assert [p["status"] for p in c.payouts(J)] == ["paid"] and len(c.transfers) == 1
    assert len(c.emails("delivery-email")) == 1 and len(c.pushes) == 1
    assert c.pushes[0]["title"] == "Photos ready" and "share" in c.pushes[0]["url"]

    # 4. Re-runs and races never double anything.
    async def race():
        from app.services.payout_service import trigger_payout_for_job
        await asyncio.gather(c.pp._finalize_job_if_complete(J), c.pp._finalize_job_if_complete(J),
                             trigger_payout_for_job(c.db, J), trigger_payout_for_job(c.db, J))
    run(race())
    assert len(c.payouts(J)) == 1 and len(c.transfers) == 1
    assert len(c.emails("delivery-email")) == 1 and len(c.pushes) == 1

    # 5. A file added after delivery waits for a fresh sign-off.
    c.add_file(J, "late")
    run(c.pp._finalize_job_if_complete(J))
    assert len(c.db.one("media_delivery", job_id=J)["delivery_urls"]) == 2
    g = c.gate(J)
    assert g["pending_manifest_count"] == 3
    assert any("waiting for your sign-off" in m["subject"] for m in c.outbox)
    _refused(c, J, g["approved_manifest_hash"], "payload_changed")
    c.approve(J, g["pending_manifest_hash"])
    assert len(c.db.one("media_delivery", job_id=J)["delivery_urls"]) == 3
    assert "pending_manifest_hash" not in c.gate(J)
    assert len(c.transfers) == 1 and len(c.emails("delivery-email")) == 1  # no second pay or mail

    # Audit trail tells the whole story in order.
    acts = [r["action"] for r in run(c.dl.trail(J))]
    for a in ("delivery.gate_hold", "delivery.approve_refused", "delivery.editor_approved",
              "delivery.gate_pass", "delivery.delivered", "delivery.payout", "delivery.late_fold_held"):
        assert a in acts, a
    assert acts.index("delivery.gate_hold") < acts.index("delivery.editor_approved") < acts.index("delivery.delivered")

    # Only Kam was ever contacted; Stripe only saw the test recorder.
    assert c.outbox and all(m["to"] == KAM for m in c.outbox)
    assert all(p["to"] == KAM for p in c.pushes)


def test_two_first_payouts_at_once_pay_one_time(chain):
    c, J = chain, "job-e2e-race"
    c.add_job(J)
    run(c.pp._finalize_job_if_complete(J))
    c.fail_stripe = "hold"  # keep approval from paying so the race below is the first payout
    c.approve(J, c.gate(J)["manifest_hash"])
    c.db.tables["payouts"] = []
    c.job(J)["payout_status"] = None
    c.fail_stripe = None

    async def race():
        from app.services.payout_service import trigger_payout_for_job
        return await asyncio.gather(*[trigger_payout_for_job(c.db, J) for _ in range(3)])
    run(race())
    assert len(c.payouts(J)) == 1 and len(c.transfers) == 1


def test_live_stripe_key_never_pays_a_test_booking(chain, monkeypatch):
    from app.services import payout_source
    monkeypatch.setattr(payout_source, "stripe_key_mode", lambda: True)
    c, J = chain, "job-e2e-live"
    c.add_job(J)
    run(c.pp._finalize_job_if_complete(J))
    c.approve(J, c.gate(J)["manifest_hash"])
    assert c.job(J)["status"] == "delivered" and not c.transfers and not c.payouts(J)


def test_failed_payout_is_recorded_and_kam_told_once_after_24h(chain):
    from app.services import delivery_watch
    c, J = chain, "job-e2e-payfail"
    c.add_job(J)
    run(c.pp._finalize_job_if_complete(J))
    c.fail_stripe = "Stripe account not found"
    c.approve(J, c.gate(J)["manifest_hash"])
    assert [p["status"] for p in c.payouts(J)] == ["failed"] and c.job(J)["payout_status"] == "failed"
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    c.clock[0] = later
    run(delivery_watch.run(c.db, now=later))
    run(delivery_watch.run(c.db, now=later + timedelta(minutes=15)))
    alerts = c.emails(f"kam-alert-payout-stuck:{J}")
    assert len(alerts) == 1 and alerts[0]["to"] == KAM
    reasons = [r["details"].get("reason") for r in run(c.dl.trail(J)) if r["action"] == "delivery.payout_failed"]
    assert reasons and "Stripe account not found" in reasons[0]


def test_delivery_email_retries_then_goes_dead_and_tells_kam_once(chain):
    from app.services import delivery_watch
    c, J = chain, "job-e2e-mail"
    c.add_job(J)
    c.fail_delivery_email = True
    run(c.pp._finalize_job_if_complete(J))
    c.approve(J, c.gate(J)["manifest_hash"])
    assert c.db.one("media_delivery", job_id=J)["notification_status"] == "failed"
    t = datetime.now(timezone.utc)
    for _ in range(40):  # 15-minute sweeps for 10 hours, then hourly for 2 days
        t += timedelta(minutes=15) if t < c.clock[0] + timedelta(hours=10) else timedelta(hours=1)
        c.clock[0] = t
        run(delivery_watch.run(c.db, now=t))
    for _ in range(30):
        t += timedelta(hours=1); c.clock[0] = t
        run(delivery_watch.run(c.db, now=t))
    tries = run(c.dl.email_attempts(J))
    assert len(tries) == c.dl.EMAIL_MAX_TRIES
    assert c.db.one("media_delivery", job_id=J)["notification_status"] == c.dl.DEAD
    assert len(c.emails(f"kam-alert-email-dead:{J}")) == 1
    assert all(m["to"] == KAM for m in c.outbox)
