"""Wave A vendor_type spine: photographer | stager | cleaner; drone stays RPAS capability."""
from app.shared.schemas import Job, VendorMember
from app.services.vendor_profile import (
    job_required_vendor_type,
    member_eligible_for_job,
    member_vendor_type,
    normalize_vendor_type,
)
from app.services.vendor_selection import pick_vendor_rule_based


def test_normalize_vendor_type_aliases():
    assert normalize_vendor_type("Photographer") == "photographer"
    assert normalize_vendor_type("staging") == "stager"
    assert normalize_vendor_type("cleaning") == "cleaner"
    assert normalize_vendor_type("drone") == "photographer"  # capability, not type
    assert normalize_vendor_type("") == "photographer"


def test_job_required_vendor_type_media_vs_wave_a():
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="photo")) == "photographer"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="drone")) == "photographer"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="reel")) == "photographer"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="stager")) == "stager"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="staging")) == "stager"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="cleaner")) == "cleaner"
    assert job_required_vendor_type(Job(address="1", time_window_start="09:00", job_type="cleaning")) == "cleaner"


def test_stager_not_assigned_media_job():
    job = Job(address="123 Main St", job_type="photo", time_window_start="09:00")
    vendors = [
        VendorMember(
            id="s1", name="Stage Pro", email="s@test.com",
            specialty="staging", vendor_type="stager",
            is_active=True, status="available", onboarding_status="approved",
        ),
        VendorMember(
            id="p1", name="Photo Pro", email="p@test.com",
            specialty="photo", vendor_type="photographer",
            is_active=True, status="available", onboarding_status="approved",
        ),
    ]
    assert pick_vendor_rule_based(job, vendors) == "p1"


def test_cleaner_job_picks_cleaner_only():
    job = Job(address="123 Main St", job_type="cleaner", time_window_start="09:00")
    vendors = [
        VendorMember(
            id="p1", name="Photo Pro", email="p@test.com",
            specialty="photo", vendor_type="photographer",
            is_active=True, status="available", onboarding_status="approved",
        ),
        VendorMember(
            id="c1", name="Clean Pro", email="c@test.com",
            specialty="cleaning", vendor_type="cleaner",
            is_active=True, status="available", onboarding_status="approved",
        ),
    ]
    assert pick_vendor_rule_based(job, vendors) == "c1"


def test_drone_job_still_requires_photographer_with_rpas():
    job = Job(address="123 Main St", job_type="drone", time_window_start="09:00", add_ons=["drone"])
    photo_no_rpas = VendorMember(
        id="p1", name="Photo", email="p@test.com", specialty="photo",
        vendor_type="photographer", is_active=True, status="available",
        onboarding_status="approved", drone_certified=True,
    )
    photo_rpas = VendorMember(
        id="p2", name="Aerial", email="a@test.com", specialty="drone",
        vendor_type="photographer", is_active=True, status="available",
        onboarding_status="approved", drone_certified=True,
        rpas_advanced=True, rpas_certificate_level="advanced",
        skills=["photo", "drone"],
    )
    stager = VendorMember(
        id="s1", name="Stage", email="s@test.com", specialty="staging",
        vendor_type="stager", is_active=True, status="available",
        onboarding_status="approved",
    )
    assert member_eligible_for_job(stager, job) is False
    assert member_eligible_for_job(photo_no_rpas, job) is False
    assert member_eligible_for_job(photo_rpas, job) is True
    assert pick_vendor_rule_based(job, [photo_no_rpas, photo_rpas, stager]) == "p2"


def test_member_vendor_type_infers_from_role_when_column_missing():
    assert member_vendor_type({"role": "Stager", "id": "x"}) == "stager"
    assert member_vendor_type({"role": "Cleaner", "id": "x"}) == "cleaner"
    assert member_vendor_type({"role": "Photographer", "id": "x"}) == "photographer"
