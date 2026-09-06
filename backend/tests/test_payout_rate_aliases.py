"""www à-la-carte package_ids alias to existing vendor_payout_rates cells."""
from app.services.payout_service import _PACKAGE_RATE_ALIASES, _rate_card_package_id


def test_www_catalog_aliases_to_legacy_cells():
    assert _rate_card_package_id("photos_only") == "standard"
    assert _rate_card_package_id("Essentials") == "standard"
    assert _rate_card_package_id("story") == "premium"
    assert _rate_card_package_id("showcase") == "premium"
    assert _rate_card_package_id("suite") == "luxury"
    assert _rate_card_package_id("full") == "luxury"


def test_legacy_ids_pass_through():
    assert _rate_card_package_id("standard") == "standard"
    assert _rate_card_package_id("premium") == "premium"
    assert _rate_card_package_id("luxury") == "luxury"
    assert _rate_card_package_id("estate") == "estate"


def test_aliases_do_not_invent_new_package_keys():
    # Alias targets must already exist as seeded rate-card package_ids.
    assert set(_PACKAGE_RATE_ALIASES.values()) <= {"standard", "premium", "luxury", "estate"}
