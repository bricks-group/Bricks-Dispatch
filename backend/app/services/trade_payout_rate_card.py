"""Set client prices and set vendor cuts for trades and media add-ons.

Kam, 2026-11-21 (t631u: no quotes, set price + cut; t635u: "Close the gaps").
Source of truth: trade_price_sheet.json (same file as
/workspace/pricing-audit-1121/price-sheet.json, copied into www and OS).

* Labour trades and media add-ons: Tier C/B/A get 37/42/48% of the price.
* Inspections: 44/47/50% (Bricks keeps the larger cut, 50% max; Kam 2026-09-27).
* Staging installs: fixed 80% to the stager (they own the inventory).
* Repairs materials: reimbursed at cost, never counted as price.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from typing import Any, Optional

_SHEET = os.path.join(os.path.dirname(__file__), "trade_price_sheet.json")
TIER_INDEX = {1: 0, 2: 1, 3: 2}
MEDIA_SHARE = (0.37, 0.42, 0.48)
INSPECTION_SHARE = (0.44, 0.47, 0.50)
STAGING_PASS_THROUGH = 0.80

# Add-ons already paid inside a package cell (never paid twice).
PACKAGE_INCLUDES = {
    "photos_only": set(),
    "essentials": {"drone"},
    "story": {"drone", "reel"},
    "showcase": {"drone", "reel", "tour"},
    "full": {"drone", "reel", "tour", "twilight"},
    "suite": {"drone", "reel", "tour", "twilight"},
}
_ADDON_ALIASES = {"drone": "drone", "drone_photos": "drone", "reel": "reel", "video": "reel",
                  "tour": "tour", "virtual_tour": "tour", "twilight": "twilight",
                  "rush": "rush", "rush_delivery": "rush"}


@lru_cache(maxsize=1)
def sheet() -> dict:
    with open(_SHEET, encoding="utf-8") as f:
        return json.load(f)


def _tier_i(vendor_tier: Any) -> int:
    try:
        return TIER_INDEX.get(int(vendor_tier), 0)
    except (TypeError, ValueError):
        return 0


def _cents(x: float) -> int:
    return int(round(float(x) * 100))


@lru_cache(maxsize=1)
def trade_services() -> dict:
    out = {}
    for trade, rows in sheet()["trades"].items():
        for r in rows:
            if "price" in r:
                out[r["key"]] = {**r, "trade": trade}
    return out


def trade_price_cents(key: str) -> Optional[int]:
    s = trade_services().get(str(key or "").strip().lower())
    return _cents(s["price"]) if s else None


def band_of(sqft: Any) -> int:
    try:
        n = int(sqft or 0)
    except (TypeError, ValueError):
        n = 0
    if not n or n <= 1200:
        return 0
    return 1 if n <= 2500 else 2 if n <= 4000 else 3


def _add_on_list(raw: Any) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def addon_payout_cents(package_id: Any, add_ons: Any, sqft: Any, vendor_tier: Any) -> int:
    """Twilight / reel / tour / drone / rush booked outside the package, and floor plan only."""
    pkg = str(package_id or "").strip().lower()
    included = PACKAGE_INCLUDES.get(pkg, set())
    ti, b = _tier_i(vendor_tier), band_of(sqft)
    pay = sheet()["addon_payouts"]
    total = 0
    for rec in _add_on_list(add_ons):
        if not isinstance(rec, dict):
            continue
        key = _ADDON_ALIASES.get(str(rec.get("key") or "").strip().lower().replace("-", "_"))
        if not key or key in included:
            continue
        cell = pay[key]["payout"]
        total += _cents(cell[ti] if key == "rush" else cell[b][ti])
    return total


def floor_plan_only_payout_cents(vendor_tier: Any) -> int:
    return _cents(sheet()["addon_payouts"]["floor_plan_only"]["payout"][_tier_i(vendor_tier)])


def survey_grade_price_cents(acres: float, first_on_site: bool = True) -> int:
    f = sheet()["formulas"]["survey_grade"]
    a = max(0.0, min(float(acres or 0), f["max_acres"]))
    extra = max(0.0, math.ceil(a) - f["included_acres"])
    price = f["base_price"] + extra * f["per_extra_acre"] + (f["setup_once"] if first_on_site else 0)
    return _cents(price)


def survey_grade_payout_cents(acres: float, first_on_site: bool = True) -> int:
    f = sheet()["formulas"]["survey_grade"]
    a = max(0.0, min(float(acres or 0), f["max_acres"]))
    extra = max(0.0, math.ceil(a) - f["included_acres"])
    return _cents(f["payout_base"] + extra * f["payout_per_extra_acre"] + (f["setup_payout"] if first_on_site else 0))


def leasing_xl_price_cents(sqft: int) -> Optional[int]:
    f = sheet()["formulas"]["cre_leasing_xl"]
    n = int(sqft or 0)
    if n <= f["from_sqft"] or n > f["max_sqft"]:
        return None
    return _cents(round(f["base_price"] + (n - f["from_sqft"]) * f["per_sqft"]))


def leasing_xl_payout_cents(sqft: int, vendor_tier: Any) -> Optional[int]:
    price = leasing_xl_price_cents(sqft)
    if price is None:
        return None
    f = sheet()["formulas"]["cre_leasing_xl"]
    share = f["payout_base_share"] + (f["tier_a_bonus_share"] if _tier_i(vendor_tier) == 2 else 0)
    return int(math.floor(price * share / 500.0) * 500)  # nearest $5 down


def trade_payout_cents(job: Any, vendor_tier: Any) -> Optional[int]:
    """Set payout for a trade job, or None when the job is not a trade service."""
    j = job if isinstance(job, dict) else dict(getattr(job, "__dict__", {}) or {})
    key = str(j.get("package_id") or "").strip().lower()
    ti = _tier_i(vendor_tier)
    if key == "cre_leasing_xl":
        return leasing_xl_payout_cents(int(j.get("sqft") or 0), vendor_tier)
    if key == "survey_grade":
        return survey_grade_payout_cents(float(j.get("acres") or 0), bool(j.get("first_on_site", True)))
    s = trade_services().get(key)
    if not s:
        return None
    cents = _cents(s["payout"][ti])
    for rec in _add_on_list(j.get("add_ons")):
        if not isinstance(rec, dict):
            continue
        k = str(rec.get("key") or "").strip().lower()
        if k == "rep_30m":
            try:
                n = max(0, int(rec.get("count") or rec.get("units") or 0))
            except (TypeError, ValueError):
                n = 0
            cents += n * _cents(trade_services()["rep_30m"]["payout"][ti])
        elif k in ("materials", "rep_materials"):
            try:
                cents += max(0, int(rec.get("amount_cents") or 0))
            except (TypeError, ValueError):
                pass
    return cents


def is_pass_through(key: str) -> bool:
    s = trade_services().get(str(key or "").strip().lower())
    return bool(s and s.get("vendor_share"))


def materials_cents(job: Any) -> int:
    j = job if isinstance(job, dict) else {}
    total = 0
    for rec in _add_on_list(j.get("add_ons")):
        if isinstance(rec, dict) and str(rec.get("key") or "").lower() in ("materials", "rep_materials"):
            try:
                total += max(0, int(rec.get("amount_cents") or 0))
            except (TypeError, ValueError):
                pass
    return total


def all_cells_within_rules() -> list[tuple]:
    """Any trade cell over its rule: labour 48%, inspection 50%, staging install 80%."""
    bad = []
    for key, s in trade_services().items():
        cap = 0.80 if s.get("vendor_share") else (0.50 if s["trade"] == "inspection" else 0.48)
        for cents in s["payout"]:
            if cents / s["price"] > cap + 0.002:
                bad.append((key, cents, s["price"]))
    return bad
