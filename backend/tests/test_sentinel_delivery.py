"""Sentinel adversarial tests: delivery robustness (Dispatch fix/delivery-robust-1109 @ 4d5aad44).

Every test asserts the SAFE behaviour. A failing test is a finding (see REPORT.md).
Run from an archive copy of backend/:  python -m pytest tests/test_sentinel_delivery.py -q
No network: FakeSupabase + FakeLedger + stubbed Stripe/Resend.
"""
import asyncio
import uuid

import pytest

from app.services import delivery_ledger as dl
from app.services import payout_source as ps
from tests._fake_ledger import FakeLedger
from tests._fake_supabase import FakeSupabase

pytestmark = pytest.mark.real_payment_proof


def run(c):
    return asyncio.run(c)


def U():
    return str(uuid.uuid4())


@pytest.fixture
def ledger(monkeypatch):
    return FakeLedger(monkeypatch)


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    async def alert(key, job_id, title, lines):
        if key in {s[0] for s in sent}:
            return False
        sent.append((key, title)); return True
    monkeypatch.setattr(dl, "alert_kam", alert)
    return sent


class CheckViolation(Exception):
    code = "23514"

    def __str__(self):
        return 'new row for relation "payouts" violates check constraint "payouts_amount_positive"'


def live_like_db(**tables):
    """FakeSupabase + the live payouts CHECK (amount > 0) constraint (pg_constraint, 2026-11-09)."""
    db = FakeSupabase()
    for k, v in tables.items():
        db.tables[k] = v
    real = db.table

    def table(name):
        q = real(name)
        if name == "payouts":
            orig = q.execute

            async def execute():
                p = q.payload or {}
                if q.op == "insert" and "amount" in p and not (p.get("amount") or 0) > 0:
                    raise CheckViolation()
                if q.op == "update" and "amount" in p and not (p.get("amount") or 0) > 0:
                    raise CheckViolation()
                return await orig()
            q.execute = execute
        return q
    db.table = table
    return db


def _job(jid="j1", bid=None, **kw):
    j = {"id": jid, "booking_id": bid, "status": "delivered", "vendor_id": "default_vendor",
         "assignee_id": "m1", "address": "1 Test Lane", "rack_price": 30000, "vendor_tier": 1}
    j.update(kw)
    return j


@pytest.fixture
def pay_env(monkeypatch, ledger, alerts):
    """trigger_payout_for_job with Stripe stubbed and a live key + live paid booking by default."""
    from app.services import payout_service as svc, probation_hold
    import app.services.stripe_connect_service as scs
    calls = []
    state = {"src": {"livemode": True, "refunded": False, "charge_id": None, "source": "t"}, "block": None,
             "transfer": None}

    async def transfer(**k):
        calls.append(k)
        if state["transfer"]:
            return await state["transfer"](len(calls), **k)
        return f"tr_{len(calls)}"

    async def src(db, job, with_charge=True):
        return dict(state["src"])

    async def block(db, job, src=None, key_mode=None):
        return state["block"]

    async def no_hold(db, job):
        return None

    async def rate(*a, **k):
        return 10000
    monkeypatch.setattr(scs, "handle_payout_to_vendor", transfer)
    monkeypatch.setattr(ps, "booking_source", src)
    monkeypatch.setattr(ps, "payment_block", block)
    monkeypatch.setattr(ps, "stripe_key_mode", lambda: True)
    monkeypatch.setattr(probation_hold, "hold_for_payout", no_hold)
    monkeypatch.setattr(svc, "get_fixed_payout_amount", rate)
    return svc, calls, state


# ============================================================ (5) payment-proof gate

def test_S5a_blocked_payout_row_survives_live_amount_check(monkeypatch, ledger, alerts):
    """Claim: 'records a blocked payout at $0'. Live payouts has CHECK (amount > 0)."""
    from app.services import payout_service as svc
    db = live_like_db(jobs=[_job()])
    out = run(svc._record_block(db, _job(), "no_booking"))
    rows = db.rows("payouts", job_id="j1")
    assert rows and rows[0]["status"] == "blocked", (
        f"no blocked payout row was written on a live-shaped DB (out={out}); sweeps that skip "
        "'blocked' rows will keep retrying/escalating this job")


def test_S5b_partial_refund_seen_only_at_stripe_blocks(monkeypatch):
    """Lead still says 'paid'; Stripe charge is 50% refunded. booking_source only flags FULL refunds."""
    b = U()
    db = FakeSupabase()
    db.tables["media_booking_leads"] = [{"id": b, "stripe_session_id": "cs_live_1", "status": "paid"}]
    monkeypatch.setattr(ps, "stripe_key_mode", lambda key=None: True)
    monkeypatch.setattr(ps, "retrieve_session_charge", lambda sid: {
        "charge_id": "ch_1", "amount": 10000, "amount_refunded": 5000, "refunded": False, "livemode": True})
    job = {"id": "j", "booking_id": b}
    src = run(ps.booking_source(db, job))
    reason = ps.payout_block_reason(src, True) or run(ps.payment_block(db, job, src, key_mode=True))
    assert reason is not None, "a partially refunded (at Stripe) booking pays the vendor in full"


def test_S5c_stripe_lookup_error_is_not_read_as_not_refunded(monkeypatch):
    """retrieve_session_charge swallows every Stripe error and returns {} -> refund check fails OPEN."""
    b = U()
    db = FakeSupabase()
    db.tables["media_booking_leads"] = [{"id": b, "stripe_session_id": "cs_live_1", "status": "paid"}]
    monkeypatch.setattr(ps, "stripe_key_mode", lambda key=None: True)

    class Boom(Exception):
        pass
    import app.services.stripe_connect_service as scs

    class _Sess:
        @staticmethod
        def retrieve(*a, **k):
            raise Boom("stripe 500")
    monkeypatch.setattr(scs.stripe_lib.checkout, "Session", _Sess, raising=False)
    job = {"id": "j", "booking_id": b}
    src = run(ps.booking_source(db, job))
    reason = ps.payout_block_reason(src, True) or run(ps.payment_block(db, job, src, key_mode=True))
    assert reason is not None, "Stripe API error during the refund check is treated as 'not refunded' -> pays"


def test_S5d_disputed_charge_blocks(monkeypatch):
    b = U()
    db = FakeSupabase()
    db.tables["media_booking_leads"] = [{"id": b, "stripe_session_id": "cs_live_1", "status": "paid"}]
    monkeypatch.setattr(ps, "stripe_key_mode", lambda key=None: True)
    monkeypatch.setattr(ps, "retrieve_session_charge", lambda sid: {
        "charge_id": "ch_1", "amount": 10000, "amount_refunded": 0, "refunded": False, "livemode": True,
        "disputed": True})
    job = {"id": "j", "booking_id": b}
    src = run(ps.booking_source(db, job))
    reason = ps.payout_block_reason(src, True) or run(ps.payment_block(db, job, src, key_mode=True))
    assert reason is not None, "a disputed (chargeback) charge still pays out; disputes are never checked"


def test_S5e_bookings_row_fields_are_not_payment_proof(monkeypatch):
    """public.bookings: RLS 'Staff can update bookings' lets the assigned photographer UPDATE
    stripe_payment_status / stripe_session_id / refunded_at (column grants confirmed live).
    payment_proof trusts those strings with no Stripe check."""
    b = U()
    db = FakeSupabase()
    db.tables["bookings"] = [{"id": b, "status": "confirmed", "stripe_session_id": "cs_live_FORGED",
                              "stripe_payment_status": "paid", "refunded_at": None}]
    monkeypatch.setattr(ps, "stripe_key_mode", lambda key=None: True)
    reason = run(ps.payment_block(db, {"id": "j", "booking_id": b}, {}, key_mode=True))
    assert reason is not None, "self-asserted bookings.stripe_payment_status='paid' + 'cs_live_' prefix releases a live payout"


def test_S5f_missing_stripe_key_is_transient_not_permanent(monkeypatch, ledger, alerts):
    """A deploy with STRIPE_SECRET_KEY briefly unset parks every delivered job as 'blocked';
    sweeps skip 'blocked' and confirm-payment refuses stripe_key_unknown -> stuck forever."""
    from app.services import payout_service as svc
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    out = run(svc._record_block(db, _job(), "stripe_key_unknown"))
    assert out["status"] == "failed", "stripe_key_unknown is recorded as permanent 'blocked'"
    assert "stripe_key_unknown" in ps.TRANSIENT_BLOCKS or "stripe_key_unknown" in ps.MANUALLY_CONFIRMABLE


def test_S5g_lookup_failure_plus_old_confirmation_does_not_skip_refund_check(monkeypatch, ledger):
    """Owner confirmed earlier; now the booking read fails (and the booking was refunded meanwhile)."""
    ledger.rows["payment-confirmed:j"] = {"id": "payment-confirmed:j", "entity_id": "j", "created_at": "x"}
    db = FakeSupabase()
    db.down["media_booking_leads"] = True
    reason = run(ps.payment_block(db, {"id": "j", "booking_id": U()}, {}, key_mode=True))
    assert reason is not None, "booking_lookup_failed + an old manual confirmation pays without reading refund state"


# ============================================================ (3) one payout per job / no double pay

def test_S3a_failed_row_that_already_has_a_transfer_is_not_paid_again(pay_env):
    svc, calls, state = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    db.tables["payouts"] = [{"id": svc.payout_row_id("j1"), "job_id": "j1", "status": "failed",
                             "stripe_transfer_id": "tr_already", "amount": 10000}]
    run(svc.trigger_payout_for_job(db, "j1"))
    assert not calls, ("a 'failed' row that already carries a stripe_transfer_id is retried: a second "
                       "Transfer.create is sent (deduped only inside Stripe's 24h idempotency window)")


def test_S3b_concurrent_retry_cannot_leave_a_paid_transfer_marked_failed(pay_env):
    """Run A pays; run B (same idempotency key) gets Stripe 409 idempotency_key_in_use after A wrote 'paid'."""
    svc, calls, state = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    db.tables["payouts"] = [{"id": svc.payout_row_id("j1"), "job_id": "j1", "status": "failed", "amount": 10000}]

    async def transfer(n, **k):
        if n == 1:
            return "tr_A"
        for _ in range(30):
            await asyncio.sleep(0)
        raise RuntimeError("idempotency_key_in_use")
    state["transfer"] = transfer

    async def both():
        await asyncio.gather(svc.trigger_payout_for_job(db, "j1"), svc.trigger_payout_for_job(db, "j1"))
    run(both())
    row = db.one("payouts", job_id="j1")
    assert len(calls) <= 1 or row["status"] == "paid", (
        f"after a race the row is status={row['status']} with stripe_transfer_id={row.get('stripe_transfer_id')}: "
        "the ops sweep will retry it (and after 24h Stripe no longer dedupes the key)")


def test_S3c_payout_id_is_stable_per_job_and_used_as_stripe_key(pay_env):
    svc, calls, state = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    run(svc.trigger_payout_for_job(db, "j1"))
    run(svc.trigger_payout_for_job(db, "j1"))
    assert len(calls) == 1 and calls[0]["payout_id"] == svc.payout_row_id("j1")
    assert len(db.rows("payouts", job_id="j1")) == 1


def test_S3d_three_concurrent_first_payouts_pay_once(pay_env):
    svc, calls, state = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]

    async def three():
        await asyncio.gather(*[svc.trigger_payout_for_job(db, "j1") for _ in range(3)])
    run(three())
    assert len(calls) == 1 and len(db.rows("payouts", job_id="j1")) == 1


def test_S3e_blocked_then_unblocked_reuses_the_same_row(pay_env):
    svc, calls, state = pay_env
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    state["block"] = "no_booking"
    run(svc.trigger_payout_for_job(db, "j1"))
    assert not calls
    state["block"] = None
    run(svc.trigger_payout_for_job(db, "j1"))
    run(svc.trigger_payout_for_job(db, "j1"))
    rows = db.rows("payouts", job_id="j1")
    assert len(calls) == 1 and len(rows) == 1 and rows[0]["status"] == "paid"


# ============================================================ (6) confirm-payment

def _confirm_env(monkeypatch, db, member):
    from app.routers import admin_ops

    async def actor(a, x):
        return member["id"]

    async def get_member(aid, email=None):
        return member

    async def get_db():
        return db
    monkeypatch.setattr(admin_ops, "resolve_vendor_actor_id", actor)
    monkeypatch.setattr(admin_ops, "get_vendor_member", get_member)
    monkeypatch.setattr(admin_ops, "get_db", get_db)
    return admin_ops


def test_S6a_confirm_payment_is_kam_only_not_any_role_owner(monkeypatch, ledger, alerts, pay_env):
    """Any vendor_members row with role/title 'Owner' (writable by an admin via PUT /api/vendor-os/team/{id})
    or payout_mode 'owner_full' passes is_owner_member."""
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    admin_ops = _confirm_env(monkeypatch, db, {"id": "someone-else", "role": "Owner", "vendor_id": "default_vendor"})
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        run(admin_ops.confirm_job_payment("j1", admin_ops.ConfirmPaymentBody(note="cheque 123"), "Bearer x", None))


def test_S6b_admin_manager_cannot_confirm(monkeypatch, ledger, alerts, pay_env):
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    admin_ops = _confirm_env(monkeypatch, db, {"id": "mgr", "role": "Manager", "title": "Office admin"})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        run(admin_ops.confirm_job_payment("j1", admin_ops.ConfirmPaymentBody(note="cheque 123"), "Bearer x", None))
    assert e.value.status_code == 403


def test_S6c_replayed_or_parallel_confirm_pays_once(monkeypatch, ledger, alerts, pay_env):
    svc, calls, state = pay_env
    from app.shared.vendor_identity import OWNER_MEMBER_ID
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    admin_ops = _confirm_env(monkeypatch, db, {"id": OWNER_MEMBER_ID, "role": "Owner"})
    body = admin_ops.ConfirmPaymentBody(note="e-transfer ref 42")

    async def go():
        await asyncio.gather(*[admin_ops.confirm_job_payment("j1", body, "Bearer x", None) for _ in range(3)])
    run(go())
    run(admin_ops.confirm_job_payment("j1", body, "Bearer x", None))
    assert len(calls) == 1
    assert len([k for k in ledger.rows if k.startswith("payment-confirmed:j1")]) == 1


def test_S6d_confirm_refuses_test_and_refunded(monkeypatch, ledger, alerts, pay_env):
    svc, calls, state = pay_env
    from app.shared.vendor_identity import OWNER_MEMBER_ID
    from fastapi import HTTPException
    db = FakeSupabase()
    db.tables["jobs"] = [_job()]
    admin_ops = _confirm_env(monkeypatch, db, {"id": OWNER_MEMBER_ID, "role": "Owner"})
    for r in ("test_booking", "booking_refunded", "sandbox_booking", "booking_partially_refunded"):
        state["block"] = r
        with pytest.raises(HTTPException) as e:
            run(admin_ops.confirm_job_payment("j1", admin_ops.ConfirmPaymentBody(note="cheque"), "Bearer x", None))
        assert e.value.status_code == 409
    assert not calls and not [k for k in ledger.rows if k.startswith("payment-confirmed:")]


def test_S6e_note_is_required(monkeypatch):
    from app.routers import admin_ops
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        admin_ops.ConfirmPaymentBody(note="  ")
    with pytest.raises(ValidationError):
        admin_ops.ConfirmPaymentBody()


# ============================================================ (2) delivery email once-only

def test_S2a_two_concurrent_first_sends_call_the_mailer_once(monkeypatch, ledger):
    """Second worker sees try 1 'sending', takes try 2 and sends again (only Resend's key dedupes it)."""
    sends = []

    async def patch_md(job_id, fields):
        return None
    import app.routers.post_production as pp
    monkeypatch.setattr(pp, "_patch_media_delivery", patch_md)

    async def send(job_id, urls, idempotency_key=None):
        sends.append(idempotency_key)
        for _ in range(20):
            await asyncio.sleep(0)
        return "sent"

    async def both():
        return await asyncio.gather(dl.send_delivery_email_once("j1", [], send),
                                    dl.send_delivery_email_once("j1", [], send))
    out = run(both())
    assert len(sends) == 1, f"mailer called {len(sends)}x for one delivery ({out}); relies solely on Resend dedupe"


def test_S2b_retry_window_fits_inside_resend_idempotency_window():
    """Resend keeps Idempotency-Key for 24h; tries 2..5 land at 15m, 1h15m, 7h15m, 31h15m."""
    assert sum(dl.EMAIL_BACKOFF_MIN) < 24 * 60, (
        f"last retry is {sum(dl.EMAIL_BACKOFF_MIN)/60:.2f}h after try 1, outside Resend's 24h key window")


def test_S2c_failed_send_is_not_marked_sent_and_is_retried(monkeypatch, ledger):
    import app.routers.post_production as pp

    async def patch_md(job_id, fields):
        return None
    monkeypatch.setattr(pp, "_patch_media_delivery", patch_md)
    monkeypatch.setattr(dl, "alert_kam", lambda *a, **k: asyncio.sleep(0, result=True))
    statuses = iter(["failed", "sent"])

    async def send(job_id, urls, idempotency_key=None):
        return next(statuses)
    assert run(dl.send_delivery_email_once("j1", [], send)) == "failed"
    assert run(dl.send_delivery_email_once("j1", [], send)) == "sent"
    assert run(dl.send_delivery_email_once("j1", [], send)) == "sent_before"


# ============================================================ (1) approval binding

def test_S1a_manifest_binds_to_file_bytes_not_just_path():
    """Approval hash covers upload_id/kind/provider/sha(path). Overwriting the same storage key
    (R2 presigned PUT is valid 3600s) leaves the hash unchanged."""
    from app.routers import post_production as pp
    u = {"id": "a", "filename": "a.mp4", "file_url": "bricks-go/jobs/j/a/a.mp4", "processed_url": None,
         "processing_status": "done", "qc_status": "passed", "provider": "inhouse"}
    before = pp._still_delivery_url_entry(dict(u, file_size=1000), "u", "")
    after = pp._still_delivery_url_entry(dict(u, file_size=999999), "u", "")
    assert dl.manifest([before])["hash"] != dl.manifest([after])["hash"], "same key, new bytes -> same approval hash"


# ============================================================ (7) stuck-order sweep

def test_S7a_email_retry_skips_jobs_not_in_the_active_set(monkeypatch, ledger):
    """media_delivery rows whose job is cancelled (not loaded into jobs_by_id) are retried to the client."""
    from app.services import delivery_watch as dw
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)
    db = FakeSupabase()
    db.tables["jobs"] = [{"id": "jc", "status": "cancelled", "booking_id": U()}]
    db.tables["media_delivery"] = [{"job_id": "jc", "delivery_urls": [], "notification_status": "failed",
                                    "delivered_at": (now - timedelta(hours=2)).isoformat()}]
    sent = []

    async def send(job_id, urls):
        sent.append(job_id); return "sent"
    import app.routers.post_production as pp
    monkeypatch.setattr(pp, "_send_delivery_notification", send)

    async def a(*x, **k):
        return True
    monkeypatch.setattr(dl, "alert_kam", a)
    run(dw.run(db, now))
    assert not sent, "a cancelled order's client was emailed 'your photos are ready' by the sweep"
