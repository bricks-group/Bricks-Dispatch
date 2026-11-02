"""Phase 1 non-media trades: fail-closed job routing, trade titles, storage."""
import asyncio

import pytest
from fastapi import HTTPException

from app.routers import onboarding
from app.services.applicant_review import review_input_from_member
from app.services.vendor_profile import (
    DB_VENDOR_TYPES,
    display_title,
    job_required_vendor_type,
    member_eligible_for_job,
    member_vendor_type,
    storage_vendor_type,
    strict_vendor_type,
)
from app.shared.schemas import VendorApplicationCreate
from app.shared.vendor_identity import member_title, public_profile


def _job(**kw):
    base = {"id": "j1", "address": "100 Queen St W, Toronto, ON", "city": "Toronto"}
    base.update(kw)
    return base


def _member(**kw):
    base = {
        "id": "m1", "name": "Pat", "email": "pat@example.com", "is_active": True,
        "onboarding_status": "approved", "home_city": "Toronto", "service_areas": ["Toronto"],
        "skills": ["photo"],
    }
    base.update(kw)
    return base


@pytest.mark.parametrize("jt", ["photo", "drone", "floor_plan_only", "photo_floorplan", "3d_tour",
                                "progress_capture", "site_mapped_capture", "listing", "reel"])
def test_media_job_types_stay_photographer(jt):
    assert job_required_vendor_type(_job(job_type=jt)) == "photographer"


@pytest.mark.parametrize("jt,vt", [("staging", "stager"), ("cleaning", "cleaner"),
                                   ("inspection", "inspector"), ("measurement", "measurer"),
                                   ("repairs", "handyman"), ("handyman", "handyman")])
def test_trade_job_types(jt, vt):
    assert job_required_vendor_type(_job(job_type=jt)) == vt


@pytest.mark.parametrize("jt", ["plumbing", "electrical", "roof_quote", "pest_control", "zzz"])
def test_unknown_job_types_fail_closed(jt):
    job = _job(job_type=jt)
    assert job_required_vendor_type(job) is None
    assert member_eligible_for_job(_member(vendor_type="photographer", skills=["photo", jt]), job) is False


def test_unknown_explicit_vendor_type_fails_closed():
    assert job_required_vendor_type(_job(job_type="photo", required_vendor_type="electrician")) is None
    assert strict_vendor_type("electrician") is None


def test_quote_only_trades_never_auto_assign():
    inspector = _member(vendor_type="photographer", application_profile={"trade": "inspector"}, skills=["inspection"])
    assert member_vendor_type(inspector) == "inspector"
    assert member_eligible_for_job(inspector, _job(job_type="inspection")) is False


def test_trade_from_application_profile_json_string():
    m = _member(vendor_type="photographer", application_profile='{"trade": "handyman"}')
    assert member_vendor_type(m) == "handyman"


def test_titles_follow_trade():
    assert display_title(_member(vendor_type="stager")) == "Stager IC"
    assert display_title(_member(vendor_type="cleaner")) == "Cleaner IC"
    assert display_title(_member(application_profile={"trade": "measurer"})) == "Measurer IC"
    assert display_title(_member(role="Repairs", vendor_type="photographer")) == "Repairs IC"
    assert display_title(_member(vendor_type="photographer")) == "Photographer IC"
    assert member_title(_member(role="Inspector")) == "Inspector IC"
    assert public_profile(_member(application_profile={"trade": "inspector"}))["vendor_type"] == "inspector"


def test_storage_vendor_type_respects_db_check():
    # vendor_members_vendor_type_check allows all six (2026-11-01).
    for vt in ("inspector", "measurer", "handyman"):
        assert storage_vendor_type(vt) == vt
    for vt in DB_VENDOR_TYPES:
        assert storage_vendor_type(vt) == vt


def _app(vendor_type, creds=None):
    return VendorApplicationCreate(
        name="Pat Example", email="pat@example.com", phone="416 555 0100", city="Toronto",
        vendor_type=vendor_type, equipment_list=["ladder, drill, levels, patch kit"],
        hst_exempt=True, accepted_terms=True, specialties=[], trade_credentials=creds,
    )


def test_paused_repairs_apply_is_refused():
    app = _app("handyman", {"cgl_confirmed": True})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(onboarding._validate_application(app))
    assert "isn't taking applications" in exc.value.detail


def test_inspector_apply_requires_cgl():
    app = _app("inspector", {})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(onboarding._validate_application(app))
    assert "$2M" in exc.value.detail


def test_inspector_apply_with_cgl_ok_and_no_samples_needed():
    app = _app("inspector", {"cgl_confirmed": True, "wsib_number": "123", "years_experience": "6"})
    asyncio.run(onboarding._validate_application(app))
    assert app.trade_credentials["cgl_confirmed"] is True
    assert app.trade_credentials["cgl_limit_cents"] >= 200_000_000
    assert app.trade_credentials["years_experience"] == 6
    assert app.trade_credentials["coi_required"] is True


def test_cleaner_apply_needs_no_cgl():
    app = _app("cleaner", {})
    asyncio.run(onboarding._validate_application(app))
    assert app.trade_credentials["coi_required"] is False


def test_review_input_uses_trade_and_credentials():
    inp = review_input_from_member(_member(
        vendor_type="photographer",
        application_profile={"trade": "inspector", "credentials": {"cgl_confirmed": True}},
    ))
    assert inp.vendor_type == "inspector"
    assert inp.credentials["cgl_confirmed"] is True


def _vet_app(vendor_type, creds=None, vetting=None, hst=None):
    app = _app(vendor_type, creds)
    app.vetting = vetting if vetting is not None else {}
    app.docs_pending = True
    app.hst_number = hst
    return app


def test_vetting_form_occupied_home_needs_reviews_or_two_refs():
    app = _vet_app("cleaner", {"business_name": "Spark Co"}, {"references": [{"name": "A B", "contact": "a@example.com"}]})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(onboarding._validate_application(app))
    assert "two references" in exc.value.detail


def test_vetting_form_reviews_link_ok():
    app = _vet_app("cleaner", {"business_name": "Spark Co"},
                   {"reviews_link": "https://g.page/spark-co", "police_check_consent": True})
    asyncio.run(onboarding._validate_application(app))
    assert app.vetting["reviews_link_known_host"] is True
    assert app.vetting["police_check_consent"] is True


def test_vetting_form_needs_business_or_hst():
    refs = [{"name": "A B", "contact": "a@example.com"}, {"name": "C D", "contact": "416 555 0101"}]
    app = _vet_app("stager", {}, {"references": refs})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(onboarding._validate_application(app))
    assert "business name or HST" in exc.value.detail
    app = _vet_app("stager", {}, {"references": refs}, hst="123456789RT0001")
    asyncio.run(onboarding._validate_application(app))
    assert len(app.vetting["references"]) == 2


def test_vetting_form_rejects_non_https_link():
    app = _vet_app("inspector", {"cgl_confirmed": True, "business_name": "X"}, {"reviews_link": "homestars.com/x"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(onboarding._validate_application(app))
    assert "https://" in exc.value.detail


def test_legacy_form_without_vetting_still_accepted():
    app = _app("cleaner", {})
    asyncio.run(onboarding._validate_application(app))
    assert app.vetting is None
