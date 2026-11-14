"""Fair-Use auth fallback for Dispatch when Supabase Auth (``/auth/v1/user``) is down.

Ported from Bricks-OS ``backend/common/fair_use_auth.py`` (branch
fix/os-auth-fairuse-jwt-1114 @ 360ea1b, Sentinel follow-up 2026-11-14):

- Only an *unavailable* Auth API (HTTP 402, 5xx, network) falls through to
  local verification. A definitive 4xx from Auth (invalid / revoked) stays 401.
  Only a real HTTP status (or a transport error) decides; message text never
  does. After an Auth 402, ``get_user`` is skipped for ``AUTH_402_COOLDOWN_S``.
- Local verification is ES256 only, against the project's pinned public JWK
  from ``SUPABASE_JWT_PUBLIC_JWK`` (header alg ES256, header kid == pinned kid,
  algorithms=['ES256']). HS256 is rejected outright: ``SUPABASE_JWT_SECRET`` is
  never read here, and there is no JWKS fetch at runtime. A missing or
  malformed key turns the fallback off (401).
- aud ``authenticated``, iss ``{SUPABASE_URL}/auth/v1``, exp, sub and role
  ``authenticated`` are required, and ``session_id`` must be a UUID.
- ONE read-only query on ONE connection: the Auth user must be live in
  ``auth.users`` (not deleted, not banned) and the token's session live in
  ``auth.sessions`` and owned by sub. Dispatch identity is ``vendor_members``
  (not ``public.users``): the caller resolves the member afterwards and a
  missing member is a 403, exactly like the primary path.
- Any error, missing key, missing DSN or missing row returns None.

The HS256 fair-use minter and its /vendor-os/auth/fair-use-login and
/fair-use-signup routes were deleted (Sentinel 2026-11-14).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

AUTH_402_COOLDOWN_S = 45.0

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


_QUOTA_CODE_RE = re.compile(r"^exceed_[a-z0-9_]+_quota$")


def _structured_codes(err: object) -> list:
    """The structured ``code`` / ``error_code`` fields of an error, never its text."""
    out = [getattr(err, "code", None), getattr(err, "error_code", None)]
    body = None
    js = getattr(err, "json", None)
    if callable(js):
        try:
            body = js()
        except Exception:
            body = None
    if isinstance(body, dict):
        out += [body.get("code"), body.get("error_code")]
    return out


def is_rest_quota_error(err: object) -> bool:
    """True only when Auth/PostgREST reported a 402 / quota in a structured field.

    - A real HTTP status of 402 (``status`` / ``status_code`` / ``response``).
    - postgrest-py 2.33 ``APIError``: the restricted-project 402 body
      (``{"message": "Service for this project is restricted…"}``) fails
      ``APIErrorFromJSON`` validation, so the error is built by
      ``generate_default_error_message(r)`` with ``code = r.status_code`` (the
      int 402). A body that does validate keeps PostgREST's ``code`` string,
      so ``"402"`` and ``exceed_*_quota`` codes are matched there too.
    - Message, hint or details text alone is never enough (Sentinel L1/L2).
    """
    if status_of(err) == 402:
        return True
    for c in _structured_codes(err):
        if isinstance(c, int) and not isinstance(c, bool) and c == 402:
            return True
        if isinstance(c, str):
            c = c.strip()
            if c == "402" or _QUOTA_CODE_RE.match(c):
                return True
    return False


def status_of(err: object) -> Optional[int]:
    """The HTTP status carried by an Auth/PostgREST error, or None."""
    return _status_of(err)


def _status_of(err: object) -> Optional[int]:
    for attr in ("status", "status_code", "code"):
        v = getattr(err, attr, None)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    resp = getattr(err, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def auth_unavailable(err: object) -> bool:
    """True only when Supabase Auth could not answer (402 quota, 5xx, network).

    A definitive answer (400/401/403/404/422: bad, expired or revoked token)
    is NOT unavailable, so the caller must keep returning 401.
    """
    # supabase_auth raises AuthRetryableError (status 0) for network failures.
    if type(err).__name__ == "AuthRetryableError":
        return True
    status = _status_of(err)
    if status:
        return status == 402 or 500 <= status <= 599
    try:
        import httpx

        if isinstance(err, (httpx.TransportError, httpx.TimeoutException)):
            return True
    except Exception:  # pragma: no cover - httpx always present with supabase
        pass
    if isinstance(err, (ConnectionError, TimeoutError)):
        return True
    # No HTTP status and not a transport error: message text never decides (L1).
    return False


# ── 402 cooldown (Sentinel M2c) ──
_cooldown_lock = threading.Lock()
_auth_402_until = 0.0


def note_auth_402(now: Optional[float] = None) -> None:
    """Auth returned 402: skip get_user for AUTH_402_COOLDOWN_S."""
    global _auth_402_until
    t = time.monotonic() if now is None else now
    with _cooldown_lock:
        _auth_402_until = t + AUTH_402_COOLDOWN_S


def clear_auth_cooldown() -> None:
    """Any non-402 Auth outcome ends the cooldown."""
    global _auth_402_until
    with _cooldown_lock:
        _auth_402_until = 0.0


def auth_cooldown_active(now: Optional[float] = None) -> bool:
    t = time.monotonic() if now is None else now
    with _cooldown_lock:
        return t < _auth_402_until


def record_auth_outcome(err: object = None) -> None:
    """Update the cooldown after a get_user call (err=None means success)."""
    if err is not None and status_of(err) == 402:
        note_auth_402()
    else:
        clear_auth_cooldown()


def _expected_issuer() -> Optional[str]:
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    return f"{url}/auth/v1" if url else None


_ALG = "ES256"
_key_cache_lock = threading.Lock()
_key_cache: dict = {}


def _pinned_key() -> Optional[tuple]:
    """(public_key, kid) from SUPABASE_JWT_PUBLIC_JWK, or None (fallback off).

    The env value must be a JSON EC P-256 public JWK with no private part. The
    kid comes from the JWK, else SUPABASE_JWT_KID. Anything else returns None.
    """
    raw = (os.environ.get("SUPABASE_JWT_PUBLIC_JWK") or "").strip()
    env_kid = (os.environ.get("SUPABASE_JWT_KID") or "").strip()
    if not raw:
        return None
    cache_key = (raw, env_kid)
    with _key_cache_lock:
        if cache_key in _key_cache:
            return _key_cache[cache_key]
    result = None
    try:
        import json

        from cryptography.hazmat.primitives.asymmetric import ec
        from jwt.algorithms import ECAlgorithm

        jwk = json.loads(raw)
        if (
            isinstance(jwk, dict)
            and jwk.get("kty") == "EC"
            and jwk.get("crv") == "P-256"
            and "d" not in jwk
            and jwk.get("alg") in (None, _ALG)
        ):
            kid = jwk.get("kid") or env_kid
            key = ECAlgorithm.from_jwk(jwk)
            if (
                isinstance(kid, str)
                and kid.strip()
                and isinstance(key, ec.EllipticCurvePublicKey)
                and isinstance(key.curve, ec.SECP256R1)
            ):
                result = (key, kid.strip())
    except Exception as e:
        logger.warning("SUPABASE_JWT_PUBLIC_JWK unusable: %s", type(e).__name__)
        result = None
    if result is None:
        logger.warning("Fair-Use local JWT fallback is off (no usable pinned ES256 key)")
    with _key_cache_lock:
        _key_cache.clear()
        _key_cache[cache_key] = result
    return result


def decode_supabase_jwt(token: str) -> Optional[dict]:
    """Verify a Supabase access token locally (ES256, pinned kid). Claims or None."""
    pinned = _pinned_key()
    issuer = _expected_issuer()
    if not pinned or not issuer or not isinstance(token, str) or token.count(".") != 2:
        return None
    key, kid = pinned
    try:
        import jwt

        header = jwt.get_unverified_header(token)
        if not isinstance(header, dict) or header.get("alg") != _ALG:
            return None
        if header.get("kid") != kid:
            return None
        claims = jwt.decode(
            token,
            key,
            algorithms=[_ALG],
            audience="authenticated",
            issuer=issuer,
            leeway=10,
            options={
                "require": ["sub", "exp", "aud", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except Exception as e:
        logger.info("local JWT rejected: %s", type(e).__name__)
        return None
    if not isinstance(claims, dict):
        return None
    sub = str(claims.get("sub") or "").strip()
    if not _UUID_RE.match(sub):
        return None
    if claims.get("role") != "authenticated":
        return None
    sid = claims.get("session_id")
    # M1: session_id is required. Real GoTrue access tokens always carry it;
    # tokens without it (e.g. locally minted ones) would survive sign-out.
    if not isinstance(sid, str) or not _UUID_RE.match(sid):
        return None
    return claims


_FLAG_AUTH = "__fua_auth_live"
_FLAG_SESSION = "__fua_session_live"

# One round trip: auth.users live, auth.sessions live and owned by the user.
_LIVE_AUTH_SQL = (
    "SELECT (au.id IS NOT NULL) AS " + _FLAG_AUTH + ", "
    "(s.id IS NOT NULL) AS " + _FLAG_SESSION + ", "
    "au.id::text AS id, au.email AS email, "
    "(au.email_confirmed_at IS NOT NULL) AS email_confirmed "
    "FROM (SELECT %s::uuid AS uid, %s::uuid AS sid) k "
    "LEFT JOIN auth.users au ON au.id = k.uid AND au.deleted_at IS NULL "
    "AND (au.banned_until IS NULL OR au.banned_until <= now()) "
    "LEFT JOIN auth.sessions s ON s.id = k.sid AND s.user_id = k.uid "
    "AND (s.not_after IS NULL OR s.not_after > now()) "
    "LIMIT 1"
)


def pg_load_live_auth_user(claims: dict, pg_connect: Callable[[], Any]) -> Optional[dict]:
    """{"id", "email", "email_confirmed"} only if the auth user and session are live."""
    sub = str(claims.get("sub") or "")
    sid = str(claims.get("session_id") or "")
    if not _UUID_RE.match(sub) or not _UUID_RE.match(sid):
        return None
    conn = None
    try:
        conn = pg_connect()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(_LIVE_AUTH_SQL, (sub, sid))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
        rec = dict(zip(cols, row))
        if rec.get(_FLAG_AUTH) is not True:
            logger.info("local JWT ok but auth user not live")
            return None
        if rec.get(_FLAG_SESSION) is not True:
            logger.info("local JWT ok but session not live")
            return None
        uid = str(rec.get("id") or "")
        if uid.lower() != sub.lower():
            logger.warning("pg live auth user id mismatch")
            return None
        email = rec.get("email")
        return {
            "id": sub,
            "email": (str(email).strip() or None) if email else None,
            "email_confirmed": rec.get("email_confirmed") is True,
        }
    except Exception as e:
        logger.warning("pg live auth check failed: %s", type(e).__name__)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _default_pg_connect():
    from .pg_fallback import _pg_connect

    return _pg_connect()


def verify_and_load_auth_user(token: str, pg_connect: Optional[Callable[[], Any]] = None) -> Optional[dict]:
    """Full Fair-Use path: ES256 signature/claims, then live auth user + session.

    Returns {"id", "email", "email_confirmed", "claims"} or None. Blocking
    (JWT + Postgres), so async callers should run it in a threadpool.
    """
    claims = decode_supabase_jwt(token)
    if not claims:
        return None
    user = pg_load_live_auth_user(claims, pg_connect or _default_pg_connect)
    if not user:
        return None
    user["claims"] = claims
    return user
