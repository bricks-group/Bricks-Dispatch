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
HEADERS = {"X-User-Id": "default_user", "Content-Type": "application/json"}


def test_vendor_os_overview():
    requests.post(f"{BASE_URL}/api/seed")
    res = requests.get(f"{BASE_URL}/api/vendor-os/overview", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    for key in ["jobs_assigned_today", "jobs_assigned_week", "jobs_completed_week", "capacity_utilization_pct", "upcoming_jobs"]:
        assert key in data


def test_vendor_os_team_crud():
    requests.post(f"{BASE_URL}/api/seed")
    list_res = requests.get(f"{BASE_URL}/api/vendor-os/team", headers=HEADERS)
    assert list_res.status_code == 200
    initial_count = len(list_res.json())

    create_payload = {
        "name": "Jamie Vendor",
        "email": "jamie@vendor.test",
        "phone": "+1 555 333 2222",
        "role": "Photographer",
    }
    create_res = requests.post(f"{BASE_URL}/api/vendor-os/team", headers=HEADERS, json=create_payload)
    assert create_res.status_code == 200
    member = create_res.json()
    member_id = member["id"]

    update_res = requests.put(
        f"{BASE_URL}/api/vendor-os/team/{member_id}",
        headers=HEADERS,
        json={"status": "busy"}
    )
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "busy"

    delete_res = requests.delete(f"{BASE_URL}/api/vendor-os/team/{member_id}", headers=HEADERS)
    assert delete_res.status_code == 200

    final_res = requests.get(f"{BASE_URL}/api/vendor-os/team", headers=HEADERS)
    assert len(final_res.json()) == initial_count


def test_vendor_os_job_assignment():
    requests.post(f"{BASE_URL}/api/seed")
    member_res = requests.post(
        f"{BASE_URL}/api/vendor-os/team",
        headers=HEADERS,
        json={"name": "Alex Team", "email": "alex.team@test.com", "phone": "", "role": "Operator"},
    )
    assert member_res.status_code == 200
    member_id = member_res.json()["id"]

    jobs_res = requests.get(f"{BASE_URL}/api/jobs", headers=HEADERS)
    assert jobs_res.status_code == 200
    job_id = jobs_res.json()[0]["id"]

    assign_res = requests.put(
        f"{BASE_URL}/api/vendor-os/jobs/{job_id}/assign",
        headers=HEADERS,
        json={"member_id": member_id}
    )
    assert assign_res.status_code == 200
    assert assign_res.json()["assignee_id"] == member_id


def test_vendor_os_availability_settings():
    requests.post(f"{BASE_URL}/api/seed")
    get_res = requests.get(f"{BASE_URL}/api/vendor-os/availability", headers=HEADERS)
    assert get_res.status_code == 200

    payload = {
        "available": True,
        "work_start": "09:00",
        "work_end": "18:00",
        "jobs_per_day": 6,
        "blocked_windows": [{"start": "2026-04-15T12:00:00Z", "end": "2026-04-15T13:00:00Z"}]
    }
    put_res = requests.put(f"{BASE_URL}/api/vendor-os/availability", headers=HEADERS, json=payload)
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["jobs_per_day"] == 6
    assert updated["work_start"] == "09:00"


def test_vendor_os_performance_and_settings():
    requests.post(f"{BASE_URL}/api/seed")
    perf_res = requests.get(f"{BASE_URL}/api/vendor-os/performance", headers=HEADERS)
    assert perf_res.status_code == 200
    perf = perf_res.json()
    for key in ["completion_rate", "on_time_rate", "job_volume_week"]:
        assert key in perf

    settings_get = requests.get(f"{BASE_URL}/api/vendor-os/settings", headers=HEADERS)
    assert settings_get.status_code == 200

    settings_put = requests.put(
        f"{BASE_URL}/api/vendor-os/settings",
        headers=HEADERS,
        json={
            "company_name": "Bricks Creative Ops",
            "service_areas": ["Los Angeles", "Pasadena"],
            "support_email": "ops@bricksdispatch.com",
        }
    )
    assert settings_put.status_code == 200
    updated = settings_put.json()
    assert updated["company_name"] == "Bricks Creative Ops"
