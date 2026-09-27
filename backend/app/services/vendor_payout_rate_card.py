"""Canonical www vendor payout rate card (CAD → cents).

Source: /workspace/ship/VENDOR_PAYOUT_REPRICE_20260927.md
Five packages × four footprint bands × tiers C/B/A (vendor_tier 1/2/3).
Kept in sync with supabase/migrations/0012_vendor_payout_reprice_www_packages.sql.
"""

from __future__ import annotations

# (package_id, sqft_tier) → (tier_c_cents, tier_b_cents, tier_a_cents)
VENDOR_PAYOUT_CELLS_CENTS: dict[tuple[str, str], tuple[int, int, int]] = {
    ("photos_only", "small"): (5515, 6260, 7150),
    ("photos_only", "mid"): (7365, 8360, 9550),
    ("photos_only", "large"): (9215, 10460, 11950),
    ("photos_only", "xl"): (11065, 12560, 14350),
    ("essentials", "small"): (9175, 10415, 11905),
    ("essentials", "mid"): (11765, 13355, 15265),
    ("essentials", "large"): (14355, 16295, 18625),
    ("essentials", "xl"): (16945, 19235, 21985),
    ("story", "small"): (14690, 16675, 19055),
    ("story", "mid"): (18390, 20875, 23855),
    ("story", "large"): (22090, 25075, 28655),
    ("story", "xl"): (26160, 29695, 33935),
    ("showcase", "small"): (20200, 22930, 26210),
    ("showcase", "mid"): (25750, 29230, 33410),
    ("showcase", "large"): (31300, 35530, 40610),
    ("showcase", "xl"): (37220, 42250, 48290),
    ("full", "small"): (25750, 29230, 33410),
    ("full", "mid"): (32040, 36370, 41570),
    ("full", "large"): (38145, 43300, 49490),
    ("full", "xl"): (44620, 50650, 57890),
}

# Client rack CAD pre-tax used for margin checks (draft §3).
VENDOR_PAYOUT_RACK_CAD: dict[tuple[str, str], float] = {
    ("photos_only", "small"): 149.0,
    ("photos_only", "mid"): 199.0,
    ("photos_only", "large"): 249.0,
    ("photos_only", "xl"): 299.0,
    ("essentials", "small"): 248.0,
    ("essentials", "mid"): 318.0,
    ("essentials", "large"): 388.0,
    ("essentials", "xl"): 458.0,
    ("story", "small"): 397.0,
    ("story", "mid"): 497.0,
    ("story", "large"): 597.0,
    ("story", "xl"): 707.0,
    ("showcase", "small"): 546.0,
    ("showcase", "mid"): 696.0,
    ("showcase", "large"): 846.0,
    ("showcase", "xl"): 1006.0,
    ("full", "small"): 696.0,
    ("full", "mid"): 866.0,
    ("full", "large"): 1031.0,
    ("full", "xl"): 1206.0,
}

WWW_PACKAGE_IDS = ("photos_only", "essentials", "story", "showcase", "full")
SQFT_TIERS = ("small", "mid", "large", "xl")
VENDOR_TIERS = (1, 2, 3)  # C / B / A


def iter_rate_card_rows():
    """Yield dicts suitable for vendor_payout_rates upserts."""
    for pkg in WWW_PACKAGE_IDS:
        for band in SQFT_TIERS:
            c, b, a = VENDOR_PAYOUT_CELLS_CENTS[(pkg, band)]
            for tier, amount in ((1, c), (2, b), (3, a)):
                yield {
                    "package_id": pkg,
                    "sqft_tier": band,
                    "vendor_tier": tier,
                    "amount_cents": amount,
                }


def all_cells_under_half_rack(epsilon: float = 1e-9) -> list[tuple]:
    """Return any (package, band, tier, vendor_share) where vendor/rack > 0.50."""
    bad = []
    for pkg in WWW_PACKAGE_IDS:
        for band in SQFT_TIERS:
            rack = VENDOR_PAYOUT_RACK_CAD[(pkg, band)]
            for tier, cents in zip(VENDOR_TIERS, VENDOR_PAYOUT_CELLS_CENTS[(pkg, band)]):
                share = (cents / 100.0) / rack
                if share > 0.50 + epsilon:
                    bad.append((pkg, band, tier, share))
    return bad
