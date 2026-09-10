import pytest
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

if not BASE_URL.startswith(("http://", "https://")):
    pytest.skip(
        "No live server configured (BASE_URL not found via /app/frontend/.env) — "
        "skipping live-server integration tests",
        allow_module_level=True,
    )


def login(email: str = "alex.rivera@bricksdispatch.com"):
    res = requests.post(
        f"{BASE_URL}/api/vendor-os/auth/login",
        json={"email": email, "name": "Alex Rivera"},
    )
    assert res.status_code == 200
    return res.json()


def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_session_listing_refresh_and_revoke_flow():
    requests.post(f"{BASE_URL}/api/seed")
    session = login()
    token = session["token"]
    headers = auth_headers(token)

    sessions_res = requests.get(f"{BASE_URL}/api/vendor-os/auth/sessions", headers=headers)
    assert sessions_res.status_code == 200
    sessions_payload = sessions_res.json()
    assert sessions_payload["total"] >= 1
    refresh_res = requests.post(f"{BASE_URL}/api/vendor-os/auth/refresh", headers=headers)
    assert refresh_res.status_code == 200
    refreshed_token = refresh_res.json()["token"]

    old_me = requests.get(f"{BASE_URL}/api/vendor-os/auth/me", headers=headers)
    assert old_me.status_code == 401

    refreshed_headers = auth_headers(refreshed_token)
    new_me = requests.get(f"{BASE_URL}/api/vendor-os/auth/me", headers=refreshed_headers)
    assert new_me.status_code == 200

    refreshed_sessions = requests.get(f"{BASE_URL}/api/vendor-os/auth/sessions", headers=refreshed_headers)
    assert refreshed_sessions.status_code == 200
    active_session = next(item for item in refreshed_sessions.json()["items"] if item["active"])
    session_id = active_session["id"]

    revoke_res = requests.post(f"{BASE_URL}/api/vendor-os/auth/revoke/{session_id}", headers=refreshed_headers)
    assert revoke_res.status_code == 200
    assert revoke_res.json()["revoked"] is True


def test_revoke_others_and_audit_events():
    requests.post(f"{BASE_URL}/api/seed")
    first = login()
    second = login()
    first_headers = auth_headers(first["token"])
    second_headers = auth_headers(second["token"])

    revoke_others_res = requests.post(f"{BASE_URL}/api/vendor-os/auth/revoke-others", headers=first_headers)
    assert revoke_others_res.status_code == 200
    assert revoke_others_res.json()["revoked_count"] >= 1

    blocked_res = requests.get(f"{BASE_URL}/api/vendor-os/auth/me", headers=second_headers)
    assert blocked_res.status_code == 401

    audit_res = requests.get(f"{BASE_URL}/api/vendor-os/audit-events?entity_type=session", headers=first_headers)
    assert audit_res.status_code == 200
    assert audit_res.json()["total"] >= 1

