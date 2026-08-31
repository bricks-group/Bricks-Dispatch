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
    # No match → unassigned (never default_user, never pending applicants)
    assert chosen_id is None

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


from app.services.vendor_profile import KAM_MEMBER_ID
from app.shared.schemas import Job as _Job


def _approved(**kwargs):
    base = dict(is_active=True, status="available", onboarding_status="approved", specialty="photo", tier=1)
    base.update(kwargs)
    return VendorMember(**base)


def test_aman_pending_never_assigned():
    job = Job(address="10 King St Mississauga", city="Mississauga", job_type="photo", time_window_start="09:00")
    vendors = [
        _approved(id="aman", name="Aman Sammy", email="aman@test.com", onboarding_status="pending", tier=3),
        _approved(id=KAM_MEMBER_ID, name="Ajani Kamali Akio", email="akashorter@icloud.com", role="Owner", tier=3,
                  service_areas=["Mississauga", "GTA"], skills=["photo", "drone", "reel", "tour", "twilight"],
                  rpas_certificate_level="advanced", drone_certified=True),
    ]
    assert pick_vendor_rule_based(job, vendors) == KAM_MEMBER_ID


def test_drone_requires_advanced_rpas():
    job = Job(address="10 King St Mississauga", city="Mississauga", job_type="drone", time_window_start="09:00",
              add_ons=[{"key": "drone", "included": True}])
    basic = _approved(id="basic", name="Basic Pilot", email="b@test.com", drone_certified=True,
                      rpas_certificate_level="basic", specialty="drone", skills=["photo", "drone"],
                      service_areas=["GTA"])
    none = _approved(id="none", name="No Ticket", email="n@test.com", specialty="photo", skills=["photo"],
                     service_areas=["GTA"])
    assert pick_vendor_rule_based(job, [basic, none]) is None
    kam = _approved(id=KAM_MEMBER_ID, name="Kam", email="akashorter@icloud.com", role="Owner", tier=3,
                    skills=["photo", "drone"], rpas_advanced=True, rpas_certificate_level="advanced",
                    drone_certified=True, service_areas=["Mississauga", "GTA"])
    assert pick_vendor_rule_based(job, [basic, none, kam]) == KAM_MEMBER_ID


def test_owner_wins_matching_premium():
    job = Job(address="88 Harbour St Toronto", city="Toronto", job_type="photo", time_window_start="09:00",
              package_id="luxury", package_name="Luxury")
    ic = _approved(id="ic1", name="IC Photog", email="ic@test.com", tier=3, skills=["photo"], service_areas=["GTA"])
    kam = _approved(id=KAM_MEMBER_ID, name="Ajani Kamali Akio", email="akashorter@icloud.com", role="Owner",
                    tier=3, skills=["photo", "drone", "reel"], service_areas=["Mississauga", "GTA"])
    assert pick_vendor_rule_based(job, [ic, kam]) == KAM_MEMBER_ID


def test_no_match_unassigned():
    job = Job(address="Calgary AB", city="Calgary", job_type="drone", time_window_start="09:00",
              add_ons=[{"key": "drone"}])
    gta_ic = _approved(id="ic1", name="GTA only", email="g@test.com", tier=1, skills=["photo"],
                       service_areas=["Mississauga", "GTA"], home_city="Mississauga")
    assert pick_vendor_rule_based(job, [gta_ic]) is None


def test_lapsed_coi_blocks_assignment():
    job = Job(address="10 King St Mississauga", city="Mississauga", job_type="photo", time_window_start="09:00")
    lapsed = _approved(id="lapsed", name="Lapsed COI", email="lapsed@test.com", tier=2,
                        skills=["photo"], service_areas=["GTA"], coi_expires_on="2020-01-01")
    current = _approved(id="current", name="Current COI", email="current@test.com", tier=1,
                         skills=["photo"], service_areas=["GTA"], coi_expires_on="2099-01-01")
    assert pick_vendor_rule_based(job, [lapsed]) is None
    assert pick_vendor_rule_based(job, [lapsed, current]) == "current"


def test_null_insurance_expiry_does_not_block():
    # Most active vendors predate this field entirely -- NULL must fail open,
    # or the whole existing roster would stop being dispatchable overnight.
    job = Job(address="10 King St Mississauga", city="Mississauga", job_type="photo", time_window_start="09:00")
    no_date_on_file = _approved(id="nodate", name="No Date On File", email="nd@test.com", tier=1,
                                 skills=["photo"], service_areas=["GTA"])
    assert pick_vendor_rule_based(job, [no_date_on_file]) == "nodate"
