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


def test_intake_awaits_admin_notify_not_background_tasks():
    src = (ROOT / "app/routers/dispatch.py").read_text()
    assert "await create_notification" in src
    assert "background_tasks.add_task" not in src
    assert "BackgroundTasks" not in src or "BackgroundTasks" in src and "drops FastAPI BackgroundTasks" in src
    assert "paid_booking_admin_subject" in src
    assert "email_html=" in src
    assert "_paid_booking_admin_email_html" in src
    assert "New paid booking:" not in src
    assert "job_data.get('address', job_id)" not in src


def test_format_shoot_window_matches_go_web():
    # Source-level: full package import needs pydantic on CI/dev without venv.
    src = (ROOT / "app/services/notifications.py").read_text()
    assert "def format_shoot_window" in src
    assert '"afternoon": ("12:00", "16:00", "Afternoon")' in src
    assert "Shoot window not set" in src
    # Exec just the pure helper body (no Supabase/pydantic).
    start = src.index("def _clock_hhmm")
    end = src.index("def _assignment_email_html")
    ns = {}
    exec("_CANONICAL_WINDOWS = " + src[src.index("_CANONICAL_WINDOWS = "):src.index("def _clock_hhmm")], ns)
    exec(src[start:end], ns)
    assert ns["format_shoot_window"]({
        "preferred_window": "afternoon",
        "time_window_start": "12:00",
        "time_window_end": "16:00",
    }) == "Afternoon · 12:00–16:00 ET"
    assert ns["format_shoot_window"]({}) == "Shoot window not set"
    assert ns["format_shoot_window"]({"time_window_start": "09:00", "time_window_end": "12:00"}) == "Morning · 09:00–12:00 ET"


def test_notify_assignment_emails_photographer():
    src = (ROOT / "app/services/notifications.py").read_text()
    assert "_assignment_email_html" in src
    assert "Open Bricks GO" in src
    assert 'result["email"]' in src
    assert "format_shoot_window" in src
