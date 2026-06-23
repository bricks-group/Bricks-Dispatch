from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch
from app.shared.schemas import Job, VendorMember
from app.services.vendor_selection import pick_vendor_rule_based, assign_vendor_for_job


class _FakeQuery:
    """Stand-in for the Supabase async query builder chain.

    select/eq/maybe_single/update/insert all return self; only execute()
    is awaited, returning the canned data registered for this table.
    """

    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def maybe_single(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _FakeDB:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name))


def test_pick_vendor_rule_based_specialty_match():
    job = Job(address="123 Main St", job_type="video", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Photo Pro", email="v1@test.com", specialty="photo", is_active=True, status="available", onboarding_status="approved"),
        VendorMember(id="v2", name="Video Pro", email="v2@test.com", specialty="video", is_active=True, status="available", onboarding_status="approved"),
    ]

    chosen_id = pick_vendor_rule_based(job, vendors)
    assert chosen_id == "v2"

def test_pick_vendor_rule_based_load_balancing():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Busy Photo", email="v1@test.com", specialty="photo", is_active=True, status="available", active_assignments=5, onboarding_status="approved"),
        VendorMember(id="v2", name="Free Photo", email="v2@test.com", specialty="photo", is_active=True, status="available", active_assignments=0, onboarding_status="approved"),
    ]

    chosen_id = pick_vendor_rule_based(job, vendors)
    assert chosen_id == "v2"

def test_pick_vendor_rule_based_quality_rating():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Good Photo", email="v1@test.com", specialty="photo", is_active=True, status="available", quality_rating=4.5, onboarding_status="approved"),
        VendorMember(id="v2", name="Great Photo", email="v2@test.com", specialty="photo", is_active=True, status="available", quality_rating=4.9, onboarding_status="approved"),
    ]

    chosen_id = pick_vendor_rule_based(job, vendors)
    assert chosen_id == "v2"

def test_pick_vendor_onboarding_guard():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        # This vendor is active but still in vetting status
        VendorMember(id="v1", name="Active but Pending", email="v1@test.com", specialty="photo", is_active=True, status="available", onboarding_status="vetting"),
    ]

    chosen_id = pick_vendor_rule_based(job, vendors)
    # No approved vendors exist for this job, so there is no candidate to assign
    assert chosen_id is None

@pytest.mark.asyncio
async def test_assign_vendor_for_job_fallback(monkeypatch):
    fake_db = _FakeDB({
        "jobs": {"id": "job_123", "address": "123 Main St", "job_type": "photo", "vendor_id": "default_vendor", "time_window_start": "09:00"},
        "vendor_members": [
            {"id": "v1", "name": "Vendor 1", "email": "v1@test.com", "is_active": True, "status": "available", "vendor_id": "default_vendor", "specialty": "photo", "onboarding_status": "approved"}
        ],
        "ops_escalations": None,
    })

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=fake_db)):
        # AI returns no usable suggestion, so rule-based fallback should pick v1
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            chosen_id = await assign_vendor_for_job("job_123")
            assert chosen_id == "v1"

@pytest.mark.asyncio
async def test_assign_vendor_for_job_ai_success(monkeypatch):
    fake_db = _FakeDB({
        "jobs": {"id": "job_123", "address": "123 Main St", "job_type": "photo", "vendor_id": "default_vendor", "time_window_start": "09:00"},
        "vendor_members": [
            {"id": "v_ai", "name": "AI Vendor", "email": "ai@test.com", "is_active": True, "status": "available", "vendor_id": "default_vendor", "specialty": "photo", "onboarding_status": "approved", "tier": 1}
        ],
        "ops_escalations": None,
    })

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=fake_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value="v_ai")):
            chosen_id = await assign_vendor_for_job("job_123")
            assert chosen_id == "v_ai"
