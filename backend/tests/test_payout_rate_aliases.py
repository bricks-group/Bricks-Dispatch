"""www à-la-carte package_ids resolve to their own vendor_payout_rates cells."""
from app.services.payout_service import (
    _LEGACY_PACKAGE_IDS,
    _PACKAGE_RATE_ALIASES,
    _WWW_PACKAGE_IDS,
    _rate_card_package_id,
    _sqft_tier,
    is_retired_for_new_jobs,
)
from app.services.vendor_payout_rate_card import (
    VENDOR_PAYOUT_CELLS_CENTS,
    WWW_PACKAGE_IDS,
    all_cells_under_half_rack,
    iter_rate_card_rows,
)


def test_www_catalog_resolves_to_own_cells():
    assert _rate_card_package_id("photos_only") == "photos_only"
    assert _rate_card_package_id("Essentials") == "essentials"
    assert _rate_card_package_id("story") == "story"
    assert _rate_card_package_id("showcase") == "showcase"
    assert _rate_card_package_id("suite") == "full"
    assert _rate_card_package_id("full") == "full"
    assert _rate_card_package_id("full_suite") == "full"
    assert _rate_card_package_id("photos") == "photos_only"
    assert _rate_card_package_id("photosonly") == "photos_only"


def test_legacy_ids_pass_through_for_historical_reads():
    # Not alias targets for www — pass-through only for jobs booked on old ids.
    assert _rate_card_package_id("standard") == "standard"
    assert _rate_card_package_id("premium") == "premium"
    assert _rate_card_package_id("luxury") == "luxury"
    assert _rate_card_package_id("estate") == "estate"
    assert _LEGACY_PACKAGE_IDS == {"standard", "premium", "luxury", "estate"}


def test_aliases_target_www_packages_only():
    assert set(_PACKAGE_RATE_ALIASES.values()) <= _WWW_PACKAGE_IDS
    assert "estate" not in _PACKAGE_RATE_ALIASES.values()
    assert "standard" not in _PACKAGE_RATE_ALIASES.values()
    assert "premium" not in _PACKAGE_RATE_ALIASES.values()
    assert "luxury" not in _PACKAGE_RATE_ALIASES.values()


def test_sqft_tier_www_footprint_boundaries():
    # Small ≤1,200 / Mid 1,201–2,500 / Large 2,501–4,000 / XL 4,001+
    assert _sqft_tier(None) == "small"
    assert _sqft_tier(0) == "small"
    assert _sqft_tier(1200) == "small"
    assert _sqft_tier(1201) == "mid"
    assert _sqft_tier(2500) == "mid"
    assert _sqft_tier(2501) == "large"
    assert _sqft_tier(4000) == "large"
    assert _sqft_tier(4001) == "xl"
    assert _sqft_tier(9000) == "xl"


def test_rate_card_has_sixty_cells():
    rows = list(iter_rate_card_rows())
    assert len(rows) == 60
    assert len(VENDOR_PAYOUT_CELLS_CENTS) == 20  # 5 packages × 4 bands
    assert set(WWW_PACKAGE_IDS) == set(_WWW_PACKAGE_IDS)


def test_all_sixty_cells_vendor_share_at_most_half_rack():
    bad = all_cells_under_half_rack()
    assert bad == [], f"cells above 50% of rack: {bad}"


def test_draft_corner_cells():
    # Tier C Photos only / Small → $55.15; Tier A Full suite / XL → $578.90
    assert VENDOR_PAYOUT_CELLS_CENTS[("photos_only", "small")][0] == 5515
    assert VENDOR_PAYOUT_CELLS_CENTS[("full", "xl")][2] == 57890


def test_legacy_packages_retired_for_new_jobs_only():
    assert is_retired_for_new_jobs("estate") is True
    assert is_retired_for_new_jobs("Estate") is True
    assert is_retired_for_new_jobs("standard") is True
    assert is_retired_for_new_jobs("premium") is True
    assert is_retired_for_new_jobs("luxury") is True
    assert is_retired_for_new_jobs("photos_only") is False
    assert is_retired_for_new_jobs("essentials") is False
    assert is_retired_for_new_jobs("showcase") is False
    assert is_retired_for_new_jobs("full") is False
    assert is_retired_for_new_jobs(None) is False
