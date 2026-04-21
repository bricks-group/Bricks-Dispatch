import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.shared.schemas import Job, VendorMember
from app.services.vendor_selection import pick_vendor_rule_based, assign_vendor_for_job

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
    # Should fall back to default_user because no approved vendors exist
    assert chosen_id == "default_user"

@pytest.mark.asyncio
async def test_assign_vendor_for_job_fallback(monkeypatch):
    # Mock DB
    mock_db = MagicMock()
    
    async def mock_find_one(query, projection=None):
        if "id" in query and query["id"] == "job_123":
            return {"id": "job_123", "address": "123 Main St", "job_type": "photo", "vendor_id": "default_vendor", "time_window_start": "09:00"}
        return None

    class MockCursor:
        async def to_list(self, length):
            return [
                {"id": "v1", "name": "Vendor 1", "email": "v1@test.com", "is_active": True, "status": "available", "vendor_id": "default_vendor", "specialty": "photo", "onboarding_status": "approved"}
            ]

    def mock_find(query):
        return MockCursor()

    mock_db.jobs.find_one = AsyncMock(side_effect=mock_find_one)
    mock_db.jobs.update_one = AsyncMock()
    mock_db.vendor_members.find = mock_find
    
    with patch("app.services.vendor_selection.db", mock_db):
        # Disable AI
        monkeypatch.setenv("USE_AI_DISPATCH", "false")
        
        chosen_id = await assign_vendor_for_job("job_123")
        assert chosen_id == "v1"
        assert mock_db.jobs.update_one.called

@pytest.mark.asyncio
async def test_assign_vendor_for_job_ai_success(monkeypatch):
    # Mock DB
    mock_db = MagicMock()
    
    async def mock_find_one(query, projection=None):
        return {"id": "job_123", "address": "123 Main St", "job_type": "photo", "vendor_id": "default_vendor", "time_window_start": "09:00"}

    class MockCursor:
        async def to_list(self, length):
            return [
                {"id": "v_ai", "name": "AI Vendor", "email": "ai@test.com", "is_active": True, "status": "available", "vendor_id": "default_vendor", "specialty": "photo", "onboarding_status": "approved"}
            ]

    mock_db.jobs.find_one = AsyncMock(side_effect=mock_find_one)
    mock_db.jobs.update_one = AsyncMock()
    mock_db.vendor_members.find = lambda q: MockCursor()
    
    with patch("app.services.vendor_selection.db", mock_db):
        with patch("app.services.vendor_selection.bricks_smart_assign", return_value="v_ai"):
            monkeypatch.setenv("USE_AI_DISPATCH", "true")
            monkeypatch.setenv("GEMINI_API_KEY", "fake_key")
            
            chosen_id = await assign_vendor_for_job("job_123")
            assert chosen_id == "v_ai"
            assert mock_db.jobs.update_one.called
