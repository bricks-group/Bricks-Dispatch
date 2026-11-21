"""IC v1.10 insurance align + person-verified approval/dispatch hard gate (step 1)."""
from datetime import date

import pytest

from app.services import trade_specs as ts
from app.services.vendor_profile import (
    member_eligible_for_job,
    trade_coi_ok,
    trade_docs_ok,
)


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    ts.registry.cache_clear()
    yield
    ts.registry.cache_clear()


def test_insurance_floors_match_ic_v110():
    assert ts.cgl_min_cents("handyman") == 200_000_000
    assert ts.cgl_min_cents("inspector") == 100_000_000
    assert ts.cgl_min_cents("cleaner") == 100_000_000
    assert ts.cgl_min_cents("stager") == 100_000_000
    assert ts.cgl_min_cents("measurer") is None
    assert ts.cgl_min_cents("photographer") is None
    eo = ts.eo_mins("inspector")
    assert eo["min_limit_cents"] == 25_000_000
    assert eo["min_aggregate_cents"] == 30_000_000
    assert ts.eo_mins("measurer") is None
    assert ts.eo_mins("handyman") is None


def test_handyman_scope_forbids_fixture_swap_loophole():
    note = (ts.spec("handyman") or {}).get("scope_note") or ""
    assert "simple fixture" not in note.lower()
    assert "no electrical" in note.lower() or "electrical" in note.lower()
    assert "plumbing" in note.lower() and "gas" in note.lower()


def test_measurer_has_no_insurance_requirement():
    m = {"vendor_type": "measurer"}
    kinds = [r["kind"] for r in ts.required_docs(m)]
    assert "eo" not in kinds and "coi" not in kinds
    assert "floor_plan" in kinds and "id_verification" in kinds


def test_stager_and_cleaner_require_cgl_not_inventory_only():
    for vt in ("stager", "cleaner"):
        kinds = [c["kind"] for c in (ts.spec(vt) or {}).get("credentials", [])]
        assert "coi" in kinds
        assert "inventory_insurance" not in kinds


def test_apply_shapes_allow_sole_and_company():
    for vt in ("photographer", "stager", "cleaner", "inspector", "measurer", "handyman"):
        assert set(ts.apply_shapes(vt)) == {"sole", "company"}


def _full_docs(vendor_type: str):
    """Person-verified rows covering every required kind for the trade."""
    m = {"vendor_type": vendor_type, "application_profile": {"trade_credentials": {}}}
    if vendor_type == "photographer":
        m["portfolio_samples"] = [{"path": "a"}, {"path": "b"}]
    docs = []
    for req in ts.required_docs(m):
        kind = req["kind"]
        row = {"doc_type": kind, "status": "verified", "expiry_date": "2030-01-01"}
        if kind in ("portfolio",):
            continue
        if kind in ("proof_photo", "before_after"):
            for i in range(int(req.get("min_count") or 1)):
                docs.append({"doc_type": kind, "status": "verified"})
        else:
            docs.append(row)
    if vendor_type == "photographer":
        # portfolio counted via portfolio_samples when no portfolio_ rows
        pass
    return m, docs


def test_approval_blocked_when_docs_missing():
    m = {"vendor_type": "handyman"}
    missing = ts.missing_person_verified(m, [])
    assert missing
    assert any(x["kind"] == "coi" for x in missing)
    assert any(x["kind"] == "id_verification" for x in missing)


def test_approval_allowed_when_all_person_verified():
    m, docs = _full_docs("cleaner")
    # cleaner also needs police_check + proof photos + id
    assert ts.all_person_verified(m, docs)
    pending = [{**d, "status": "pending"} for d in docs]
    assert not ts.all_person_verified(m, pending)


def test_ai_extracted_does_not_count_as_person_verified():
    m = {"vendor_type": "handyman"}
    docs = [
        {"doc_type": "coi", "status": "extracted", "expiry_date": "2030-01-01"},
        {"doc_type": "id_verification", "status": "verified"},
        {"doc_type": "police_check", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
        {"doc_type": "before_after", "status": "verified"},
    ]
    assert not ts.all_person_verified(m, docs)


def test_trade_coi_ok_inspector_needs_1m_and_eo():
    today = date(2026, 11, 21)
    base = {
        "coi_verified": True,
        "coi_liability_cad": 1_000_000,
        "coi_expires_on": "2030-01-01",
        "application_profile": {
            "trade_credentials": {
                "eo_verified": True,
                "eo_limit_cents": 25_000_000,
                "eo_expires_on": "2030-01-01",
            }
        },
    }
    assert trade_coi_ok(base, "inspector", today=today)
    assert not trade_coi_ok({**base, "coi_liability_cad": 500_000}, "inspector", today=today)
    no_eo = {**base, "application_profile": {"trade_credentials": {}}}
    assert not trade_coi_ok(no_eo, "inspector", today=today)


def test_trade_coi_ok_cleaner_and_stager_need_1m():
    today = date(2026, 11, 21)
    ok = {"coi_verified": True, "coi_liability_cad": 1_000_000, "coi_expires_on": "2030-01-01"}
    assert trade_coi_ok(ok, "cleaner", today=today)
    assert trade_coi_ok(ok, "stager", today=today)
    assert not trade_coi_ok({}, "cleaner", today=today)
    assert trade_coi_ok({}, "measurer", today=today)


def test_trade_coi_ok_handyman_still_2m():
    today = date(2026, 11, 21)
    ok = {"coi_verified": True, "coi_liability_cad": 2_000_000, "coi_expires_on": "2030-01-01"}
    assert trade_coi_ok(ok, "handyman", today=today)
    assert not trade_coi_ok({**ok, "coi_liability_cad": 1_000_000}, "handyman", today=today)


def test_dispatch_blocked_without_credentials_stamp():
    m = {
        "id": "m1",
        "is_active": True,
        "onboarding_status": "approved",
        "vendor_type": "cleaner",
        "role": "Cleaner",
        "onboarding_completed_at": "2026-11-01T00:00:00Z",
        "home_city": "Toronto",
        "tier": 1,
        "coi_verified": True,
        "coi_liability_cad": 1_000_000,
        "coi_expires_on": "2030-01-01",
    }
    job = {"job_type": "cleaning", "address": "Toronto, ON"}
    assert not member_eligible_for_job(m, job)
    stamped = {
        **m,
        "application_profile": {"credentials_person_verified": True, "trade": "cleaner"},
    }
    assert member_eligible_for_job(stamped, job)


def test_dispatch_blocked_when_live_docs_fail():
    m = {
        "vendor_type": "handyman",
        "application_profile": {"credentials_person_verified": True},
        "coi_verified": True,
        "coi_liability_cad": 2_000_000,
        "coi_expires_on": "2030-01-01",
        "_vetting_docs": [],  # live docs override stamp path
    }
    assert not trade_docs_ok(m, "handyman", docs=m["_vetting_docs"])


def test_operator_shape_defaults_sole():
    assert ts.operator_shape({}) == "sole"
    assert ts.operator_shape({"application_profile": {"operator_shape": "company"}}) == "company"
