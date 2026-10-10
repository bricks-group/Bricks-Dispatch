"""Canonical www vendor payout rate card (CAD → cents).

Source: /workspace/ship/VENDOR_PAYOUT_REPRICE_20260927.md
Five packages × four footprint bands × tiers C/B/A (vendor_tier 1/2/3).
Kept in sync with supabase/migrations/0012_vendor_payout_reprice_www_packages.sql.

VENDOR_PAYOUT_RACK_CAD follows www / Companion package ladder with REEL_RATE
[199,249,299,349] (Story/Showcase/Full 447/596/746 Small). Payout CELLS stay
the Sep 27 fixed cents — margin check uses the live rack; do not auto-bump
vendor pay without an explicit reprice.
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
# Client rack = www / Companion package ladder (CAD pre-tax).
# PHOTO+DRONE[+REEL][+TOUR][+TWILIGHT]. REEL_RATE lockstep [199,249,299,349]
# (was package-embedded [149,179,209,249] → story/showcase/full 397/546/696).
# Vendor payout CELLS unchanged — fixed cents stay; share drops slightly, still ≤50%.
VENDOR_PAYOUT_RACK_CAD: dict[tuple[str, str], float] = {
    ("photos_only", "small"): 149.0,
    ("photos_only", "mid"): 199.0,
    ("photos_only", "large"): 249.0,
    ("photos_only", "xl"): 299.0,
    ("essentials", "small"): 248.0,
    ("essentials", "mid"): 318.0,
    ("essentials", "large"): 388.0,
    ("essentials", "xl"): 458.0,
    ("story", "small"): 447.0,
    ("story", "mid"): 567.0,
    ("story", "large"): 687.0,
    ("story", "xl"): 807.0,
    ("showcase", "small"): 596.0,
    ("showcase", "mid"): 766.0,
    ("showcase", "large"): 936.0,
    ("showcase", "xl"): 1106.0,
    ("full", "small"): 746.0,
    ("full", "mid"): 936.0,
    ("full", "large"): 1121.0,
    ("full", "xl"): 1306.0,
}

WWW_PACKAGE_IDS = ("photos_only", "essentials", "story", "showcase", "full")
SQFT_TIERS = ("small", "mid", "large", "xl")
VENDOR_TIERS = (1, 2, 3)  # C / B / A

# Add-on lines (not folded into package cells). Nearest $0.05 to 37/42/48.
# Visit is per job by client footprint. Staging is per image, same on every band.
VISIT_PAYOUT_PACKAGE_ID = "virtual_visit"
STAGING_PAYOUT_PACKAGE_ID = "virtual_staging"
VISIT_PAYOUT_CELLS_CENTS: dict[str, tuple[int, int, int]] = {
    "small": (9215, 10460, 11950),
    "mid": (11065, 12560, 14350),
    "large": (12915, 14660, 16750),
    "xl": (14765, 16760, 19150),
}
VISIT_RACK_CAD = {"small": 249.0, "mid": 299.0, "large": 349.0, "xl": 399.0}
STAGING_PAYOUT_PER_IMAGE_CENTS = (1295, 1470, 1680)  # C / B / A
STAGING_RACK_CAD = 35.0


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


def addon_payout_cents(add_ons, sqft: int | None, vendor_tier: int | None) -> int:
    """Fixed visit (per job) + staging (per image) on top of the package cell.

    Only explicit add_ons keys. Notes text does not pay. Tier outside 1/2/3 uses C.
    """
    import json
    raw = add_ons
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return 0
    if not isinstance(raw, list):
        return 0
    tier_i = {1: 0, 2: 1, 3: 2}.get(int(vendor_tier) if vendor_tier in (1, 2, 3) else 1, 0)
    if sqft:
        band = "small" if sqft <= 1200 else "mid" if sqft <= 2500 else "large" if sqft <= 4000 else "xl"
    else:
        band = "small"
    total = 0
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        key = str(rec.get("key") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if key in {"visit", "virtual_visit", "virtualvisit"}:
            rec_band = str(rec.get("sqft_tier") or "").strip().lower()
            use = rec_band if rec_band in VISIT_PAYOUT_CELLS_CENTS else band
            total += VISIT_PAYOUT_CELLS_CENTS[use][tier_i]
        elif key in {"staging", "virtual_staging", "virtualstaging"}:
            try:
                n = int(rec.get("images") or rec.get("count") or 1)
            except Exception:
                n = 1
            if n < 1:
                n = 1
            total += n * STAGING_PAYOUT_PER_IMAGE_CENTS[tier_i]
    return total


def all_cells_under_half_rack(epsilon: float = 1e-9) -> list[tuple]:
    """Return any (package, band, tier, vendor_share) where vendor/rack > 0.50.

    Includes the five www packages and the visit / staging add-on cells.
    """
    bad = []
    for pkg in WWW_PACKAGE_IDS:
        for band in SQFT_TIERS:
            rack = VENDOR_PAYOUT_RACK_CAD[(pkg, band)]
            for tier, cents in zip(VENDOR_TIERS, VENDOR_PAYOUT_CELLS_CENTS[(pkg, band)]):
                share = (cents / 100.0) / rack
                if share > 0.50 + epsilon:
                    bad.append((pkg, band, tier, share))
    for band, rack in VISIT_RACK_CAD.items():
        for tier, cents in zip(VENDOR_TIERS, VISIT_PAYOUT_CELLS_CENTS[band]):
            share = (cents / 100.0) / rack
            if share > 0.50 + epsilon:
                bad.append((VISIT_PAYOUT_PACKAGE_ID, band, tier, share))
    for tier, cents in zip(VENDOR_TIERS, STAGING_PAYOUT_PER_IMAGE_CENTS):
        share = (cents / 100.0) / STAGING_RACK_CAD
        if share > 0.50 + epsilon:
            bad.append((STAGING_PAYOUT_PACKAGE_ID, "per_image", tier, share))
    return bad
