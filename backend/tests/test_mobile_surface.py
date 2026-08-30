"""Routes Expo + Swift actually call, and Owner payout copy."""
from pathlib import Path

from app.shared.pagination import paginated_response
from app.shared.vendor_identity import is_owner_member, public_profile

ROOT = Path(__file__).resolve().parents[1]


def test_jobs_route_mounted():
    jobs = (ROOT / "app/routers/jobs.py").read_text()
    server = (ROOT / "server.py").read_text()
    main = (ROOT / "app/main.py").read_text()
    assert '@router.get("/jobs")' in jobs
    assert "jobs_router" in server
    assert "jobs.router" in main
    assert "/api/jobs" in server


def test_notification_and_push_token_routes_mounted():
    notif = (ROOT / "app/routers/notifications.py").read_text()
    vendor_os = (ROOT / "app/routers/vendor_os.py").read_text()
    server = (ROOT / "server.py").read_text()
    assert '@router.get("/notifications")' in notif
    assert '@router.get("/notifications/unread-count")' in notif
    assert '@router.post("/notifications/mark-all-read")' in notif
    assert '@router.post("/push-tokens")' in notif
    assert '@router.put("/notifications/{notification_id}/read")' in notif
    assert '@router.post("/push-token")' in vendor_os
    assert "device_token" in vendor_os
    assert "notifications_router" in server
    assert "/api/push-tokens" in server
    assert "pass" not in (ROOT / "app/main.py").read_text().split("include_router")[-1] or "push_token_register" not in (ROOT / "app/main.py").read_text()


def test_dead_placeholders_removed_from_main():
    main = (ROOT / "app/main.py").read_text()
    assert "Placeholder for actual login" not in main
    assert "push_token_register" not in main


def test_vendor_os_jobs_includes_jobs_alias():
    src = (ROOT / "app/routers/vendor_os.py").read_text()
    assert 'payload["jobs"] = jobs' in src


def test_paginated_plus_jobs_alias_shape():
    payload = paginated_response([{"id": "j1"}], 1, 1, 20)
    payload["jobs"] = payload["items"]
    assert payload["items"][0]["id"] == "j1"
    assert payload["jobs"][0]["id"] == "j1"


def test_kam_owner_full_package_zero_deduction():
    kam = {
        "id": "0c3fa7c2-d23b-4e44-90ad-ab2d19af2580",
        "role": "Owner",
        "title": "Owner",
        "payout_mode": "owner_full",
        "is_active": True,
        "onboarding_status": "approved",
    }
    assert is_owner_member(kam)
    pub = public_profile(kam)
    assert pub["title"] == "Owner"
    assert pub["payout_model"] == "owner_full"
    assert pub["deduction_cad"] == 0
    assert pub["show_platform_cut"] is False


def test_aman_pending_not_assignable():
    aman = {
        "id": "aman-pending",
        "role": "Photographer IC",
        "is_active": True,
        "onboarding_status": "pending",
    }
    pub = public_profile(aman)
    assert pub["assignable"] is False
    assert "Pending" in (pub["assignable_reason"] or "")
