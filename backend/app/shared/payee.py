"""Who a payout is for, named in errors (not the org vendor_id). E2E bug 1, 2026-11-02."""


def payee_label(contractor: dict, member_id: str = "") -> str:
    """Name the person being paid (not the org vendor_id) in payout errors."""
    name = (contractor.get("full_name") or contractor.get("name") or contractor.get("email") or "").strip()
    member = member_id or contractor.get("member_id") or ""
    parts = [p for p in (f"member {member}" if member else "",
                         f"contractor {contractor.get('id')}" if contractor.get("id") else "",
                         contractor.get("stripe_account_id") or "") if p]
    ident = ", ".join(parts)
    if name and ident:
        return f"{name} ({ident})"
    return name or ident or "unknown payee"
