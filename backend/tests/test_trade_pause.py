"""Paused trades (handyman, 2026-11-01) and paused vendors are never assigned."""
import json
import os
from datetime import date

from app.services import trade_specs as ts
from app.services.vendor_profile import member_eligible_for_job
from app.shared.vendor_identity import is_assignable, is_vendor_paused


def _m(**kw):
    base = {"id": "m1", "is_active": True, "onboarding_status": "approved", "vendor_type": "handyman",
            "role": "Repairs", "title": "Repairs IC", "onboarding_completed_at": "2026-11-01T00:00:00Z",
            "home_city": "Toronto", "tier": 1}
    base.update(kw)
    return base


def test_handyman_trade_is_paused_but_kept():
    assert "handyman" in ts.trades()
    assert ts.trade_enabled("handyman") is False
    assert ts.paused_trades() == ["handyman"]
    for k in ("photographer", "stager", "cleaner", "inspector", "measurer"):
        assert ts.trade_enabled(k) is True


def test_approved_handyman_is_not_assignable_while_paused():
    m = _m()
    assert is_vendor_paused(m)
    assert not is_assignable(m)
    assert not member_eligible_for_job(m, {"job_type": "repairs", "address": "Toronto"})


def test_paused_vendor_is_not_assignable():
    m = _m(vendor_type="cleaner", role="Cleaner", title="Cleaner IC", paused_at="2026-11-01T00:00:00Z")
    assert is_vendor_paused(m) and not is_assignable(m)
    assert is_assignable(_m(vendor_type="cleaner", role="Cleaner", title="Cleaner IC"))


def test_owner_never_paused():
    assert not is_vendor_paused(_m(id="0c3fa7c2-d23b-4e44-90ad-ab2d19af2580", paused_at="2026-11-01"))


def test_registry_copies_match():
    here = os.path.dirname(ts._PATH)
    assert json.load(open(ts._PATH))["trades"]["handyman"]["enabled"] is False


def test_expired_credential_detected():
    m = _m(vendor_type="inspector", role="Inspector", title="Inspector IC")
    docs = [{"doc_type": "eo", "status": "verified", "expiry_date": "2026-01-01"},
            {"doc_type": "rhi_or_training", "status": "verified", "expiry_date": "2030-01-01"},
            {"doc_type": "coi", "status": "verified", "expiry_date": "2030-01-01"}]
    exp = ts.expired_required(m, docs, today=date(2026, 11, 1))
    assert [e["kind"] for e in exp] == ["eo"]
    states = {c["kind"]: c["state"] for c in ts.credential_states(m, docs, today=date(2026, 11, 1))}
    assert states["coi"] == "verified" and states["sample_report"] == "missing" and states["id_verification"] == "missing"


def test_photographer_listing_coi_not_required():
    m = _m(vendor_type="photographer", role="Photographer", title="Photographer IC")
    kinds = [r["kind"] for r in ts.required_docs(m)]
    assert "coi" not in kinds and "drone_liability" not in kinds
    m2 = dict(m, application_profile={"trade_credentials": {"commercial_drone": True}})
    assert "drone_liability" in [r["kind"] for r in ts.required_docs(m2)]
