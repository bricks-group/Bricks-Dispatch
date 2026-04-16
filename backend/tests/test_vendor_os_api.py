"""
Vendor OS API tests for Bricks Dispatch backend.
"""
import requests
from pathlib import Path


def get_base_url():
    env_file = Path('/app/frontend/.env')
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith('EXPO_PUBLIC_BACKEND_URL='):
                    return line.split('=', 1)[1].strip()
    return ''


BASE_URL = get_base_url()


def auth_headers():
    login_res = requests.post(
        f"{BASE_URL}/api/vendor-os/auth/login",
        json={"email": "alex.rivera@bricksdispatch.com", "name": "Alex Rivera"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_vendor_os_overview():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    res = requests.get(f"{BASE_URL}/api/vendor-os/overview", headers=headers)
    assert res.status_code == 200
    data = res.json()
    for key in ["jobs_assigned_today", "jobs_assigned_week", "jobs_completed_week", "capacity_utilization_pct", "upcoming_jobs"]:
        assert key in data


def test_vendor_os_team_crud():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    list_res = requests.get(f"{BASE_URL}/api/vendor-os/team", headers=headers)
    assert list_res.status_code == 200
    initial_count = list_res.json()["total"]

    create_payload = {
        "name": "Jamie Vendor",
        "email": "jamie@vendor.test",
        "phone": "+1 555 333 2222",
        "role": "Photographer",
    }
    create_res = requests.post(f"{BASE_URL}/api/vendor-os/team", headers=headers, json=create_payload)
    assert create_res.status_code == 200
    member = create_res.json()
    member_id = member["id"]

    update_res = requests.put(
        f"{BASE_URL}/api/vendor-os/team/{member_id}",
        headers=headers,
        json={"status": "busy"}
    )
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "busy"

    delete_res = requests.delete(f"{BASE_URL}/api/vendor-os/team/{member_id}", headers=headers)
    assert delete_res.status_code == 200

    final_res = requests.get(f"{BASE_URL}/api/vendor-os/team", headers=headers)
    assert final_res.json()["total"] == initial_count


def test_vendor_os_team_filter_and_pagination():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    requests.post(f"{BASE_URL}/api/vendor-os/team", headers=headers, json={"name": "Pat Busy", "email": "pat.busy@test.com", "phone": "", "role": "Editor"})
    requests.post(f"{BASE_URL}/api/vendor-os/team", headers=headers, json={"name": "Sam Busy", "email": "sam.busy@test.com", "phone": "", "role": "Photographer"})
    team = requests.get(f"{BASE_URL}/api/vendor-os/team?search=busy&page=1&page_size=1", headers=headers)
    assert team.status_code == 200
    payload = team.json()
    assert payload["total"] >= 2
    assert len(payload["items"]) == 1
    assert payload["total_pages"] >= 2


def test_vendor_os_job_assignment():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    member_res = requests.post(
        f"{BASE_URL}/api/vendor-os/team",
        headers=headers,
        json={"name": "Alex Team", "email": "alex.team@test.com", "phone": "", "role": "Operator"},
    )
    assert member_res.status_code == 200
    member_id = member_res.json()["id"]

    jobs_res = requests.get(f"{BASE_URL}/api/jobs", headers={"X-User-Id": "default_user", "Content-Type": "application/json"})
    assert jobs_res.status_code == 200
    job_id = jobs_res.json()[0]["id"]

    assign_res = requests.put(
        f"{BASE_URL}/api/vendor-os/jobs/{job_id}/assign",
        headers=headers,
        json={"member_id": member_id}
    )
    assert assign_res.status_code == 200
    assert assign_res.json()["assignee_id"] == member_id


def test_vendor_os_availability_settings():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    get_res = requests.get(f"{BASE_URL}/api/vendor-os/availability", headers=headers)
    assert get_res.status_code == 200

    payload = {
        "available": True,
        "work_start": "09:00",
        "work_end": "18:00",
        "jobs_per_day": 6,
        "blocked_windows": [{"start": "2026-04-15T12:00:00Z", "end": "2026-04-15T13:00:00Z"}]
    }
    put_res = requests.put(f"{BASE_URL}/api/vendor-os/availability", headers=headers, json=payload)
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["jobs_per_day"] == 6
    assert updated["work_start"] == "09:00"


def test_vendor_os_performance_and_settings():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    perf_res = requests.get(f"{BASE_URL}/api/vendor-os/performance", headers=headers)
    assert perf_res.status_code == 200
    perf = perf_res.json()
    for key in ["completion_rate", "on_time_rate", "job_volume_week"]:
        assert key in perf

    settings_get = requests.get(f"{BASE_URL}/api/vendor-os/settings", headers=headers)
    assert settings_get.status_code == 200

    settings_put = requests.put(
        f"{BASE_URL}/api/vendor-os/settings",
        headers=headers,
        json={
            "company_name": "Bricks Creative Ops",
            "service_areas": ["Los Angeles", "Pasadena"],
            "support_email": "ops@bricksdispatch.com",
        }
    )
    assert settings_put.status_code == 200
    updated = settings_put.json()
    assert updated["company_name"] == "Bricks Creative Ops"


def test_vendor_os_jobs_filter_and_pagination():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()
    jobs_res = requests.get(f"{BASE_URL}/api/vendor-os/jobs?status=assigned&page=1&page_size=2", headers=headers)
    assert jobs_res.status_code == 200
    payload = jobs_res.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 2
    assert payload["total"] >= len(payload["items"])
    assert payload["total_pages"] >= 1
    for item in payload["items"]:
        assert item["status"] == "assigned"


def test_vendor_os_auth_logout_and_me():
    requests.post(f"{BASE_URL}/api/seed")
    login_res = requests.post(
        f"{BASE_URL}/api/vendor-os/auth/login",
        json={"email": "alex.rivera@bricksdispatch.com", "name": "Alex Rivera"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me_res = requests.get(f"{BASE_URL}/api/vendor-os/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "alex.rivera@bricksdispatch.com"

    logout_res = requests.post(f"{BASE_URL}/api/vendor-os/auth/logout", headers=headers)
    assert logout_res.status_code == 200

    blocked_res = requests.get(f"{BASE_URL}/api/vendor-os/auth/me", headers=headers)
    assert blocked_res.status_code == 401


def test_vendor_os_audit_history_records_mutations():
    requests.post(f"{BASE_URL}/api/seed")
    headers = auth_headers()

    member_res = requests.post(
        f"{BASE_URL}/api/vendor-os/team",
        headers=headers,
        json={"name": "Audit User", "email": "audit.user@test.com", "phone": "", "role": "Operator"},
    )
    assert member_res.status_code == 200

    audit_res = requests.get(f"{BASE_URL}/api/vendor-os/audit-events?entity_type=vendor_member", headers=headers)
    assert audit_res.status_code == 200
    payload = audit_res.json()
    assert payload["total"] >= 1
    assert any(event["action"] == "create" for event in payload["items"])
