"""Set dollar payouts for client services and plan visits (Kam, 2026-10-30).

Table B of ship/plan-visits/PROPOSAL.md, in cents. Property-manager,
commercial-leasing and investor jobs used to fall through to the 35/45/55%
fallback; they now pay a set base, plus a Tier A bonus on one-offs.

Rules:
  * The base already covers the bundled deliverables (floor plan, reel,
    Virtual Visit, interior), so add-ons are never paid on top. Paying the
    Virtual Visit twice is the bug this closes (gap audit X4).
  * Plan visits pay the base only, no bonus.
  * Every payout is capped at 50% of what the client actually paid
    (``package_price``, after REIT or member discounts), never the rack price.
    A mapped setup charged with the visit adds its $650 to that basis.

Plan keys arrive as ``package_id`` on plan visits. A Rentals credit spent on
another PM service arrives with that service's key and
``billing_type = client_plan_credit``; it pays the base for that service,
with no bonus, capped at 50% of the credits' value.
"""
from __future__ import annotations

import math
from typing import Any, Optional

VENDOR_MAX_SHARE = 0.50
TOP_TIER = 3
MAPPED_SETUP_CAD = 650.0
PLAN_BILLING_TYPES = frozenset({"commercial_plan", "client_plan", "client_plan_credit"})

# key -> (client price per job in cents, base payout cents, Tier A bonus cents)
ONE_OFF: dict[str, tuple[int, int, int]] = {
    "pm_listing_set": (19500, 8000, 1500),
    "pm_listing_video": (25500, 10500, 2000),
    "pm_condition_report": (14500, 6000, 1000),
    "pm_turnover": (29500, 11500, 2500),
    "pm_building_set": (65000, 27500, 4500),
    "cre_leasing_s": (59500, 24000, 5000),
    "cre_leasing_m": (89500, 36000, 8000),
    "cre_leasing_l": (145000, 58000, 13000),
    "cre_aerial": (34900, 14500, 2500),
    "inv_acquisition": (125000, 50000, 11000),
    "inv_acquisition_mapped": (225000, 95000, 0),
}

# plan key -> (price per visit or credit in cents, base payout cents). No bonus on plans.
PLAN: dict[str, tuple[int, int]] = {
    "pm_rentals_5": (26500, 11500),
    "pm_rentals_12": (24900, 11500),
    "pm_rentals_25": (23500, 11500),
    "cre_leasing_desk_s": (79500, 24000),
    "cre_leasing_desk_m": (79500, 36000),
    "inv_watch_quarterly": (58500, 18500),
    "inv_watch_monthly": (45000, 18500),
    "mapped_monthly": (140000, 68500),
    "mapped_biweekly": (130000, 63500),
    "mapped_weekly": (97500, 47500),
    "visual_monthly": (45000, 18500),
    "visual_biweekly": (42500, 18500),
    "visual_weekly": (40000, 18500),
}


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(getattr(obj, "__dict__", {}) or {})


def _key(job: dict) -> str:
    return str(job.get("package_id") or "").strip().lower()


def _tier(tier: Any) -> int:
    try:
        return int(tier or 1)
    except (TypeError, ValueError):
        return 1


def _add_on_keys(raw: Any) -> set[str]:
    from .commercial_jobs import _add_on_keys as keys
    return keys(raw)


def net_price_cents(job: Any) -> Optional[int]:
    """What the client paid for this job, in cents (package_price is dollars). None if unknown."""
    j = _as_dict(job)
    # Canonical cents include every add-on and exclude client GST/HST.
    if j.get("subtotal_cents") is not None:
        return max(0, int(j["subtotal_cents"]))
    raw = j.get("package_price")
    if raw is None or raw == "":
        return None
    try:
        cents = int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return None
    if cents <= 0:
        # Hand-made jobs carry no price; never zero a photographer on a missing field.
        return None
    if "mapped_setup" in _add_on_keys(j.get("add_ons")):
        cents += int(MAPPED_SETUP_CAD * 100)
    return max(0, cents)


def cap_cents(job: Any, cents: int) -> int:
    """Clamp a payout to 50% of the net price. Unknown price: leave as is."""
    net = net_price_cents(job)
    if net is None:
        return int(cents)
    return min(int(cents), int(math.floor(net * VENDOR_MAX_SHARE)))


def is_client_service(job: Any) -> bool:
    k = _key(_as_dict(job))
    return k in ONE_OFF or k in PLAN


def is_plan_visit(job: Any) -> bool:
    j = _as_dict(job)
    return _key(j) in PLAN or str(j.get("billing_type") or "").strip().lower() in PLAN_BILLING_TYPES


def client_service_payout_cents(job: Any, vendor_tier: Any = None) -> Optional[int]:
    """Set payout for a client service or plan visit, capped at 50% of net. None = not one of these."""
    j = _as_dict(job)
    k = _key(j)
    if k in PLAN:
        return cap_cents(j, PLAN[k][1])
    if k not in ONE_OFF:
        return None
    _price, base, bonus = ONE_OFF[k]
    cents = base
    if not is_plan_visit(j) and _tier(vendor_tier) >= TOP_TIER:
        cents += bonus
    return cap_cents(j, cents)


def set_payout_cents(job: Any, vendor_tier: Any = None) -> Optional[int]:
    """Client service first, then builder commercial visit. None = rate card / fallback applies."""
    cents = client_service_payout_cents(job, vendor_tier)
    if cents is not None:
        return cents
    from .commercial_jobs import commercial_payout_cents
    return commercial_payout_cents(job)


def payout_rows() -> list[dict]:
    """Display table (www / help): price, base, Tier A bonus, all in CAD."""
    rows = [{"key": k, "kind": "one_off", "price_cad": p / 100, "base_cad": b / 100, "tier_a_bonus_cad": x / 100}
            for k, (p, b, x) in ONE_OFF.items()]
    rows += [{"key": k, "kind": "plan", "price_cad": p / 100, "base_cad": b / 100, "tier_a_bonus_cad": 0.0}
             for k, (p, b) in PLAN.items()]
    return rows
