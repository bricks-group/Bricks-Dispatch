"""Delivery robustness (Kam 2026-11-09): exact-set approval, once-only sends,
retries + dead letter, fail-loud alerts, one payout row per job, stuck sweep."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import delivery_ledger as dl
from tests._fake_ledger import FakeLedger

NOW = datetime(2026, 11, 9, 17, 0, tzinfo=timezone.utc)


def run(c):
    return asyncio.run(c)


def _still(uid="up-1", path="https://cdn/a.jpg"):
    return {"id": uid, "filename": f"{uid}.jpg", "file_url": f"jobs/j/{uid}.jpg", "processed_url": path,
            "processing_status": "done", "qc_status": "passed", "provider": "inhouse"}


# ---------------------------------------------------------------- manifest

def test_manifest_is_order_independent_and_ignores_signed_url_tokens():
    from app.routers import post_production as pp
    a = pp._still_delivery_url_entry(_still("a"), "https://x/sign/a.jpg?token=1", "t1")
    b = pp._still_delivery_url_entry(_still("b"), "https://x/sign/b.jpg?token=2", "t2")
    a2 = dict(a, url="https://x/sign/a.jpg?token=999", expires_at="later")
    assert dl.manifest([a, b])["hash"] == dl.manifest([b, a2])["hash"]
    assert dl.manifest([a])["hash"] != dl.manifest([a, b])["hash"]


def test_manifest_changes_when_a_file_is_re_edited():
    from app.routers import post_production as pp
    before = pp._still_delivery_url_entry(_still("a", "https://cdn/a-v1.jpg"), "u", "")
    after = pp._still_delivery_url_entry(_still("a", "https://cdn/a-v2.jpg"), "u", "")
    assert dl.manifest([before])["hash"] != dl.manifest([after])["hash"]


# ---------------------------------------------------------------- gate binding

def _gate_env(monkeypatch, job):
    from app.routers import post_production as pp
    monkeypatch.setenv("FULFILLMENT_GATE", "1")
    monkeypatch.setattr(pp, "_load_job_row", AsyncMock(return_value=job))
    rec = AsyncMock()
    monkeypatch.setattr(pp, "_record_fulfillment_gate", rec)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(dl, "alert_kam", alerts)
    monkeypatch.setattr("app.services.editor_review.alert_editor_hold", AsyncMock(return_value=True))
    return pp, rec, alerts


def test_gate_delivers_only_the_approved_set(monkeypatch):
    FakeLedger(monkeypatch)
    from app.routers import post_production as pp
    urls = [pp._still_delivery_url_entry(_still(), "u", "")]
    h = dl.manifest(urls)["hash"]
    job = {"id": "j", "package_id": "photos_only", "editor_approved_at": "2026-11-09T15:00:00Z",
           "fulfillment_gate": {"approved_manifest_hash": h, "held_at": "x"}}
    pp, rec, alerts = _gate_env(monkeypatch, job)
    assert run(pp._fulfillment_gate_allows(None, "j", urls, visit_present=False)) is True
    saved = rec.await_args.args[2]
    assert saved["approved_manifest_hash"] == h and saved["manifest_hash"] == h
    assert not alerts.await_count


def test_gate_refuses_when_files_changed_after_approval(monkeypatch):
    led = FakeLedger(monkeypatch)
    from app.routers import post_production as pp
    approved = [pp._still_delivery_url_entry(_still(), "u", "")]
    job = {"id": "j", "package_id": "photos_only", "editor_approved_at": "2026-11-09T15:00:00Z",
           "fulfillment_gate": {"approved_manifest_hash": dl.manifest(approved)["hash"]}}
    pp, rec, alerts = _gate_env(monkeypatch, job)
    now_urls = approved + [pp._still_delivery_url_entry(_still("late"), "u", "")]
    assert run(pp._fulfillment_gate_allows(None, "j", now_urls, visit_present=False)) is False
    saved = rec.await_args.args[2]
    assert saved["ready"] is False and any("changed since the editor approved" in r for r in saved["reasons"])
    assert alerts.await_count == 1 and "changed" in alerts.await_args.args[2].lower()
    assert "delivery.gate_hold" in led.actions("j")


def test_gate_refuses_an_approval_that_names_no_files(monkeypatch):
    FakeLedger(monkeypatch)
    from app.routers import post_production as pp
    job = {"id": "j", "package_id": "photos_only", "editor_approved_at": "2026-11-09T15:00:00Z"}
    pp, rec, alerts = _gate_env(monkeypatch, job)
    urls = [pp._still_delivery_url_entry(_still(), "u", "")]
    assert run(pp._fulfillment_gate_allows(None, "j", urls, visit_present=False)) is False


# ---------------------------------------------------------------- editor approve endpoint

def _approve_env(monkeypatch, job, delivery=None):
    from app.routers import admin_ops as ao
    from app.routers import post_production as pp
    FakeLedger(monkeypatch)
    monkeypatch.setattr(ao, "_require_admin", AsyncMock(return_value="kam"))
    updates = []

    async def _load_one(db, table, col, val, cols="*"):
        if table == "jobs":
            return job
        if table == "media_delivery":
            return delivery
        return None
    monkeypatch.setattr(ao, "_load_one", _load_one)
    q = MagicMock()
    q.update.side_effect = lambda f: (updates.append(f), q)[1]
    q.eq.return_value = q
    q.execute = AsyncMock()
    db = MagicMock()
    db.table.return_value = q
    monkeypatch.setattr(ao, "get_db", AsyncMock(return_value=db))
    fin = AsyncMock()
    monkeypatch.setattr(pp, "_finalize_job_if_complete", fin)
    return ao, updates, fin


def test_approve_refuses_when_the_page_showed_a_different_set(monkeypatch):
    from fastapi import HTTPException
    job = {"id": "j", "status": "processing", "fulfillment_gate": {"manifest_hash": "a" * 64, "manifest_count": 3}}
    ao, updates, fin = _approve_env(monkeypatch, job)
    with pytest.raises(HTTPException) as e:
        run(ao.editor_approve_job("j", ao.EditorApproveBody(manifest_hash="b" * 64)))
    assert e.value.status_code == 409 and e.value.detail["code"] == "payload_changed"
    assert "Refresh" in e.value.detail["message"]
    assert not updates and not fin.await_count
    with pytest.raises(HTTPException):  # no hash at all is refused too
        run(ao.editor_approve_job("j", None))


def test_approve_binds_the_shown_set_then_finalizes(monkeypatch):
    job = {"id": "j", "status": "processing", "fulfillment_gate": {"manifest_hash": "a" * 64, "manifest_count": 3,
                                                                    "manifest_ids": ["u:1"]}}
    ao, updates, fin = _approve_env(monkeypatch, job)
    out = run(ao.editor_approve_job("j", ao.EditorApproveBody(manifest_hash="a" * 64)))
    assert out["status"] == "approved" and fin.await_count == 1
    gate = updates[0]["fulfillment_gate"]
    assert gate["approved_manifest_hash"] == "a" * 64 and gate["approved_by"] == "kam"
    assert updates[0]["editor_approved_by"] == "kam"


def test_approve_with_nothing_checked_yet_is_refused(monkeypatch):
    from fastapi import HTTPException
    ao, updates, fin = _approve_env(monkeypatch, {"id": "j", "status": "processing"})
    with pytest.raises(HTTPException) as e:
        run(ao.editor_approve_job("j", ao.EditorApproveBody(manifest_hash="x")))
    assert e.value.detail["code"] == "nothing_to_approve" and not updates


# ---------------------------------------------------------------- once-only email + retries

def _email_env(monkeypatch):
    led = FakeLedger(monkeypatch)
    patched = []
    from app.routers import post_production as pp
    monkeypatch.setattr(pp, "_patch_media_delivery", AsyncMock(side_effect=lambda j, f: patched.append(dict(f))))
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(dl, "alert_kam", alerts)
    return led, patched, alerts


def test_delivery_email_is_sent_once_with_an_idempotency_key(monkeypatch):
    led, patched, alerts = _email_env(monkeypatch)
    send = AsyncMock(return_value="sent")
    assert run(dl.send_delivery_email_once("j", [], send)) == "sent"
    assert run(dl.send_delivery_email_once("j", [], send)) == "sent_before"
    assert send.await_count == 1 and send.await_args.kwargs["idempotency_key"] == "delivery-email:j"


def test_failed_email_backs_off_then_goes_dead_and_tells_kam_once(monkeypatch):
    led, patched, alerts = _email_env(monkeypatch)
    send = AsyncMock(return_value="failed")
    assert run(dl.send_delivery_email_once("j", [], send)) == "failed"
    tries = run(dl.email_attempts("j"))
    for t in tries:
        t["created_at"] = NOW.isoformat()
    assert dl.email_retry_due(tries, NOW + timedelta(minutes=5)) is False
    assert dl.email_retry_due(tries, NOW + timedelta(minutes=16)) is True
    for _ in range(dl.EMAIL_MAX_TRIES - 1):
        run(dl.send_delivery_email_once("j", [], send))
    assert patched[-1]["notification_status"] == dl.DEAD
    assert send.await_count == dl.EMAIL_MAX_TRIES
    assert run(dl.send_delivery_email_once("j", [], send)) == dl.DEAD  # no more sends
    assert send.await_count == dl.EMAIL_MAX_TRIES
    dead = [c for c in alerts.await_args_list if c.args[0].startswith("email-dead:")]
    assert len(dead) == 1


def test_email_not_sent_when_the_send_log_is_down_and_kam_is_told(monkeypatch):
    led = FakeLedger(monkeypatch, down=True)
    from app.routers import post_production as pp
    monkeypatch.setattr(pp, "_patch_media_delivery", AsyncMock())
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(dl, "alert_kam", alerts)
    send = AsyncMock(return_value="sent")
    assert run(dl.send_delivery_email_once("j", [], send)) == "failed"
    assert not send.await_count and alerts.await_count == 1


# ---------------------------------------------------------------- fail-loud Kam alerts

def test_kam_alert_is_deduped_sent_when_store_down_and_released_on_failure(monkeypatch):
    led = FakeLedger(monkeypatch)
    se = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.notifications.send_email_via_resend", se)
    assert run(dl.alert_kam("k1", "j", "T", ["a"])) is True
    assert run(dl.alert_kam("k1", "j", "T", ["a"])) is False
    assert se.await_count == 1 and se.await_args.args[0] == "raph@brxs.ca"
    assert se.await_args.kwargs["idempotency_key"] == "kam-alert-k1"
    se.return_value = False
    assert run(dl.alert_kam("k2", "j", "T", ["a"])) is False
    assert "kam-alert:k2" not in led.rows  # released: next sweep retries
    led.down = True
    se.return_value = True
    assert run(dl.alert_kam("k3", "j", "T", ["a"])) is True  # store down: still alerts


def test_ops_send_kam_fails_loud(monkeypatch):
    from app.services import ops_autopilot as ops
    db = MagicMock()
    sel = MagicMock()
    sel.eq.return_value = sel
    sel.limit.return_value = sel
    sel.execute = AsyncMock(side_effect=RuntimeError("store down"))
    db.table.return_value.select.return_value = sel
    se = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.notifications.send_email_via_resend", se)
    monkeypatch.setattr("app.services.notifications.create_notification", AsyncMock())
    monkeypatch.setenv("OPS_ACTION_SECRET", "s")
    assert run(ops.send_kam(db, "k", "Title", ["line"])) is True and se.await_count == 1


def test_resend_gets_the_idempotency_header(monkeypatch):
    from app.services import notifications as n
    monkeypatch.setenv("RESEND_API_KEY", "re_test_dummy")
    calls = []

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls.append(headers)
            r = MagicMock(); r.raise_for_status = MagicMock(); return r
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _C)
    assert run(n.send_email_via_resend("raph@brxs.ca", "s", "<p>h</p>", idempotency_key="delivery-email:j"))
    assert calls[0]["Idempotency-Key"] == "delivery-email:j"


# ---------------------------------------------------------------- one payout row per job

def test_payout_row_id_is_stable_per_job():
    from app.services.payout_service import payout_row_id
    assert payout_row_id("j1") == payout_row_id("j1") != payout_row_id("j2")


def test_concurrent_payout_insert_never_pays_twice(monkeypatch):
    from app.services import payout_service as ps
    dup = Exception("duplicate key value violates unique constraint payouts_pkey")
    monkeypatch.setattr(ps, "_db_insert_payout", AsyncMock(side_effect=dup))
    monkeypatch.setattr(ps, "_db_select_payout_by_job", AsyncMock(return_value={"id": "p", "status": "pending"}))
    inserted, existing = run(ps._insert_payout_once(None, "j", {"id": ps.payout_row_id("j")}))
    assert inserted is None and existing["id"] == "p"


# ---------------------------------------------------------------- late files after delivery

def test_late_files_wait_for_a_fresh_sign_off(monkeypatch):
    led = FakeLedger(monkeypatch)
    from app.routers import post_production as pp
    a = pp._still_delivery_url_entry(_still("a"), "u", "")
    late = pp._still_delivery_url_entry(_still("late"), "u", "")
    job = {"id": "j", "package_id": "photos_only", "status": "delivered",
           "fulfillment_gate": {"approved_manifest_hash": dl.manifest([a])["hash"]}}
    pp, rec, alerts = _gate_env(monkeypatch, job)
    assert run(pp._hold_unapproved_additions(None, "j", [a], [a, late])) is None
    saved = rec.await_args.args[2]
    assert saved["pending_manifest_hash"] == dl.manifest([a, late])["hash"] and saved["pending_added"] == 1
    assert alerts.await_count == 1
    job["fulfillment_gate"] = {"approved_manifest_hash": dl.manifest([a, late])["hash"]}
    assert run(pp._hold_unapproved_additions(None, "j", [a], [a, late])) == [a, late]


def test_removals_always_apply(monkeypatch):
    FakeLedger(monkeypatch)
    from app.routers import post_production as pp
    a = pp._still_delivery_url_entry(_still("a"), "u", "")
    b = pp._still_delivery_url_entry(_still("b"), "u", "")
    job = {"id": "j", "package_id": "photos_only", "status": "delivered", "fulfillment_gate": {}}
    pp, rec, alerts = _gate_env(monkeypatch, job)
    assert run(pp._hold_unapproved_additions(None, "j", [a, b], [a])) == [a]


# ---------------------------------------------------------------- stuck sweep

class _Q:
    def __init__(self, rows): self.rows = rows
    def select(self, *a, **k): return self
    def in_(self, col, vals): return _Q([r for r in self.rows if r.get(col) in vals])
    def eq(self, col, val): return _Q([r for r in self.rows if r.get(col) == val])
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    async def execute(self):
        m = MagicMock(); m.data = self.rows; return m


class _DB:
    def __init__(self, **t): self.t = t
    def table(self, n): return _Q(self.t.get(n, []))


def _watch(monkeypatch, db):
    from app.services import delivery_watch as dw
    led = FakeLedger(monkeypatch)
    sent = []

    async def alert(key, job_id, title, lines):
        if key in sent:
            return False
        sent.append(key); return True
    monkeypatch.setattr(dl, "alert_kam", alert)
    monkeypatch.setattr(dw, "_ops_seen", AsyncMock(return_value=False))
    return dw, sent


def test_sweep_alerts_once_for_each_stuck_order_and_ignores_test_orders(monkeypatch):
    old = (NOW - timedelta(hours=30)).isoformat()
    jobs = [
        {"id": "paid-late", "status": "delivered", "payout_status": "failed", "updated_at": old, "booking_id": "b1"},
        {"id": "e2e", "status": "delivered", "payout_status": "failed", "updated_at": old, "booking_id": "e2e-x"},
        {"id": "approved", "status": "processing", "editor_approved_at": (NOW - timedelta(hours=2)).isoformat(),
         "updated_at": old, "fulfillment_gate": {"reasons": ["missing: drone"]}},
        {"id": "unreviewed", "status": "processing", "updated_at": old,
         "fulfillment_gate": {"held_at": (NOW - timedelta(hours=7)).isoformat()}},
        {"id": "fresh", "status": "processing", "updated_at": NOW.isoformat(),
         "fulfillment_gate": {"held_at": (NOW - timedelta(hours=1)).isoformat()}},
    ]
    db = _DB(jobs=jobs, payouts=[], media_delivery=[],
             ops_autopilot_runs=[{"kind": "daily", "created_at": (NOW - timedelta(hours=2)).isoformat()}])
    dw, sent = _watch(monkeypatch, db)
    out = run(dw.run(db, NOW))
    assert sorted(sent) == ["approved-stuck:approved", "payout-stuck:paid-late", "review-stale:unreviewed"]
    assert out["errors"] == []
    run(dw.run(db, NOW + timedelta(minutes=15)))
    assert len(sent) == 3  # once per order


def test_sweep_retries_a_failed_delivery_email_and_flags_a_stale_daily_check(monkeypatch):
    md = [{"job_id": "j", "delivery_urls": [], "notification_status": "failed",
           "delivered_at": (NOW - timedelta(hours=1)).isoformat()}]
    db = _DB(jobs=[{"id": "j", "status": "delivered", "payout_status": "paid", "booking_id": "b"}],
             media_delivery=md, payouts=[], ops_autopilot_runs=[])
    dw, sent = _watch(monkeypatch, db)
    from app.routers import post_production as pp
    resend = AsyncMock(return_value="sent")
    monkeypatch.setattr(pp, "_send_delivery_notification", resend)
    out = run(dw.run(db, NOW))
    assert resend.await_count == 1 and out["email_retried"] == [{"job_id": "j", "status": "sent"}]
    assert any(k.startswith("daily-stale:") for k in sent)


def test_review_queue_shows_the_exact_set_and_late_files(monkeypatch):
    from app.services import editor_review as er
    from app.routers import post_production as pp
    open_job = {"id": "open", "status": "processing", "package_id": "photos_only",
                "fulfillment_gate": {"manifest_hash": "h1", "manifest_count": 1, "manifest_ids": ["u:up-1:photo:inhouse:r"]}}
    late_job = {"id": "late", "status": "delivered", "package_id": "photos_only", "editor_approved_at": "x",
                "fulfillment_gate": {"pending_manifest_hash": "h2", "pending_manifest_count": 2,
                                     "pending_manifest_ids": ["u:up-1:photo::", "u:up-2:photo::"]}}

    async def query(db, table, job_id=None):
        if table == "jobs":
            return [open_job]
        if table == "uploads":
            return [_still("up-1"), _still("up-9")]
        return []
    monkeypatch.setattr(er, "_query", query)
    monkeypatch.setattr(er, "_delivered_with_pending", AsyncMock(return_value=[late_job]))
    monkeypatch.setattr(pp, "_list_delivered_capture_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.storage.get_signed_download_url", AsyncMock(return_value="https://s"))
    items = {i["id"]: i for i in asyncio.run(er.list_review_items(None))}
    assert items["open"]["manifest_hash"] == "h1" and items["open"]["late_files"] is False
    flags = {m["id"]: m["in_delivery"] for m in items["open"]["media"]}
    assert flags == {"up-1": True, "up-9": False}
    assert items["late"]["manifest_hash"] == "h2" and items["late"]["late_files"] is True
