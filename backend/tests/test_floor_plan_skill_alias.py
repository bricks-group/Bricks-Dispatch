"""job_type=floor_plan_only must require photo skill (SKILL_KEYS), not a phantom.

www stripe-webhook / OS book_checkout / Companion PACKAGES stamp
job_type=floor_plan_only for standalone floor-plan books. Assign hard-gate
needed.issubset(have) used to demand "floor_plan_only", which no photo
vendor has via skills=["photo"] / specialty=photo. Map floor_plan*→photo
lockstep with capture_entitlements _FLOORPLAN_JOB_TYPES + video→reel alias.
"""
from app.services.vendor_profile import (
    SKILL_KEYS,
    job_required_skills,
    member_eligible_for_job,
    normalize_skill_key,
    specialty_matches_job,
    vendor_skills,
)
from app.shared.schemas import Job, VendorMember


def test_skill_keys_have_photo_not_floor_plan():
    assert "photo" in SKILL_KEYS
    assert "floor_plan_only" not in SKILL_KEYS
    assert "floor_plan" not in SKILL_KEYS
    assert "floorplan" not in SKILL_KEYS


def test_job_type_floor_plan_only_requires_photo_not_phantom():
    needed = job_required_skills({"job_type": "floor_plan_only"})
    assert needed == {"photo"}
    assert "floor_plan_only" not in needed


def test_job_type_floor_plan_and_floorplan_require_photo():
    assert job_required_skills({"job_type": "floor_plan"}) == {"photo"}
    assert job_required_skills({"job_type": "floorplan"}) == {"photo"}
    assert job_required_skills({"job_type": "floorplans"}) == {"photo"}
    assert job_required_skills({"job_type": "2d_floor_plan"}) == {"photo"}


def test_floor_plan_only_with_addon_still_photo_only():
    needed = job_required_skills(
        {
            "job_type": "floor_plan_only",
            "add_ons": [{"key": "floor_plan_only", "included": True}],
        }
    )
    assert needed == {"photo"}
    assert "floor_plan_only" not in needed


def test_vendor_skills_list_floor_plan_maps_to_photo():
    have = vendor_skills({"skills": ["floor_plan_only"], "specialty": ""})
    assert "photo" in have
    assert "floor_plan_only" not in have


def test_vendor_specialty_floor_plan_only_maps_to_photo():
    have = vendor_skills({"skills": [], "specialty": "floor_plan_only"})
    assert have == {"photo"}
    assert "floor_plan_only" not in have


def test_photo_vendor_eligible_for_floor_plan_only_job():
    """www / Companion / OS floor_plan_only books must assign to photo vendors."""
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="floor_plan_only",
        time_window_start="09:00",
        add_ons=[{"key": "floor_plan_only", "included": True}],
    )
    photo_vendor = VendorMember(
        id="v-photo",
        name="Photo Pro",
        email="photo@test.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=["photo"],
        service_areas=["Mississauga", "GTA"],
    )
    assert member_eligible_for_job(photo_vendor, job) is True


def test_normalize_skill_key_floor_plan_aliases_to_photo():
    assert normalize_skill_key("floor_plan_only") == "photo"
    assert normalize_skill_key("floor_plan") == "photo"
    assert normalize_skill_key("floorplan") == "photo"
    assert normalize_skill_key("floorplans") == "photo"
    assert normalize_skill_key("2d_floor_plan") == "photo"
    assert normalize_skill_key("photo") == "photo"
    assert normalize_skill_key("drone") == "drone"


def test_specialty_photo_matches_floor_plan_only_job():
    """After alias, specialty=photo must still score on job_type=floor_plan_only."""
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="floor_plan_only",
        time_window_start="09:00",
    )
    photo_specialist = VendorMember(
        id="v-photo-spec",
        name="Photo Specialist",
        email="ps@test.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(photo_specialist, job) is True


def test_specialty_floor_plan_only_matches_photo_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="photo",
        time_window_start="09:00",
    )
    fp_specialist = VendorMember(
        id="v-fp-spec",
        name="FP Specialist",
        email="fp@test.com",
        specialty="floor_plan_only",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(fp_specialist, job) is True


def test_reel_only_does_not_specialty_match_floor_plan_only_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="floor_plan_only",
        time_window_start="09:00",
    )
    reel_only = VendorMember(
        id="v-reel",
        name="Reel Only",
        email="ro@test.com",
        specialty="reel",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=["reel"],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(reel_only, job) is False
