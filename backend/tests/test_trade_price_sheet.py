"""Set prices and set cuts for trades and add-ons (Kam t631u / t635u, 2026-11-21)."""
from app.services import trade_payout_rate_card as trc
from app.services.payout_service import _trade_payout
from app.services.vendor_profile import QUOTE_ONLY_TYPES, trade_coi_ok


def test_no_quote_only_trades():
    assert QUOTE_ONLY_TYPES == ()


def test_every_trade_has_price_and_cut():
    svc = trc.trade_services()
    for key in ("stg_consult", "stg_vacant_small", "cln_1b", "cln_house_xl", "insp_condo",
                "insp_detached_xl", "floor_plan_only", "rep_1h", "rep_30m", "rep_half", "rep_day"):
        assert svc[key]["price"] > 0 and len(svc[key]["payout"]) == 3


def test_cuts_within_rules():
    assert trc.all_cells_within_rules() == []


def test_handyman_prices():
    s = trc.trade_services()
    assert [s[k]["price"] for k in ("rep_1h", "rep_30m", "rep_half", "rep_day")] == [149, 47.5, 449, 849]
    assert s["rep_1h"]["payout"] == [55.15, 62.6, 71.5]


def test_inspection_holds_50_percent():
    assert trc.trade_services()["insp_condo"]["payout"] == [153.55, 164.05, 174.5]


def test_staging_install_80_percent():
    assert trc.trade_services()["stg_vacant_small"]["payout"] == [1996.0, 1996.0, 1996.0]


def test_repairs_extra_time_and_materials():
    job = {"package_id": "rep_1h", "add_ons": [{"key": "rep_30m", "count": 2},
           {"key": "materials", "amount_cents": 4000}], "subtotal_cents": 14900 + 9500 + 4000}
    assert _trade_payout(job, 3) == 7150 + 2 * 2280 + 4000


def test_addons_outside_package_pay():
    adds = [{"key": "twilight"}, {"key": "rush"}, {"key": "drone"}]
    # Essentials already covers drone: twilight Small Tier C + rush Tier C only.
    assert trc.addon_payout_cents("essentials", adds, 1000, 1) == 5550 + 2775
    assert trc.addon_payout_cents("full", [{"key": "twilight"}], 1000, 3) == 0


def test_floor_plan_only_cell():
    assert [trc.floor_plan_only_payout_cents(t) for t in (1, 2, 3)] == [3665, 4160, 4750]


def test_formulas():
    assert trc.leasing_xl_price_cents(30000) == 185000
    assert trc.leasing_xl_price_cents(20000) is None
    assert trc.survey_grade_price_cents(12, first_on_site=True) == (1499 + 2 * 60 + 650) * 100


def test_coi_gate():
    ok = {"coi_verified": True, "coi_liability_cad": 2000000, "coi_expires_on": "2099-01-01"}
    assert trade_coi_ok(ok, "handyman")
    assert not trade_coi_ok({**ok, "coi_liability_cad": 1000000}, "inspector")
    assert not trade_coi_ok({**ok, "coi_expires_on": "2020-01-01"}, "handyman")
    assert trade_coi_ok({}, "cleaner")
