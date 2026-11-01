"""job_type=video must require reel skill (SKILL_KEYS), not a phantom video skill.

Companion fa52afd stopped stamping Story/Showcase/Suite as job_type=video, but
in-flight + demo jobs and any leftover intake still use video. Assign hard-gate
needed.issubset(have) used to demand "video", which no reel-capable vendor has
via skills=[] / specialty=photo — only specialty=video smuggled a non-SKILL_KEYS
label. Map video→reel lockstep with media_intake / portfolio aliases.
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


def test_skill_keys_have_reel_not_video():
    assert "reel" in SKILL_KEYS
    assert "video" not in SKILL_KEYS


def test_job_type_video_requires_reel_not_video():
    needed = job_required_skills({"job_type": "video"})
    assert needed == {"reel"}
    assert "video" not in needed


def test_job_type_video_with_reel_addon_still_reel_only():
    needed = job_required_skills(
        {"job_type": "video", "add_ons": [{"key": "reel", "included": True}]}
    )
    assert needed == {"reel"}
    assert "video" not in needed


def test_addon_key_video_maps_to_reel():
    needed = job_required_skills(
        {"job_type": "photo", "add_ons": [{"key": "video", "included": True}]}
    )
    assert "reel" in needed
    assert "video" not in needed


def test_vendor_skills_list_video_maps_to_reel():
    have = vendor_skills({"skills": ["photo", "video"], "specialty": "photo"})
    assert "reel" in have
    assert "video" not in have


def test_vendor_specialty_video_maps_to_reel():
    have = vendor_skills({"skills": [], "specialty": "video"})
    assert have == {"reel"}
    assert "video" not in have


def test_reel_vendor_eligible_for_legacy_video_job():
    """Pre-fa52afd Companion Story jobs: job_type=video + reel add-on."""
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
        add_ons=[{"key": "reel", "included": True}],
    )
    reel_vendor = VendorMember(
        id="v-reel",
        name="Reel Pro",
        email="reel@test.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=["photo", "drone", "reel"],
        service_areas=["Mississauga", "GTA"],
    )
    assert member_eligible_for_job(reel_vendor, job) is True


def test_specialty_video_vendor_still_eligible_for_video_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
    )
    video_vendor = VendorMember(
        id="v-vid",
        name="Video Pro",
        email="vid@test.com",
        specialty="video",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert "reel" in vendor_skills(video_vendor)
    assert member_eligible_for_job(video_vendor, job) is True


def test_normalize_skill_key_video_aliases_to_reel():
    assert normalize_skill_key("video") == "reel"
    assert normalize_skill_key("cinematic") == "reel"
    assert normalize_skill_key("cinematic_reel") == "reel"
    assert normalize_skill_key("reel") == "reel"
    assert normalize_skill_key("drone") == "drone"


def test_specialty_reel_matches_legacy_video_job():
    """After ea09a32, have never contains 'video' — specialty=reel must still score."""
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
    )
    reel_specialist = VendorMember(
        id="v-reel-spec",
        name="Reel Specialist",
        email="rs@test.com",
        specialty="reel",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(reel_specialist, job) is True


def test_specialty_video_still_matches_video_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
    )
    video_specialist = VendorMember(
        id="v-vid-spec",
        name="Video Specialist",
        email="vs@test.com",
        specialty="video",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(video_specialist, job) is True


def test_specialty_video_matches_reel_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="reel",
        time_window_start="09:00",
    )
    video_specialist = VendorMember(
        id="v-vid-spec2",
        name="Video Specialist",
        email="vs2@test.com",
        specialty="video",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=[],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(video_specialist, job) is True


def test_photo_specialty_with_reel_skill_matches_video_job():
    """job_type in have after normalize — same as job_type=reel + skills reel."""
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
    )
    photo_reel = VendorMember(
        id="v-photo-reel",
        name="Photo Reel",
        email="pr@test.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=["photo", "reel"],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(photo_reel, job) is True


def test_photo_only_does_not_specialty_match_video_job():
    job = Job(
        address="10 King St Mississauga",
        city="Mississauga",
        job_type="video",
        time_window_start="09:00",
    )
    photo_only = VendorMember(
        id="v-photo",
        name="Photo Only",
        email="po@test.com",
        specialty="photo",
        is_active=True,
        status="available",
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00",
        tier=2,
        skills=["photo"],
        service_areas=["Mississauga", "GTA"],
    )
    assert specialty_matches_job(photo_only, job) is False
