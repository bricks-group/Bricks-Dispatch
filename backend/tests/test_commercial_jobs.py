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
        onboarding_status="approved", onboarding_completed_at="2026-10-01T00:00:00+00:00", application_profile={"credentials_person_verified": True}, tier=tier, drone_certified=True,
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
    assert commercial_payout_cents(_job()) == COMMERCIAL_PAYOUT_CENTS[VISUAL] == 18500
    assert commercial_payout_cents(_job("site_mapped_capture")) == 73500
    assert MAPPED_SETUP_PAYOUT_CENTS == 31500
    assert commercial_payout_cents(_job("site_mapped_capture", add_ons=["mapped_setup"])) == 73500 + 31500
    assert commercial_payout_cents(Job(address="1", time_window_start="09:00", job_type="photo")) is None
    rows = {r["kind"]: r for r in payout_table()}
    # Pricing v2 (2026-10-24): rack visit $495, capture $1,499, setup $650; payouts unchanged.
    one_offs = (VISUAL, MAPPED, "mapped_setup")
    assert {k: rows[k]["client_rack_cad"] for k in one_offs} == {VISUAL: 495.0, MAPPED: 1499.0, "mapped_setup": 650.0}
    assert rows[VISUAL]["bricks_keeps_cad"] == 310.0
    # Kam rule: Bricks keeps the larger cut; vendor <= 50% of rack.
    for kind in rows:
        share = rows[kind]["vendor_payout_cad"] / rows[kind]["client_rack_cad"]
        assert share < 0.50
        assert rows[kind]["bricks_keeps_cad"] > rows[kind]["vendor_payout_cad"]


def test_every_monthly_plan_keeps_positive_per_visit_margin():
    # (price per site per month, visits) for each plan; payout is fixed per visit.
    plans = {
        "visual_monthly": (450, 1, VISUAL), "visual_biweekly": (850, 2, VISUAL), "visual_weekly": (1600, 4, VISUAL),
        "mapped_monthly": (1400, 1, MAPPED), "mapped_biweekly": (2600, 2, MAPPED), "mapped_weekly": (3900, 4, MAPPED),
    }
    for key, (price, visits, kind) in plans.items():
        per_visit = price / visits
        assert per_visit - COMMERCIAL_PAYOUT_CENTS[kind] / 100 > 0, key
    assert 650 - MAPPED_SETUP_PAYOUT_CENTS / 100 > 0


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
       "payout_mode": "owner_full", "is_active": True, "onboarding_status": "approved", "onboarding_completed_at": "2026-10-01T00:00:00+00:00"}


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


# ── In-house employees (future hook) ─────────────────────────────────────────
from app.services.commercial_jobs import (  # noqa: E402
    is_employee,
    member_commercial_payout_cents,
    set_company_policy,
)

POLICY = {"id": "bricks", "verified_at": "2026-10-17T00:00:00Z", "aviation_covered": True,
          "covers_employees": True, "liability_cad": 5_000_000, "expires_on": FUTURE}


def _employee(vid="e1", tier=1, rpas="advanced", skills=("photo", "drone")):
    v = _vendor(vid, tier=tier, insured=False, limit=None, expires=None, aviation=False, rpas=rpas, skills=skills)
    d = v.model_dump()
    d["worker_type"] = "employee"
    return d


def teardown_function(_fn):
    set_company_policy(None)


def test_employee_covered_by_company_policy_not_personal_coi():
    set_company_policy(POLICY)
    e = _employee()
    assert is_employee(e)
    assert member_eligible_for_job(e, _job()) is True
    assert member_eligible_for_job(e, _job(min_insurance_cad=5_000_000)) is True
    assert top_tier_insurance_flag(e) is None


def test_employee_blocked_without_or_with_bad_company_policy():
    e = _employee()
    set_company_policy(None)
    assert "company_insurance_missing" in commercial_gate_failures(e, _job())
    set_company_policy({**POLICY, "covers_employees": False})
    assert "company_policy_excludes_employees" in commercial_gate_failures(e, _job())
    set_company_policy({**POLICY, "expires_on": PAST})
    assert "company_insurance_expired" in commercial_gate_failures(e, _job())
    set_company_policy({**POLICY, "liability_cad": 2_000_000})
    assert member_eligible_for_job(e, _job(min_insurance_cad=5_000_000)) is False


def test_employee_still_needs_rpas_and_skills():
    set_company_policy(POLICY)
    assert member_eligible_for_job(_employee(rpas="basic"), _job()) is False
    assert member_eligible_for_job(_employee(), _job("site_mapped_capture")) is False
    assert member_eligible_for_job(_employee(skills=("photo", "drone", "mapping")), _job("site_mapped_capture")) is True


def test_contractor_cannot_use_company_policy():
    set_company_policy(POLICY)
    assert member_eligible_for_job(_vendor(insured=False), _job()) is False


def test_employee_has_no_per_job_payout():
    assert member_commercial_payout_cents(_employee(), _job()) == 0
    assert member_commercial_payout_cents(_vendor(), _job()) == 18500
    assert member_commercial_payout_cents(_employee(), Job(address="1", time_window_start="09:00", job_type="photo")) is None


def test_employee_payout_cents_zero_via_identity():
    import asyncio
    from app.shared.vendor_identity import photographer_payout_cents
    assert asyncio.run(photographer_payout_cents(None, {"job_type": "progress_capture"}, _employee())) == 0


def test_www_commercial_booking_skips_companion_status_webhook(monkeypatch):
    import asyncio
    from app.services import webhooks

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("must not call Companion for cb_ bookings")
    monkeypatch.setattr(webhooks.httpx, "AsyncClient", Boom)
    assert asyncio.run(webhooks.send_dispatch_status_update_webhook("cb_123", "job1", "assigned")) is False


def test_www_commercial_booking_payload_is_commercial():
    # Shape OS commercial_booking.py sends (no payout fields).
    job = {"job_type": "site_mapped_capture", "booking_id": "cb_x", "min_insurance_cad": 5_000_000,
           "requires_rpas_advanced": True, "add_ons": ["mapped_setup"], "scheduled_date": "2026-10-22"}
    from app.services import commercial_jobs as cj
    assert cj.is_commercial_job(job) and cj.job_min_insurance_cad(job) == 5_000_000
    assert cj.required_skills(job) == {"drone", "mapping"} and cj.job_requires_advanced_rpas(job)
    assert (cj.commercial_payout_cents(job) or 0) > 0  # Dispatch owns the figure
