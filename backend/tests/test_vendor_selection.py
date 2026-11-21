import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.shared.schemas import Job, VendorMember
from app.services.vendor_selection import pick_vendor_rule_based, assign_vendor_for_job

def test_pick_vendor_rule_based_specialty_match():
    job = Job(address="123 Main St", job_type="video", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Photo Pro", email="v1@test.com", specialty="photo", is_active=True, status="available", onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
        VendorMember(id="v2", name="Video Pro", email="v2@test.com", specialty="video", is_active=True, status="available", onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
    ]
    
    chosen_id = pick_vendor_rule_based(job, vendors)
    assert chosen_id == "v2"

def test_pick_vendor_rule_based_load_balancing():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Busy Photo", email="v1@test.com", specialty="photo", is_active=True, status="available", active_assignments=5, onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
        VendorMember(id="v2", name="Free Photo", email="v2@test.com", specialty="photo", is_active=True, status="available", active_assignments=0, onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
    ]
    
    chosen_id = pick_vendor_rule_based(job, vendors)
    assert chosen_id == "v2"

def test_pick_vendor_rule_based_quality_rating():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        VendorMember(id="v1", name="Good Photo", email="v1@test.com", specialty="photo", is_active=True, status="available", quality_rating=4.5, onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
        VendorMember(id="v2", name="Great Photo", email="v2@test.com", specialty="photo", is_active=True, status="available", quality_rating=4.9, onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}),
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

def _supabase_style_db(job_doc, members):
    """Minimal async chain mock for get_db().table(...).select(...).eq(...).execute()."""
    mock_db = MagicMock()

    class Result:
        def __init__(self, data):
            self.data = data

    def table(name):
        chain = MagicMock()

        async def execute():
            if name == "jobs":
                # Distinguish select-one vs update vs select occupied
                return Result(job_doc if isinstance(job_doc, dict) else job_doc)
            if name == "vendor_members":
                return Result(members)
            if name == "ops_escalations":
                return Result([])
            return Result([])

        # Make every chained method return the same chain; terminal execute is async
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.maybe_single.return_value = chain
        chain.update.return_value = chain
        chain.insert.return_value = chain
        chain.limit.return_value = chain
        chain.execute = execute

        # For jobs.maybe_single path — return job; for list path return members
        async def execute_jobs():
            # Heuristic: if maybe_single was used in this chain, return single job
            return Result(job_doc)

        async def execute_members():
            return Result(members)

        if name == "jobs":
            chain.execute = execute_jobs
        elif name == "vendor_members":
            chain.execute = execute_members
        return chain

    mock_db.table.side_effect = table
    return mock_db


@pytest.mark.asyncio
async def test_assign_vendor_for_job_fallback(monkeypatch):
    job_doc = {
        "id": "job_123",
        "address": "123 Main St",
        "city": "Mississauga",
        "job_type": "photo",
        "vendor_id": "default_vendor",
        "time_window_start": "09:00",
        "scheduled_date": "2026-09-10",
        "status": "pending_assignment",
    }
    members = [
        {
            "id": "v1",
            "name": "Vendor 1",
            "email": "v1@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00", "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 1,
        }
    ]
    mock_db = _supabase_style_db(job_doc, members)
    monkeypatch.setenv("USE_AI_DISPATCH", "false")

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            with patch("app.services.vendor_selection.load_occupied_jobs_by_vendor", AsyncMock(return_value={})):
                with patch("app.services.vendor_selection._update_job_assignee", AsyncMock()) as upd:
                    chosen_id = await assign_vendor_for_job("job_123")
                    assert chosen_id == "v1"
                    assert upd.called


@pytest.mark.asyncio
async def test_assign_vendor_for_job_ai_success(monkeypatch):
    job_doc = {
        "id": "job_123",
        "address": "123 Main St",
        "city": "Mississauga",
        "job_type": "photo",
        "vendor_id": "default_vendor",
        "time_window_start": "09:00",
        "scheduled_date": "2026-09-10",
        "status": "pending_assignment",
    }
    members = [
        {
            "id": "v_ai",
            "name": "AI Vendor",
            "email": "ai@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00", "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 1,
        }
    ]
    mock_db = _supabase_style_db(job_doc, members)
    monkeypatch.setenv("USE_AI_DISPATCH", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_key")

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value="v_ai")):
            with patch("app.services.vendor_selection.load_occupied_jobs_by_vendor", AsyncMock(return_value={})):
                with patch("app.services.vendor_selection._update_job_assignee", AsyncMock()) as upd:
                    chosen_id = await assign_vendor_for_job("job_123")
                    assert chosen_id == "v_ai"
                    assert upd.called


from app.services.vendor_profile import KAM_MEMBER_ID
from app.shared.schemas import Job as _Job


def _approved(**kwargs):
    base = dict(is_active=True, status="available", onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}, specialty="photo", tier=1)
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


def test_missing_expired_insurance_does_not_block_assignment():
    """No COI and no WSIB, or an expired date, must still be assignable."""
    from app.services.vendor_profile import member_eligible_for_job

    job = Job(address="10 King St Mississauga", city="Mississauga", job_type="photo", time_window_start="09:00")
    no_docs = _approved(id="nodocs", name="No Insurance Docs", email="none@test.com", tier=1,
                        skills=["photo"], service_areas=["GTA"])
    lapsed_coi = _approved(id="lapsed-coi", name="Lapsed COI", email="lapsed@test.com", tier=2,
                           skills=["photo"], service_areas=["GTA"], coi_expires_on="2020-01-01")
    lapsed_wsib = _approved(id="lapsed-wsib", name="Lapsed WSIB", email="wsib@test.com", tier=1,
                            skills=["photo"], service_areas=["GTA"], wsib_expires_on="2020-06-01")
    both_expired = _approved(id="both", name="Both Expired", email="both@test.com", tier=1,
                             skills=["photo"], service_areas=["GTA"],
                             coi_expires_on="2019-01-01", wsib_expires_on="2019-06-01")
    current = _approved(id="current", name="Current COI", email="current@test.com", tier=1,
                        skills=["photo"], service_areas=["GTA"], coi_expires_on="2099-01-01")

    for member in (no_docs, lapsed_coi, lapsed_wsib, both_expired, current):
        assert member_eligible_for_job(member, job) is True

    assert pick_vendor_rule_based(job, [no_docs]) == "nodocs"
    assert pick_vendor_rule_based(job, [lapsed_coi]) == "lapsed-coi"
    assert pick_vendor_rule_based(job, [lapsed_wsib]) == "lapsed-wsib"
    assert pick_vendor_rule_based(job, [both_expired]) == "both"
    # Expired coverage must not knock a higher tier out of the pool.
    assert pick_vendor_rule_based(job, [lapsed_coi, current]) == "lapsed-coi"


# ── Auto-assign strengthen: duration, overlap, travel hard-gate, Canada ──────

from app.services.shoot_duration import (
    estimate_shoot_duration_minutes,
    vendor_slot_conflicts,
    previous_job_coords,
)
from app.services.vendor_profile import location_matches, travel_within_cap
from app.services.visit_checklist import seed_visit_checklist, ensure_visit_checklist
from app.services.vendor_selection import filter_assignable_vendors, pick_vendor_rule_based
import asyncio
from unittest.mock import AsyncMock, patch


def test_estimate_shoot_duration_photo_sqft_and_addons():
    base = Job(address="1 Main", job_type="photo", time_window_start="10:00", sqft=2000)
    assert estimate_shoot_duration_minutes(base) == 60
    small = Job(address="1 Main", job_type="photo", time_window_start="10:00", sqft=1200)
    assert estimate_shoot_duration_minutes(small) == 45
    full = Job(
        address="1 Main",
        job_type="photo",
        time_window_start="10:00",
        sqft=3000,
        package_name="Premium",
        add_ons=[{"key": "drone"}, {"key": "reel"}, {"key": "tour"}, {"key": "twilight"}],
    )
    mins = estimate_shoot_duration_minutes(full)
    assert mins >= 60 + 25 + 30 + 35 + 35
    assert mins <= 300


def test_vendor_slot_conflicts_overlap():
    job = Job(
        id="a",
        address="1 Main",
        job_type="photo",
        scheduled_date="2026-09-10",
        time_window_start="10:00",
        time_window_end="11:00",
        estimated_duration_min=60,
    )
    occupied = [
        {
            "id": "b",
            "scheduled_date": "2026-09-10",
            "time_window_start": "10:30",
            "time_window_end": "11:30",
            "status": "assigned",
            "estimated_duration_min": 60,
        }
    ]
    assert vendor_slot_conflicts(job, occupied) is True
    free = [
        {
            "id": "c",
            "scheduled_date": "2026-09-10",
            "time_window_start": "13:00",
            "time_window_end": "14:00",
            "status": "assigned",
            "estimated_duration_min": 60,
        }
    ]
    assert vendor_slot_conflicts(job, free) is False


def test_filter_assignable_excludes_slot_conflict():
    job = Job(
        id="new",
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="photo",
        scheduled_date="2026-09-10",
        time_window_start="10:00",
        time_window_end="11:00",
    )
    v = _approved(id="v1", name="V", email="v@test.com", skills=["photo"], service_areas=["GTA"])
    occupied = {
        "v1": [
            {
                "id": "old",
                "scheduled_date": "2026-09-10",
                "time_window_start": "09:30",
                "time_window_end": "11:00",
                "status": "assigned",
            }
        ]
    }
    assert filter_assignable_vendors(job, [v], occupied_by_vendor=occupied) == []
    assert pick_vendor_rule_based(job, [v], occupied_by_vendor=occupied) is None


def test_travel_hard_gate_over_cap_even_empty_areas():
    # Coords known + over cap → miss even with empty service_areas (fail-closed on distance)
    member = _approved(
        id="far",
        name="Far",
        email="f@test.com",
        skills=["photo"],
        service_areas=[],
        home_lat=43.65,
        home_lng=-79.38,
        max_travel_km=30,
    )
    job = Job(
        address="Calgary downtown",
        city="Calgary",
        state="AB",
        job_type="photo",
        time_window_start="10:00",
        lat=51.0447,
        lng=-114.0719,
    )
    assert travel_within_cap(member, job) is False
    assert location_matches(member, job) is False


def test_calgary_alberta_without_gta():
    member = _approved(
        id="ab1",
        name="Calgary Photog",
        email="c@test.com",
        skills=["photo"],
        service_areas=["Calgary", "Alberta"],
        home_city="Calgary",
    )
    job = Job(
        address="12 8 Ave SW",
        city="Calgary",
        state="AB",
        job_type="photo",
        time_window_start="10:00",
    )
    assert location_matches(member, job) is True
    assert pick_vendor_rule_based(job, [member]) == "ab1"

    gta_only = _approved(
        id="gta",
        name="GTA",
        email="g@test.com",
        skills=["photo"],
        service_areas=["Mississauga", "GTA"],
        home_city="Mississauga",
    )
    assert location_matches(gta_only, job) is False
    assert pick_vendor_rule_based(job, [gta_only]) is None


def test_placeholder_emdash_address_ontario_gta_vendor():
    """Prod smoke: address='—', city empty, state=ON, no lat/lng must still assign GTA vendor."""
    gta = _approved(
        id="0c3fa7c2-d23b-4e44-90ad-ab2d19af2580",
        name="Ajani Kamali Akio",
        email="akashorter@icloud.com",
        role="Owner",
        tier=3,
        skills=["photo", "drone", "reel", "tour", "twilight"],
        service_areas=["Mississauga", "GTA", "Toronto", "Peel", "Halton", "York", "Durham"],
        home_city="Mississauga",
        home_lat=43.5890,
        home_lng=-79.6441,
        max_travel_km=80,
    )
    job = Job(
        address="—",
        city="",
        state="ON",
        job_type="photo",
        package_name="Essentials",
        time_window_start="10:00",
        time_window_end="11:00",
        lat=None,
        lng=None,
    )
    assert location_matches(gta, job) is True
    assert pick_vendor_rule_based(job, [gta]) == gta.id


def test_hamilton_on_without_lat_gta_vendor():
    """Golden Horseshoe city outside classic GTA token list still matches ON + GTA spine."""
    gta = _approved(
        id="gta_ham",
        name="GTA Photog",
        email="gta@test.com",
        skills=["photo"],
        service_areas=["Mississauga", "GTA"],
        home_city="Mississauga",
    )
    job = Job(
        address="100 King St W",
        city="Hamilton",
        state="ON",
        job_type="photo",
        time_window_start="10:00",
        lat=None,
        lng=None,
    )
    assert location_matches(gta, job) is True
    assert pick_vendor_rule_based(job, [gta]) == "gta_ham"


def test_calgary_ab_still_rejects_gta_only_after_ontario_failopen():
    """AB jobs must not be rescued by the Ontario/GTA province gate."""
    gta_only = _approved(
        id="gta",
        name="GTA",
        email="g@test.com",
        skills=["photo"],
        service_areas=["Mississauga", "GTA"],
        home_city="Mississauga",
    )
    job = Job(
        address="12 8 Ave SW",
        city="Calgary",
        state="AB",
        job_type="photo",
        time_window_start="10:00",
    )
    assert location_matches(gta_only, job) is False
    assert pick_vendor_rule_based(job, [gta_only]) is None


def test_visit_checklist_one_visit_no_return():
    job = {
        "job_type": "photo",
        "package_id": "premium",
        "package_name": "Premium",
        "add_ons": [{"key": "drone"}, {"key": "twilight"}, {"key": "reel"}],
    }
    items = seed_visit_checklist(job)
    labels = " | ".join(i["label"].lower() for i in items)
    assert "arrive" in labels
    assert "hdr" in labels
    assert "drone" in labels
    assert "twilight" in labels and "same visit" in labels
    assert "return visit" not in labels
    assert "second appointment" not in labels
    assert "submit" in labels or "deliver" in labels
    # ensure reseeds legacy return wording
    legacy = [{"id": "1", "label": "Twilight return visit tomorrow", "completed": False}]
    fixed = ensure_visit_checklist({**job, "checklist": legacy})
    assert all("return visit" not in i["label"].lower() for i in fixed)


@pytest.mark.asyncio
async def test_vendor_is_busy_freebusy_mock():
    from datetime import datetime, timezone
    from app.services.vendor_calendars import vendor_is_busy, _google_freebusy

    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)

    class FakeResp:
        def __init__(self, status, data):
            self.status_code = status
            self._data = data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http")
        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            busy = [{"start": "2026-09-10T14:00:00Z", "end": "2026-09-10T14:30:00Z"}]
            return FakeResp(200, {"calendars": {"primary": {"busy": busy}}})

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        assert await _google_freebusy("tok", start, end) is True

    class FreeClient(FakeClient):
        async def post(self, url, headers=None, json=None):
            return FakeResp(200, {"calendars": {"primary": {"busy": []}}})

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FreeClient):
        assert await _google_freebusy("tok", start, end) is False

    member = {"id": "m1", "email": "m@test.com", "google_calendar_token": "tok"}
    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        assert await vendor_is_busy(member, start, end) is True
    with patch("app.services.vendor_calendars.httpx.AsyncClient", FreeClient):
        assert await vendor_is_busy(member, start, end) is False
    # No calendar → unknown
    assert await vendor_is_busy({"id": "m2"}, start, end) is None


def test_previous_job_coords_same_day():
    job = Job(
        id="n",
        address="B",
        job_type="photo",
        scheduled_date="2026-09-10",
        time_window_start="14:00",
        time_window_end="15:00",
        lat=43.7,
        lng=-79.4,
    )
    occupied = [
        {
            "id": "earlier",
            "scheduled_date": "2026-09-10",
            "time_window_start": "10:00",
            "time_window_end": "11:00",
            "status": "assigned",
            "lat": 43.65,
            "lng": -79.38,
        }
    ]
    coords = previous_job_coords(job, occupied)
    assert coords is not None
    assert abs(coords[0] - 43.65) < 0.01


def test_slot_bounds_property_local_to_utc():
    """Vancouver 2pm PDT must FreeBusy as 21:00Z, not 14:00Z."""
    from datetime import timezone
    from app.services.shoot_duration import _slot_bounds, selected_slot_iso_pair

    van = {
        "address": "200 Granville St, Vancouver, BC",
        "state": "BC",
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "15:00",
        "sqft": 2000,
    }
    bounds = _slot_bounds(van)
    assert bounds is not None
    start, end = bounds
    assert start.tzinfo is not None
    assert start.astimezone(timezone.utc).hour == 21
    assert end.astimezone(timezone.utc).hour == 22
    pair = selected_slot_iso_pair(van)
    assert pair == ("2026-09-25T21:00:00Z", "2026-09-25T22:00:00Z")

    tor = {
        "address": "10 King St W, Toronto, ON",
        "state": "ON",
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "15:00",
        "sqft": 2000,
    }
    t0, _ = _slot_bounds(tor)
    assert t0.astimezone(timezone.utc).hour == 18  # EDT


def test_slot_bounds_cross_zone_conflict():
    """Same wall clock in different zones is not the same absolute block."""
    from app.services.shoot_duration import vendor_slot_conflicts

    van = {
        "id": "a",
        "address": "Vancouver BC",
        "state": "BC",
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "15:00",
    }
    # Toronto 14:00–15:00 EDT is earlier in absolute time than Vancouver 14:00 PDT.
    occupied = [{
        "id": "b",
        "address": "Toronto ON",
        "state": "ON",
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "15:00",
    }]
    assert vendor_slot_conflicts(van, occupied) is False



# ── FreeBusy token refresh + assign busy skip ────────────────────────────────


@pytest.mark.asyncio
async def test_token_refresh_on_expired_freebusy(monkeypatch):
    """Expired Google access_token must refresh before FreeBusy is trusted."""
    from datetime import datetime, timezone, timedelta
    from app.services import vendor_calendars as vc

    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")

    calls = {"freebusy": 0, "refresh": 0}

    class FakeResp:
        def __init__(self, status, data):
            self.status_code = status
            self._data = data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http")
        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None, data=None):
            if "oauth2.googleapis.com/token" in str(url):
                calls["refresh"] += 1
                return FakeResp(200, {"access_token": "fresh", "expires_in": 3600})
            calls["freebusy"] += 1
            # After refresh, token is "fresh"
            auth = (headers or {}).get("Authorization") or ""
            assert "fresh" in auth
            return FakeResp(200, {"calendars": {"primary": {"busy": [{"start": "2026-09-10T14:00:00Z", "end": "2026-09-10T14:30:00Z"}]}}})

    member = {
        "id": "m1",
        "email": "m@test.com",
        "google_calendar_token": "stale",
        "refresh_token": "rtok",
        "token_expires_at": expired,
    }
    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        busy = await vc.vendor_is_busy(member, start, end)
    assert busy is True
    assert calls["refresh"] == 1
    assert calls["freebusy"] == 1


@pytest.mark.asyncio
async def test_assign_skips_calendar_busy_vendor(monkeypatch):
    """Busy FreeBusy vendor is excluded; free vendor wins."""
    job_doc = {
        "id": "job_busy",
        "address": "10 King St Mississauga",
        "city": "Mississauga",
        "job_type": "photo",
        "vendor_id": "default_vendor",
        "time_window_start": "10:00",
        "time_window_end": "11:00",
        "scheduled_date": "2026-09-10",
        "status": "pending_assignment",
        "lat": 43.59,
        "lng": -79.64,
    }
    members = [
        {
            "id": "busy_v",
            "name": "Busy",
            "email": "busy@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00", "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 2,
            "home_lat": 43.59,
            "home_lng": -79.64,
        },
        {
            "id": "free_v",
            "name": "Free",
            "email": "free@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00", "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 1,
            "home_lat": 43.59,
            "home_lng": -79.64,
        },
    ]
    mock_db = _supabase_style_db(job_doc, members)
    monkeypatch.setenv("USE_AI_DISPATCH", "false")

    async def fake_busy(member, start, end, db=None):
        mid = member.id if hasattr(member, "id") else (member or {}).get("id")
        return True if mid == "busy_v" else False

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            with patch("app.services.vendor_selection.load_occupied_jobs_by_vendor", AsyncMock(return_value={})):
                with patch("app.services.vendor_calendars.vendor_is_busy", AsyncMock(side_effect=fake_busy)):
                    with patch("app.services.vendor_selection._update_job_assignee", AsyncMock()) as upd:
                        chosen = await assign_vendor_for_job("job_busy")
                        assert chosen == "free_v"
                        assert upd.called


@pytest.mark.asyncio
async def test_assign_disconnected_vendor_uses_overlap_rules(monkeypatch):
    """No calendar → FreeBusy unknown; Bricks job-overlap still excludes a conflicted vendor."""
    job_doc = {
        "id": "job_disc",
        "address": "10 King St Mississauga",
        "city": "Mississauga",
        "state": "ON",
        "job_type": "photo",
        "vendor_id": "default_vendor",
        "time_window_start": "10:00",
        "time_window_end": "11:00",
        "scheduled_date": "2026-09-10",
        "status": "pending_assignment",
        "lat": 43.59,
        "lng": -79.64,
    }
    members = [
        {
            "id": "overlap_v",
            "name": "Overlap",
            "email": "overlap@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved",
            "onboarding_completed_at": "2026-10-01T00:00:00+00:00",
            "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 2,
            "home_lat": 43.59,
            "home_lng": -79.64,
        },
        {
            "id": "open_v",
            "name": "Open",
            "email": "open@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved",
            "onboarding_completed_at": "2026-10-01T00:00:00+00:00",
            "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 1,
            "home_lat": 43.59,
            "home_lng": -79.64,
        },
    ]
    occupied = {
        "overlap_v": [
            {
                "id": "other",
                "scheduled_date": "2026-09-10",
                "time_window_start": "10:00",
                "time_window_end": "11:00",
                "status": "assigned",
                "address": "10 King St Mississauga",
                "city": "Mississauga",
                "state": "ON",
            }
        ],
        "open_v": [],
    }
    mock_db = _supabase_style_db(job_doc, members)
    monkeypatch.setenv("USE_AI_DISPATCH", "false")

    async def fake_busy(_member, _start, _end, db=None):
        return None  # disconnected / unknown

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            with patch(
                "app.services.vendor_selection.load_occupied_jobs_by_vendor",
                AsyncMock(return_value=occupied),
            ):
                with patch("app.services.vendor_calendars.vendor_is_busy", AsyncMock(side_effect=fake_busy)):
                    with patch("app.services.vendor_selection._update_job_assignee", AsyncMock()) as upd:
                        chosen = await assign_vendor_for_job("job_disc")
                        assert chosen == "open_v"
                        assert upd.called


@pytest.mark.asyncio
async def test_assign_calendar_api_error_fail_soft(monkeypatch):
    """FreeBusy unknown (API error) must not exclude the vendor — prefer not to strand jobs."""
    job_doc = {
        "id": "job_soft",
        "address": "10 King St Mississauga",
        "city": "Mississauga",
        "job_type": "photo",
        "vendor_id": "default_vendor",
        "time_window_start": "10:00",
        "time_window_end": "11:00",
        "scheduled_date": "2026-09-10",
        "status": "pending_assignment",
        "lat": 43.59,
        "lng": -79.64,
    }
    members = [
        {
            "id": "only_v",
            "name": "Only",
            "email": "only@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved",
            "onboarding_completed_at": "2026-10-01T00:00:00+00:00",
            "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "tier": 1,
            "home_lat": 43.59,
            "home_lng": -79.64,
        },
    ]
    mock_db = _supabase_style_db(job_doc, members)
    monkeypatch.setenv("USE_AI_DISPATCH", "false")

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            with patch("app.services.vendor_selection.load_occupied_jobs_by_vendor", AsyncMock(return_value={})):
                with patch("app.services.vendor_calendars.vendor_is_busy", AsyncMock(return_value=None)):
                    with patch("app.services.vendor_selection._update_job_assignee", AsyncMock()) as upd:
                        chosen = await assign_vendor_for_job("job_soft")
                        assert chosen == "only_v"
                        assert upd.called


@pytest.mark.asyncio
async def test_google_freebusy_http_error_is_unknown():
    """Calendar API errors are unknown (None), not busy — fail-soft."""
    from datetime import datetime, timezone
    from app.services.vendor_calendars import vendor_is_busy, _google_freebusy

    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)

    class FakeResp:
        status_code = 500
        def raise_for_status(self):
            raise RuntimeError("http 500")
        def json(self):
            return {"error": "boom"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            return FakeResp()

    with patch("app.services.vendor_calendars.httpx.AsyncClient", FakeClient):
        assert await _google_freebusy("tok", start, end) is None
        member = {"id": "m1", "email": "m@test.com", "google_calendar_token": "tok"}
        assert await vendor_is_busy(member, start, end) is None


def test_sqft_parsed_from_property_details():
    from app.services.shoot_duration import estimate_shoot_duration_minutes
    job = {
        "job_type": "photo",
        "time_window_start": "10:00",
        "property_details": "Detached, 3,200 sq ft, 4 bed",
    }
    assert estimate_shoot_duration_minutes(job) == 75  # 2501-4000 band


def test_manual_availability_toggle_and_hours():
    from app.services.vendor_availability_gate import manual_availability_allows, work_hours_cover_slot

    job = Job(
        id="j1",
        address="100 Queen St W, Mississauga, ON",
        job_type="photo",
        scheduled_date="2026-09-10",
        time_window_start="10:00",
        time_window_end="11:00",
        city="Mississauga",
        state="ON",
    )
    assert manual_availability_allows(None, job) is True
    assert manual_availability_allows({"available": False, "work_start": "08:00", "work_end": "17:00"}, job) is False
    assert manual_availability_allows(
        {"available": True, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 4},
        job,
        has_calendar=False,
    ) is True
    assert manual_availability_allows(
        {"available": True, "work_start": "08:00", "work_end": "09:30", "jobs_per_day": 4},
        job,
        has_calendar=False,
    ) is False
    assert manual_availability_allows(
        {"available": True, "work_start": "08:00", "work_end": "09:30", "jobs_per_day": 4},
        job,
        has_calendar=True,
    ) is True
    assert work_hours_cover_slot("08:00", "17:00", job) is True
    assert work_hours_cover_slot("12:00", "17:00", job) is False


def test_manual_availability_capacity_and_blocks():
    from app.services.vendor_availability_gate import manual_availability_allows

    job = Job(
        id="j2",
        address="100 Queen St W, Mississauga, ON",
        job_type="photo",
        scheduled_date="2026-09-10",
        time_window_start="10:00",
        time_window_end="11:00",
        city="Mississauga",
        state="ON",
    )
    occupied = [
        {"id": "a", "scheduled_date": "2026-09-10", "status": "assigned"},
        {"id": "b", "scheduled_date": "2026-09-10", "status": "assigned"},
    ]
    assert manual_availability_allows(
        {"available": True, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 2},
        job,
        occupied=occupied,
        has_calendar=True,
    ) is False
    assert manual_availability_allows(
        {"available": True, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 3},
        job,
        occupied=occupied,
        has_calendar=True,
    ) is True
    assert manual_availability_allows(
        {
            "available": True,
            "work_start": "08:00",
            "work_end": "17:00",
            "jobs_per_day": 4,
            "blocked_windows": [{"start": "2026-09-10T13:30:00Z", "end": "2026-09-10T15:00:00Z"}],
        },
        job,
        has_calendar=True,
        duration_min=60,
    ) is False


def test_pick_vendor_respects_manual_unavailable():
    job = Job(
        address="100 Queen St W, Mississauga, ON",
        job_type="photo",
        time_window_start="10:00",
        time_window_end="11:00",
        scheduled_date="2026-09-10",
        city="Mississauga",
        state="ON",
    )
    vendors = [
        VendorMember(
            id="v1",
            name="Off Duty",
            email="off@test.com",
            specialty="photo",
            is_active=True,
            status="available",
            onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
            application_profile={"credentials_person_verified": True},
            service_areas=["Mississauga"],
            home_city="Mississauga",
        ),
        VendorMember(
            id="v2",
            name="On Duty",
            email="on@test.com",
            specialty="photo",
            is_active=True,
            status="available",
            onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
            application_profile={"credentials_person_verified": True},
            service_areas=["Mississauga"],
            home_city="Mississauga",
        ),
    ]
    chosen = pick_vendor_rule_based(
        job,
        vendors,
        availability_by_member={
            "v1": {"available": False, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 4},
            "v2": {"available": True, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 4},
        },
        calendar_connected={"v1": False, "v2": False},
    )
    assert chosen == "v2"


@pytest.mark.asyncio
async def test_assign_failure_marks_dispatch_failed_and_notifies_owner(monkeypatch):
    """No eligible vendor → status=dispatch_failed (ops queue) + owner notify, not silent unassigned."""
    from app.services import vendor_selection as vs

    job_doc = {
        "id": "job_fail_1",
        "booking_id": "lead_1",
        "address": "99 Nowhere Rd Calgary",
        "city": "Calgary",
        "job_type": "drone",
        "vendor_id": "default_vendor",
        "time_window_start": "09:00",
        "scheduled_date": "2026-09-20",
        "status": "pending_assignment",
        "add_ons": [{"key": "drone"}],
        "package_name": "Essentials",
        "min_tier_required": 1,
        "lat": None,
        "lng": None,
    }
    members = [
        {
            "id": "ic_gta",
            "name": "GTA only",
            "email": "g@test.com",
            "is_active": True,
            "status": "available",
            "vendor_id": "default_vendor",
            "specialty": "photo",
            "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00", "application_profile": {"credentials_person_verified": True},
            "skills": ["photo"],
            "service_areas": ["Mississauga", "GTA"],
            "home_city": "Mississauga",
            "tier": 1,
        }
    ]
    mock_db = _supabase_style_db(job_doc, members)
    marked = []
    notified = []

    async def fake_mark(job_id):
        marked.append(job_id)

    async def fake_notify(job, err):
        notified.append((job.id, err))

    monkeypatch.setenv("USE_AI_DISPATCH", "false")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        with patch("app.services.vendor_selection.bricks_smart_assign", AsyncMock(return_value=None)):
            with patch("app.services.vendor_selection.load_occupied_jobs_by_vendor", AsyncMock(return_value={})):
                with patch(
                    "app.services.vendor_availability_gate.load_availability_by_member",
                    AsyncMock(return_value={}),
                ):
                    with patch(
                        "app.services.vendor_availability_gate.member_has_calendar",
                        AsyncMock(return_value=False),
                    ):
                        with patch.object(vs, "_mark_dispatch_failed", side_effect=fake_mark):
                            with patch.object(vs, "_notify_owner_no_vendor", side_effect=fake_notify):
                                chosen = await assign_vendor_for_job("job_fail_1")

    assert chosen is None
    assert marked == ["job_fail_1"]
    assert notified and notified[0][0] == "job_fail_1"


@pytest.mark.asyncio
async def test_mark_dispatch_failed_writes_expected_status():
    """_mark_dispatch_failed must write status=dispatch_failed (admin queue key)."""
    from app.services.vendor_selection import _mark_dispatch_failed

    updates = []
    mock_db = MagicMock()
    chain = MagicMock()
    chain.update.side_effect = lambda payload: (updates.append(dict(payload)), chain)[1]
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=None))
    mock_db.table.return_value = chain

    with patch("app.services.vendor_selection.get_db", AsyncMock(return_value=mock_db)):
        await _mark_dispatch_failed("job_x")

    assert updates
    assert updates[0]["status"] == "dispatch_failed"
    assert updates[0].get("assignee_id") == ""
