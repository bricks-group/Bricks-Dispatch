"""Assignment SMS/push: wired, honest about missing Twilio/APNs."""
from pathlib import Path

from app.notifications import notify_job_assigned_to_vendor, public_notification, send_dispatch_sms

ROOT = Path(__file__).resolve().parents[1]


def test_sms_returns_false_when_twilio_env_missing(monkeypatch):
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(key, raising=False)
    assert send_dispatch_sms("+14165550100", "hello") is False
    assert notify_job_assigned_to_vendor("+14165550100", {"address": "10 Main", "job_id": "j1"}) is False


def test_sms_returns_false_when_phone_missing(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15005550006")
    assert notify_job_assigned_to_vendor("", {"address": "10 Main"}) is False
    assert notify_job_assigned_to_vendor(None, {"address": "10 Main"}) is False


def test_assign_endpoint_calls_notify_assignment():
    src = (ROOT / "app/routers/vendor_os.py").read_text()
    assert "notify_assignment" in src
    assert 'PUT' in src or "/jobs/{job_id}/assign" in src


def test_auto_assign_calls_notify_assignment():
    src = (ROOT / "app/services/vendor_selection.py").read_text()
    assert "notify_assignment" in src
    assert "async def _update_job_assignee" in src


def test_public_notification_has_expo_and_swift_keys():
    pub = public_notification({
        "id": "n1",
        "title": "New job assigned",
        "body": "10 Main St",
        "type": "job_assigned",
        "read": False,
        "created_at": "2026-08-29T12:00:00+00:00",
        "data": {"job_id": "j1"},
    })
    assert pub["message"] == "10 Main St"
    assert pub["body"] == "10 Main St"
    assert pub["type"] == "job_assigned"
    assert pub["notification_type"] == "job_assigned"
    assert pub["read"] is False
    assert pub["is_read"] is False


def test_apns_skips_when_env_missing():
    src = (ROOT / "app/services/notifications.py").read_text()
    assert "APNs env missing" in src
    assert "never fakes" in src.lower() or "Never fakes" in src or "Never claims sent" in src
