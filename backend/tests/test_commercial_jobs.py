"""Commercial (builder/developer) jobs: Tier A + verified insurance + Advanced RPAS.

Kam, 2026-10-17. Listing jobs keep ignoring insurance.
"""
from datetime import date, timedelta

from app.shared.job_scope import KAM_MEMBER_ID, ORG_VENDOR_ID, resolve_manual_assignment
from app.shared.schemas import Job, VendorMember
from app.services.commercial_jobs import (
    COMMERCIAL_PAYOUT_CENTS,
    DEFAULT_MIN_INSURANCE_CAD,
    MAPPED,
    MAPPED_SETUP_PAYOUT_CENTS,
    VISUAL,
    can_hold_top_tier,
    commercial_gate_failures,
    commercial_kind,
    commercial_payout_cents,
    deliverable_kind,
    insurance_status,
    job_min_insurance_cad,
    payout_table,
    top_tier_insurance_flag,
)
from app.services.vendor_profile import (
    job_required_skills,
    member_eligible_for_job,
    min_tier_for_job,
    vendor_skills,
)
from app.services.vendor_selection import pick_vendor_rule_based

FUTURE = (date.today() + timedelta(days=200)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()


def _vendor(vid="a1", tier=3, insured=True, limit=2_000_000, expires=FUTURE,
            aviation=True, rpas="advanced", skills=("photo", "drone")):
    return VendorMember(
        id=vid, name=vid, email=f"{vid}@test.com", specialty="photo",
        vendor_type="photographer", is_active=True, status="available",
        onboarding_status="approved", tier=tier, drone_certified=True,
        rpas_advanced=(rpas == "advanced"), rpas_certificate_level=rpas,
        skills=list(skills),
        coi_verified=insured, coi_liability_cad=limit, coi_expires_on=expires,
        coi_aviation_covered=aviation,
    )


def _job(job_type="progress_capture", **kw):
    base = dict(address="100 Site Rd, Vaughan", time_window_start="09:00", job_type=job_type,
                dev_capture_visit_id="v1", scheduled_date=(date.today() + timedelta(days=3)).isoformat())
    base.update(kw)
    return Job(**base)


def test_progress_capture_bug_fixed_requires_drone_not_phantom_skill():
    job = _job("progress_capture")
    assert commercial_kind(job) == VISUAL
    assert job_required_skills(job) == {"drone"}
    assert member_eligible_for_job(_vendor(), job) is True


def test_commercial_requires_top_tier():
    job = _job()
    assert min_tier_for_job(job) == 3
    assert member_eligible_for_job(_vendor(tier=2), job) is False
    assert "tier_below_top" in commercial_gate_failures(_vendor(tier=2), job)


def test_commercial_requires_verified_unexpired_aviation_insurance():
    job = _job()
    assert member_eligible_for_job(_vendor(insured=False), job) is False
    assert member_eligible_for_job(_vendor(expires=PAST), job) is False
    assert member_eligible_for_job(_vendor(aviation=False), job) is False
    assert member_eligible_for_job(_vendor(limit=1_000_000), job) is False
    assert member_eligible_for_job(_vendor(expires=None), job) is False


def test_insurance_expiring_before_job_date_fails():
    soon = (date.today() + timedelta(days=1)).isoformat()
    job = _job(scheduled_date=(date.today() + timedelta(days=10)).isoformat())
    assert member_eligible_for_job(_vendor(expires=soon), job) is False


def test_job_minimum_configurable_ccdc41_5m():
    job = _job(min_insurance_cad=5_000_000)
    assert job_min_insurance_cad(job) == 5_000_000
    assert member_eligible_for_job(_vendor(limit=2_000_000), job) is False
    assert member_eligible_for_job(_vendor(limit=5_000_000), job) is True
    # A lower per-job value never drops below the $2M floor.
    assert job_min_insurance_cad(_job(min_insurance_cad=500_000)) == DEFAULT_MIN_INSURANCE_CAD


def test_advanced_rpas_required_by_default_optional_per_job():
    assert member_eligible_for_job(_vendor(rpas="basic"), _job()) is False
    assert member_eligible_for_job(_vendor(rpas="basic"), _job(requires_rpas_advanced=False)) is True


def test_mapped_capture_requires_admin_granted_mapping_skill():
    job = _job("site_mapped_capture")
    assert job_required_skills(job) == {"drone", "mapping"}
    assert member_eligible_for_job(_vendor(), job) is False
    assert member_eligible_for_job(_vendor(skills=("photo", "drone", "mapping")), job) is True


def test_mapping_never_inferred_from_equipment_or_specialty():
    m = _vendor().model_dump()
    m["equipment_list"] = ["DJI Matrice 4E mapping kit"]
    m["specialty"] = "mapping"
    assert "mapping" not in vendor_skills(m)


def test_monitoring_visit_resolves_visual_or_mapped():
    assert deliverable_kind(_job("site_monitoring")) == VISUAL
    assert deliverable_kind(_job("site_monitoring", add_ons=["mapped"])) == MAPPED


def test_listing_jobs_still_ignore_insurance():
    job = Job(address="1 Main", time_window_start="09:00", job_type="photo")
    assert member_eligible_for_job(_vendor(tier=1, insured=False), job) is True


def test_rule_based_pick_only_insured_top_tier():
    job = _job()
    vendors = [_vendor("uninsured", insured=False), _vendor("tierb", tier=2), _vendor("ok")]
    assert pick_vendor_rule_based(job, vendors) == "ok"
    assert pick_vendor_rule_based(job, vendors[:2]) is None


def test_commercial_payout_table():
    assert commercial_payout_cents(_job()) == COMMERCIAL_PAYOUT_CENTS[VISUAL] == 23500
    assert commercial_payout_cents(_job("site_mapped_capture")) == 94500
    assert commercial_payout_cents(_job("site_mapped_capture", add_ons=["mapped_setup"])) == 94500 + MAPPED_SETUP_PAYOUT_CENTS
    assert commercial_payout_cents(Job(address="1", time_window_start="09:00", job_type="photo")) is None
    rows = {r["kind"]: r for r in payout_table()}
    # 63% capture share of the sourced client rack, nearest $5.
    for kind in (VISUAL, MAPPED, "mapped_setup"):
        share = rows[kind]["vendor_payout_cad"] / rows[kind]["client_rack_cad"]
        assert 0.62 <= share <= 0.64


def test_top_tier_requires_insurance_and_flags_existing():
    assert can_hold_top_tier(_vendor()) is True
    assert can_hold_top_tier(_vendor(insured=False)) is False
    assert top_tier_insurance_flag(_vendor(insured=False)) == "insurance_not_verified"
    assert top_tier_insurance_flag(_vendor()) is None
    assert top_tier_insurance_flag(_vendor(tier=2, insured=False)) is None


def test_insurance_status_reasons():
    assert insurance_status({"coi_verified": True, "coi_aviation_covered": True, "coi_expires_on": FUTURE, "coi_liability_cad": 2_000_000})["valid"]
    assert insurance_status({})["reason"] == "insurance_not_verified"


KAM = {"id": KAM_MEMBER_ID, "vendor_id": KAM_MEMBER_ID, "role": "Owner", "title": "Owner",
       "payout_mode": "owner_full", "is_active": True, "onboarding_status": "approved"}


def test_manual_assign_blocks_commercial_without_insurance():
    job = {"id": "j1", "vendor_id": ORG_VENDOR_ID, "assignee_id": "", "status": "pending_assignment",
           "address": "100 Site Rd", "job_type": "progress_capture"}
    bad = {**_vendor("ic1", insured=False).model_dump(), "vendor_id": ORG_VENDOR_ID}
    good = {**_vendor("ic2").model_dump(), "vendor_id": ORG_VENDOR_ID}
    code, err, _ = resolve_manual_assignment(actor_id=KAM_MEMBER_ID, actor_member=KAM, job=job, member_id="ic1", target_member=bad)
    assert (code, err) == (409, "commercial_requirements_unmet")
    code, err, patch = resolve_manual_assignment(actor_id=KAM_MEMBER_ID, actor_member=KAM, job=job, member_id="ic2", target_member=good)
    assert code == 200 and patch["assignee_id"] == "ic2"


def test_manual_assign_listing_job_unchanged():
    job = {"id": "j2", "vendor_id": ORG_VENDOR_ID, "assignee_id": "", "status": "pending_assignment",
           "address": "1 Main", "job_type": "photo"}
    ic = {**_vendor("ic3", tier=1, insured=False).model_dump(), "vendor_id": ORG_VENDOR_ID}
    code, _, _ = resolve_manual_assignment(actor_id=KAM_MEMBER_ID, actor_member=KAM, job=job, member_id="ic3", target_member=ic)
    assert code == 200
