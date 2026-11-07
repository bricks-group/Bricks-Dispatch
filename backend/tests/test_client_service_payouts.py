"""Client service and plan visit payouts (Kam, 2026-10-30): Table B set
amounts, Tier A bonus on one-offs, no add-on double pay, 50% of net cap."""
import pytest

from app.services import client_service_payouts as csp
from app.services.commercial_jobs import commercial_payout_cents, payout_table


def _job(key, price, **kw):
    return {"package_id": key, "package_price": price, "job_type": kw.pop("job_type", "photo"), **kw}


@pytest.mark.parametrize("key,price,base,bonus", [
    ("pm_listing_set", 195, 8000, 1500),
    ("pm_listing_video", 255, 10500, 2000),
    ("pm_condition_report", 145, 6000, 1000),
    ("pm_turnover", 295, 11500, 2500),
    ("pm_building_set", 650, 27500, 4500),
    ("cre_leasing_s", 595, 24000, 5000),
    ("cre_leasing_m", 895, 36000, 8000),
    ("cre_leasing_l", 1450, 58000, 13000),
    ("cre_aerial", 349, 14500, 2500),
    ("inv_acquisition", 1250, 50000, 11000),
])
def test_one_off_base_and_tier_a_bonus(key, price, base, bonus):
    assert csp.client_service_payout_cents(_job(key, price), 1) == base
    assert csp.client_service_payout_cents(_job(key, price), 2) == base
    assert csp.client_service_payout_cents(_job(key, price), 3) == base + bonus


def test_every_rate_is_at_most_half_of_price():
    for key, (price, base, bonus) in csp.ONE_OFF.items():
        assert 2 * (base + bonus) <= price, key
    for key, (price, base) in csp.PLAN.items():
        assert 2 * base <= price, key


def test_mapped_plan_visits_cut_to_685_635_475():
    for key, per_visit, cents in (("mapped_monthly", 1400, 68500), ("mapped_biweekly", 1300, 63500),
                                  ("mapped_weekly", 975, 47500)):
        job = _job(key, per_visit, job_type="site_mapped_capture", billing_type="commercial_plan")
        assert commercial_payout_cents(job) == cents
        assert csp.set_payout_cents(job, 3) == cents


def test_mapped_one_off_unchanged_and_setup_counts_in_net():
    one = _job("mapped_one_off", 1499, job_type="site_mapped_capture")
    assert commercial_payout_cents(one) == 73500
    setup = {**one, "add_ons": ["mapped_setup"]}
    assert commercial_payout_cents(setup) == 73500 + 31500  # basis 1499 + 650
    weekly_setup = _job("mapped_weekly", 975, job_type="site_mapped_capture", add_ons=["mapped_setup"])
    assert commercial_payout_cents(weekly_setup) == 47500 + 31500


def test_visual_plan_and_one_off():
    assert commercial_payout_cents(_job("visual_weekly", 400, job_type="site_progress_visual")) == 18500
    assert commercial_payout_cents(_job("visual_one_off", 495, job_type="site_progress_visual")) == 18500


def test_cap_uses_net_price_after_reit_discount():
    # REIT 15% off an L leasing set: 1450 -> 1232.50; Tier A 710 would be 57.6% of net.
    job = _job("cre_leasing_l", 1232.5)
    assert csp.client_service_payout_cents(job, 3) == 61625  # floor(50% of 123250)
    assert csp.client_service_payout_cents(job, 1) == 58000  # base fits under the cap
    # 10% off a turnover: 265.50 -> cap 132.75 < 140 Tier A.
    assert csp.client_service_payout_cents(_job("pm_turnover", 265.5), 3) == 13275


def test_plan_visits_pay_base_only_no_bonus():
    assert csp.client_service_payout_cents(_job("pm_rentals_12", 249, billing_type="commercial_plan"), 3) == 11500
    credit = _job("pm_listing_set", 235, billing_type="client_plan_credit")
    assert csp.client_service_payout_cents(credit, 3) == 8000
    building = _job("pm_building_set", 705, billing_type="client_plan_credit")  # 3 Rentals 25 credits
    assert csp.client_service_payout_cents(building, 3) == 27500
    assert csp.client_service_payout_cents(_job("inv_watch_quarterly", 585), 3) == 18500
    assert csp.client_service_payout_cents(_job("cre_leasing_desk_m", 795), 3) == 36000


def test_bundled_add_ons_never_paid_on_top():
    job = _job("cre_leasing_m", 895, add_ons=["floor_plan", "virtual_visit"])
    assert csp.client_service_payout_cents(job, 3) == 44000
    mapped = _job("inv_acquisition_mapped", 2250, job_type="site_mapped_capture",
                  add_ons=["interior", {"key": "virtual_visit", "sqft_tier": "m"}])
    assert csp.set_payout_cents(mapped, 3) == 95000
    assert commercial_payout_cents(mapped) == 95000


def test_unknown_or_missing_price_is_not_zeroed():
    assert csp.client_service_payout_cents(_job("pm_turnover", None), 1) == 11500
    assert csp.client_service_payout_cents(_job("pm_turnover", 0), 1) == 11500


def test_agent_jobs_untouched():
    assert csp.set_payout_cents({"package_id": "essentials", "package_price": 299, "job_type": "photo"}, 3) is None


def test_payout_table_has_plan_rows():
    rows = {r["kind"]: r for r in payout_table()}
    assert rows["mapped_weekly"]["vendor_payout_cad"] == 475.0
    assert rows["visual_monthly"]["vendor_payout_cad"] == 185.0
