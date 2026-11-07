"""Signed, single-use, browser-bound OAuth state for vendor calendar connects.

Launch security audit 2026-11-07: callbacks used ``state=<member_id>`` (unsigned),
so anyone could attach their calendar tokens to any vendor member.

Flow
1. ``POST /calendars/google/connect`` (authenticated vendor) mints a state:
   HMAC-SHA256 over {member id, provider, random nonce, expiry}.
2. The returned ``oauth_url`` points at ``GET /calendars/google/start?state=...``
   on this API, which sets an HttpOnly, Secure, SameSite=Lax cookie holding the
   nonce and 302s to Google. This binds the flow to the browser that started it.
3. The callback verifies signature, provider and expiry, requires the cookie
   nonce to match, and atomically consumes the nonce (primary-key insert into
   ``calendar_oauth_nonces``). Any DB error fails closed. Replays are rejected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

STATE_TTL_SECONDS = 10 * 60
COOKIE_NAME = "bx_cal_oauth"
_STATE_CONTEXT = b"bricks-dispatch/calendar-oauth-state/v1"


class OAuthStateError(ValueError):
    pass


def _secret() -> bytes:
    for name in (
        "CALENDAR_OAUTH_STATE_SECRET",
        "CALENDAR_TOKEN_ENCRYPTION_KEY",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            # Domain-separated derivation so the state key is never the raw secret.
            return hmac.new(raw.encode(), _STATE_CONTEXT, hashlib.sha256).digest()
    raise OAuthStateError("Calendar OAuth state signing is not configured")


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_state(member_id: str, provider: str, *, now: Optional[int] = None) -> str:
    member_id = str(member_id or "").strip()
    provider = str(provider or "").strip().lower()
    if not member_id or not provider:
        raise OAuthStateError("OAuth state is invalid")
    payload = {
        "m": member_id,
        "p": provider,
        "n": secrets.token_urlsafe(24),
        "e": int(now if now is not None else time.time()) + STATE_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{_b64e(raw)}.{_b64e(sig)}"


def read_state(state: str, provider: str, *, now: Optional[int] = None) -> dict:
    """Verify signature, provider and expiry (does not consume)."""
    try:
        raw_b64, sig_b64 = str(state or "").split(".", 1)
        raw, sig = _b64d(raw_b64), _b64d(sig_b64)
    except Exception as e:
        raise OAuthStateError("OAuth state is invalid") from e
    expect = hmac.new(_secret(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        raise OAuthStateError("OAuth state is invalid")
    try:
        payload = json.loads(raw)
        member_id, prov, nonce, exp = payload["m"], payload["p"], payload["n"], int(payload["e"])
    except Exception as e:
        raise OAuthStateError("OAuth state is invalid") from e
    if not (isinstance(member_id, str) and member_id and isinstance(nonce, str) and nonce):
        raise OAuthStateError("OAuth state is invalid")
    if prov != str(provider or "").strip().lower():
        raise OAuthStateError("OAuth state is invalid")
    if int(now if now is not None else time.time()) > exp:
        raise OAuthStateError("OAuth state expired")
    return {"member_id": member_id, "provider": prov, "nonce": nonce, "exp": exp}


def nonce_digest(nonce: str) -> str:
    return hashlib.sha256(str(nonce).encode()).hexdigest()


async def consume_state(
    state: str,
    provider: str,
    *,
    cookie_nonce: Optional[str],
    db,
    now: Optional[int] = None,
) -> str:
    """Validate + bind + atomically consume. Returns the member id. Fails closed."""
    payload = read_state(state, provider, now=now)
    if not cookie_nonce or not hmac.compare_digest(str(cookie_nonce), payload["nonce"]):
        raise OAuthStateError("OAuth was not started from this browser")
    try:
        await db.table("calendar_oauth_nonces").insert(
            {
                "nonce_sha256": nonce_digest(payload["nonce"]),
                "member_id": payload["member_id"],
                "provider": payload["provider"],
            }
        ).execute()
    except Exception as e:  # duplicate key (replay) or DB outage: never fail open
        raise OAuthStateError("OAuth state already used or could not be verified") from e
    return payload["member_id"]
