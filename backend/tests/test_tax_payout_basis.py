import pytest
from app.services.client_service_payouts import net_price_cents, cap_cents
from app.services.vendor_profile import owner_payout_cents

def test_subtotal_is_the_only_new_payout_basis():
    job = {"subtotal_cents": 10000, "package_price": 113, "rack_price": 113, "add_ons": ["mapped_setup"]}
    assert net_price_cents(job) == 10000
    assert cap_cents(job, 9000) == 5000
    assert owner_payout_cents(job) == 10000

def test_zero_subtotal_does_not_fall_back_to_tax_inclusive_price():
    job = {"subtotal_cents": 0, "package_price": 113}
    assert net_price_cents(job) == 0
    assert cap_cents(job, 9000) == 0
    assert owner_payout_cents(job) == 0

def test_legacy_rows_remain_compatible():
    assert net_price_cents({"package_price": 100}) == 10000
    assert owner_payout_cents({"package_price": 100}) == 10000

@pytest.mark.parametrize("province,rate", [("ON", .13), ("NS", .14), ("BC", .05), ("QC", .05), ("NB", .15), ("NU", .05)])
def test_contractor_tax_follows_property(province, rate):
    from app.services.tax_policy import property_tax_rate
    assert property_tax_rate({"state": province}) == rate
