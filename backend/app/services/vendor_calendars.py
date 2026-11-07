"""Multi-provider vendor calendar busy checks for auto-assign.

Providers: Google FreeBusy, Microsoft Graph schedule, Apple CalDAV, optional ICS URL.
Returns True (busy) | False (free) | None (unknown / fail-soft).

Honest Connect status only — never report Connected without a real token/URL.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

PROVIDERS = ("google", "microsoft", "apple", "ics")
# Availability is not Google-only: every provider here is offered with honest Connect.
UI_CONNECT_PROVIDERS = ("google", "microsoft", "apple", "ics")

# Vendor-facing copy — connecting a calendar drives auto-assign, not a hidden setting.
VENDOR_CALENDAR_EDU_COPY = (
    "Connecting a calendar tells Bricks when you’re free so native auto-assign "
    "only offers you jobs that fit your real schedule. Busy on any connected "
    "calendar = you won’t get that slot. Manual hours and the available toggle "
    "still apply if no calendar is connected."
)
VENDOR_CALENDAR_USED_FOR_OFFERS = "Used for job offers."
VENDOR_CALENDAR_NUDGE = "Connect a calendar so we don’t double-book you."
VENDOR_CALENDAR_WELCOME = (
    "Connecting a calendar is how Dispatch knows your availability for auto-assign."
)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return getattr(obj, "__dict__", {}) or {}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def google_client_id() -> str:
    return (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        or os.environ.get("GOOGLE_CLIENT_ID")
        or os.environ.get("GOOGLE_CALENDAR_CLIENT_ID")
        or ""
    ).strip()


def google_client_secret() -> str:
    return (
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or ""
    ).strip()


def google_redirect_uri() -> str:
    return (
        os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
        or os.environ.get("VENDOR_CALENDAR_OAUTH_REDIRECT")
        or ""
    ).strip()


def microsoft_client_id() -> str:
    return (
        os.environ.get("MICROSOFT_OAUTH_CLIENT_ID")
        or os.environ.get("AZURE_AD_CLIENT_ID")
        or os.environ.get("MS_GRAPH_CLIENT_ID")
        or ""
    ).strip()


def microsoft_client_secret() -> str:
    return (
        os.environ.get("MICROSOFT_OAUTH_CLIENT_SECRET")
        or os.environ.get("AZURE_AD_CLIENT_SECRET")
        or os.environ.get("MS_GRAPH_CLIENT_SECRET")
        or ""
    ).strip()


def microsoft_redirect_uri() -> str:
    """Microsoft callback only — do not reuse the Google redirect (would mix codes)."""
    return (
        os.environ.get("MICROSOFT_OAUTH_REDIRECT_URI")
        or os.environ.get("MS_GRAPH_REDIRECT_URI")
        or ""
    ).strip()


# FreeBusy-only on purpose: enough for the auto-assign busy gate, no Google app
# verification screen. Event write-back on assign would need calendar.events — skip.
GOOGLE_CAL_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
MICROSOFT_CAL_SCOPE = "Calendars.Read offline_access User.Read"


def supabase_configured() -> bool:
    """Same Supabase project that already holds the Google OAuth client secret."""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    return bool(url and key)


def google_direct_oauth() -> bool:
    """Token exchange against Google's token endpoint needs the client secret on Vercel."""
    return bool(google_client_id() and google_client_secret())


def google_oauth_configured() -> bool:
    """Ready to start Connect — never needs_env solely for a missing Vercel secret.

    Prefer Supabase-mediated Google OAuth (secret lives in Supabase). Direct OAuth
    is optional when GOOGLE_CLIENT_SECRET is also present on the API.
    """
    if not google_client_id():
        return False
    return supabase_configured() or google_direct_oauth()


def google_connect_via() -> Optional[str]:
    """Default path is supabase; direct only when secret is present and Supabase is not."""
    if not google_oauth_configured():
        return None
    if supabase_configured():
        return "supabase"
    if google_direct_oauth():
        return "direct"
    return None


def microsoft_oauth_configured() -> bool:
    """Microsoft still needs client id + secret on Vercel (honest needs_env otherwise)."""
    return bool(microsoft_client_id() and microsoft_client_secret())


def calendar_success_redirect_base() -> str:
    """Browser landing page after OAuth — live GO Availability on go.brxs.ca."""
    return (
        os.environ.get("VENDOR_CALENDAR_SUCCESS_REDIRECT")
        or "https://go.brxs.ca/availability"
    ).strip().rstrip("/")


def google_supabase_redirect_uri() -> str:
    """SPA callback for Supabase Google Calendar scope grant."""
    return (
        os.environ.get("GOOGLE_SUPABASE_CALENDAR_REDIRECT")
        or "https://go.brxs.ca/auth/google-calendar"
    ).strip()


def build_google_authorize_url(member_id: str) -> str:
    params = {
        "client_id": google_client_id(),
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_CAL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": member_id,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def build_google_supabase_connect_payload() -> dict:
    """Frontend uses supabase.auth.signInWithOAuth with these fields (OS pattern)."""
    return {
        "mode": "supabase",
        "connect_via": "supabase",
        "redirect_uri": google_supabase_redirect_uri(),
        "scope": GOOGLE_CAL_SCOPE,
        "query_params": {"access_type": "offline", "prompt": "consent"},
    }


async def google_calendar_ping(access_token: str) -> dict:
    """Verify calendar scope before marking Connected. Uses FreeBusy (calendar.freebusy scope)."""
    if not access_token:
        return {"ok": False, "status": 0, "via": None}
    headers = {"Authorization": f"Bearer {access_token}"}
    now = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers=headers,
                json={
                    "timeMin": _iso(now),
                    "timeMax": _iso(now + timedelta(minutes=30)),
                    "items": [{"id": "primary"}],
                },
            )
            if resp.status_code < 400:
                return {"ok": True, "status": resp.status_code, "via": "freebusy"}
            return {"ok": False, "status": resp.status_code, "via": "freebusy"}
    except Exception as e:
        logger.warning("Google Calendar ping error: %s", e)
        return {"ok": False, "status": 0, "via": None}


def build_microsoft_authorize_url(member_id: str) -> str:
    params = {
        "client_id": microsoft_client_id(),
        "response_type": "code",
        "redirect_uri": microsoft_redirect_uri(),
        "scope": MICROSOFT_CAL_SCOPE,
        "state": member_id,
        "prompt": "consent",
    }
    return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urlencode(params)


async def microsoft_calendar_ping(access_token: str) -> dict:
    """Verify Graph calendar access before marking Outlook Connected."""
    if not access_token:
        return {"ok": False, "status": 0, "via": None}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/calendar",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code < 400:
                return {"ok": True, "status": resp.status_code, "via": "graph"}
            return {"ok": False, "status": resp.status_code, "via": "graph"}
    except Exception as e:
        logger.warning("Microsoft calendar ping error: %s", e)
        return {"ok": False, "status": 0, "via": None}


async def apple_caldav_ping(url: str, password: str, username: str) -> dict:
    """REPORT a tiny window — Connected only if CalDAV accepts the credentials."""
    if not (url or "").strip() or not (password or "").strip():
        return {"ok": False, "status": 0, "via": "caldav"}
    now = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.request(
                "REPORT",
                url.strip(),
                content=(
                    '<?xml version="1.0" encoding="utf-8" ?>'
                    '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                    "<D:prop><D:getetag/></D:prop>"
                    "<C:filter><C:comp-filter name=\"VCALENDAR\">"
                    f'<C:comp-filter name="VEVENT">'
                    f'<C:time-range start="{now.strftime("%Y%m%dT%H%M%SZ")}" '
                    f'end="{(now + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")}"/>'
                    "</C:comp-filter></C:comp-filter></C:filter>"
                    "</C:calendar-query>"
                ),
                headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
                auth=(username or "user", password),
            )
            if resp.status_code < 400:
                return {"ok": True, "status": resp.status_code, "via": "caldav"}
            return {"ok": False, "status": resp.status_code, "via": "caldav"}
    except Exception as e:
        logger.warning("Apple CalDAV ping error: %s", e)
        return {"ok": False, "status": 0, "via": "caldav"}


async def ics_feed_ping(url: str) -> dict:
    """GET the feed — Connected only if the URL returns ICS."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return {"ok": False, "status": 0, "via": "ics"}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(u)
            text = (resp.text or "").upper()
            if resp.status_code >= 400:
                return {"ok": False, "status": resp.status_code, "via": "ics"}
            if "BEGIN:VCALENDAR" not in text and "BEGIN:VEVENT" not in text:
                return {"ok": False, "status": resp.status_code or 200, "via": "ics"}
            return {"ok": True, "status": resp.status_code, "via": "ics"}
    except Exception as e:
        logger.warning("ICS ping error: %s", e)
        return {"ok": False, "status": 0, "via": "ics"}


def _expires_at_iso(expires_in: Any) -> Optional[str]:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


async def exchange_google_code(code: str) -> dict:
    """Exchange authorization code for Google tokens. Raises on HTTP/token errors."""
    client_id = google_client_id()
    client_secret = google_client_secret()
    redirect_uri = google_redirect_uri()
    if not (client_id and client_secret and redirect_uri):
        raise RuntimeError("Google OAuth client id/secret/redirect not configured")
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data=data)
    payload = {}
    try:
        payload = resp.json() if hasattr(resp, "json") else {}
        if callable(payload):
            payload = payload()
    except Exception:
        payload = {}
    if getattr(resp, "status_code", 500) >= 400 or not isinstance(payload, dict):
        err = ""
        if isinstance(payload, dict):
            err = str(payload.get("error_description") or payload.get("error") or "")
        raise RuntimeError(err or f"Google token exchange HTTP {getattr(resp, 'status_code', '?')}")
    if not payload.get("access_token"):
        raise RuntimeError("Google did not return an access_token")
    return payload


async def exchange_microsoft_code(code: str) -> dict:
    """Exchange authorization code for Microsoft tokens. Raises on HTTP/token errors."""
    client_id = microsoft_client_id()
    client_secret = microsoft_client_secret()
    redirect_uri = microsoft_redirect_uri()
    if not (client_id and client_secret and redirect_uri):
        raise RuntimeError("Microsoft OAuth client id/secret/redirect not configured")
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "scope": "Calendars.Read offline_access User.Read",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    payload = {}
    try:
        payload = resp.json() if hasattr(resp, "json") else {}
        if callable(payload):
            payload = payload()
    except Exception:
        payload = {}
    if getattr(resp, "status_code", 500) >= 400 or not isinstance(payload, dict):
        err = ""
        if isinstance(payload, dict):
            err = str(payload.get("error_description") or payload.get("error") or "")
        raise RuntimeError(err or f"Microsoft token exchange HTTP {getattr(resp, 'status_code', '?')}")
    if not payload.get("access_token"):
        raise RuntimeError("Microsoft did not return an access_token")
    return payload


async def upsert_oauth_connection(
    db,
    *,
    member_id: str,
    vendor_id: str,
    provider: str,
    token_payload: dict,
) -> dict:
    """Persist OAuth tokens (encrypted at rest when CALENDAR_TOKEN_ENCRYPTION_KEY is set).

    Returns a row suitable for honest connection_status (ciphertext still counts as present).
    """
    from .calendar_token_crypto import encrypt_connection_secrets

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "member_id": member_id,
        "vendor_id": vendor_id or "default_vendor",
        "provider": provider,
        "access_token": token_payload.get("access_token"),
        "refresh_token": token_payload.get("refresh_token"),
        "token_expires_at": _expires_at_iso(token_payload.get("expires_in")),
        "updated_at": now,
        "created_at": now,
    }
    stored = encrypt_connection_secrets(row)
    try:
        await db.table("vendor_calendar_connections").upsert(stored, on_conflict="member_id,provider").execute()
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_upsert_vendor_calendar_connection,
        )
        if _is_rest_quota_error(e):
            pg_row = pg_upsert_vendor_calendar_connection(stored)
            if not pg_row:
                raise RuntimeError(
                    "Could not save calendar connection (Fair-Use Postgres fallback failed)"
                ) from e
            return stored
        raise
    return stored


async def persist_google_oauth_if_verified(
    db,
    *,
    member_id: str,
    vendor_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: Any = 3600,
    scope: str | None = None,
) -> dict:
    """Store Google tokens only after a refresh token + successful Calendar ping.

    Never marks Connected on a token dump alone. Failures return an honest
    connection_status-shaped payload (connected=False).
    """
    access = (access_token or "").strip()
    refresh = (refresh_token or "").strip()
    granted = (scope or "").strip()
    if granted and "calendar" not in granted.lower():
        return {
            "provider": "google",
            "connected": False,
            "status": "noscope",
            "detail": "Google Calendar permission is required. Approve calendar access and retry.",
        }
    if not refresh:
        return {
            "provider": "google",
            "connected": False,
            "status": "noscope",
            "detail": (
                "Google Calendar permission is required (missing refresh token). "
                "Try Connect again and approve calendar access."
            ),
        }
    if not access:
        return {
            "provider": "google",
            "connected": False,
            "status": "error",
            "detail": "Could not connect Google Calendar (missing access token).",
        }
    ping = await google_calendar_ping(access)
    if not ping.get("ok"):
        logger.warning(
            "Google Calendar ping failed (%s via %s) — not marking Connected",
            ping.get("status"),
            ping.get("via"),
        )
        if ping.get("status") in (401, 403):
            return {
                "provider": "google",
                "connected": False,
                "status": "noscope",
                "detail": "Google Calendar permission is required. Approve calendar access and retry.",
            }
        return {
            "provider": "google",
            "connected": False,
            "status": "error",
            "detail": "Could not verify Google Calendar access. Try again.",
        }
    try:
        row = await upsert_oauth_connection(
            db,
            member_id=member_id,
            vendor_id=vendor_id or "default_vendor",
            provider="google",
            token_payload={
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": expires_in or 3600,
                "scope": granted or GOOGLE_CAL_SCOPE,
            },
        )
    except Exception as e:
        logger.warning("Google calendar upsert failed: %s", e)
        return {
            "provider": "google",
            "connected": False,
            "status": "error",
            "detail": f"Could not save calendar connection: {e}",
        }
    status = connection_status(row)
    status["ping"] = ping.get("via")
    return status


async def persist_microsoft_oauth_if_verified(
    db,
    *,
    member_id: str,
    vendor_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: Any = 3600,
    scope: str | None = None,
) -> dict:
    """Store Outlook tokens only after a refresh token + Graph calendar ping."""
    access = (access_token or "").strip()
    refresh = (refresh_token or "").strip()
    granted = (scope or "").strip()
    if granted and "calendar" not in granted.lower():
        return {
            "provider": "microsoft",
            "connected": False,
            "status": "noscope",
            "detail": "Outlook calendar permission is required. Approve Calendars.Read and retry.",
        }
    if not refresh:
        return {
            "provider": "microsoft",
            "connected": False,
            "status": "noscope",
            "detail": (
                "Outlook calendar permission is required (missing refresh token). "
                "Try Connect again and approve calendar access."
            ),
        }
    if not access:
        return {
            "provider": "microsoft",
            "connected": False,
            "status": "error",
            "detail": "Could not connect Outlook (missing access token).",
        }
    ping = await microsoft_calendar_ping(access)
    if not ping.get("ok"):
        logger.warning(
            "Microsoft calendar ping failed (%s via %s) — not marking Connected",
            ping.get("status"),
            ping.get("via"),
        )
        if ping.get("status") in (401, 403):
            return {
                "provider": "microsoft",
                "connected": False,
                "status": "noscope",
                "detail": "Outlook calendar permission is required. Approve Calendars.Read and retry.",
            }
        return {
            "provider": "microsoft",
            "connected": False,
            "status": "error",
            "detail": "Could not verify Outlook calendar access. Try again.",
        }
    try:
        row = await upsert_oauth_connection(
            db,
            member_id=member_id,
            vendor_id=vendor_id or "default_vendor",
            provider="microsoft",
            token_payload={
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": expires_in or 3600,
                "scope": granted or MICROSOFT_CAL_SCOPE,
            },
        )
    except Exception as e:
        logger.warning("Microsoft calendar upsert failed: %s", e)
        return {
            "provider": "microsoft",
            "connected": False,
            "status": "error",
            "detail": f"Could not save calendar connection: {e}",
        }
    status = connection_status(row)
    status["ping"] = ping.get("via")
    return status


async def persist_apple_caldav_if_verified(
    db,
    *,
    member_id: str,
    vendor_id: str,
    caldav_url: str,
    password: str,
    username: str,
) -> dict:
    """Store Apple/CalDAV only after a successful CalDAV ping. Never fake Connected."""
    from .calendar_token_crypto import encrypt_connection_secrets

    url = (caldav_url or "").strip()
    pw = (password or "").strip()
    user = (username or "").strip()
    if not url or not pw:
        return {
            "provider": "apple",
            "connected": False,
            "status": "needs_app_password",
            "detail": (
                "Apple Calendar needs an iCloud CalDAV URL and an app-specific password. "
                "Connected is only shown after CalDAV accepts both."
            ),
        }
    ping = await apple_caldav_ping(url, pw, user)
    if not ping.get("ok"):
        logger.warning("Apple CalDAV ping failed (%s) — not marking Connected", ping.get("status"))
        if ping.get("status") in (401, 403):
            return {
                "provider": "apple",
                "connected": False,
                "status": "needs_app_password",
                "detail": (
                    "Apple Calendar rejected those credentials. Use an iCloud app-specific "
                    "password, not your Apple ID password."
                ),
            }
        return {
            "provider": "apple",
            "connected": False,
            "status": "error",
            "detail": "Could not reach that CalDAV calendar. Check the URL and try again.",
        }
    now = datetime.now(timezone.utc).isoformat()
    stored = encrypt_connection_secrets({
        "member_id": member_id,
        "vendor_id": vendor_id or "default_vendor",
        "provider": "apple",
        "caldav_url": url,
        "caldav_password": pw,
        "caldav_username": user,
        "updated_at": now,
        "created_at": now,
    })
    try:
        await db.table("vendor_calendar_connections").upsert(stored, on_conflict="member_id,provider").execute()
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_upsert_vendor_calendar_connection,
        )
        if _is_rest_quota_error(e):
            if not pg_upsert_vendor_calendar_connection(stored):
                return {
                    "provider": "apple",
                    "connected": False,
                    "status": "error",
                    "detail": "Could not save calendar connection (Fair-Use).",
                }
        else:
            logger.warning("Apple calendar upsert failed: %s", e)
            return {
                "provider": "apple",
                "connected": False,
                "status": "error",
                "detail": f"Could not save CalDAV connection: {e}",
            }
    status = connection_status(stored)
    status["ping"] = ping.get("via")
    return status


async def persist_ics_if_verified(
    db,
    *,
    member_id: str,
    vendor_id: str,
    ics_url: str,
) -> dict:
    """Store an ICS URL only after it returns a calendar feed."""
    url = (ics_url or "").strip()
    if not url:
        return {
            "provider": "ics",
            "connected": False,
            "status": "not_connected",
            "detail": "Paste a public ICS calendar URL",
        }
    ping = await ics_feed_ping(url)
    if not ping.get("ok"):
        logger.warning("ICS ping failed (%s) — not marking Connected", ping.get("status"))
        return {
            "provider": "ics",
            "connected": False,
            "status": "error",
            "detail": "That URL did not return an ICS calendar. Check it is public and try again.",
        }
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "member_id": member_id,
        "vendor_id": vendor_id or "default_vendor",
        "provider": "ics",
        "ics_url": url,
        "updated_at": now,
        "created_at": now,
    }
    try:
        await db.table("vendor_calendar_connections").upsert(row, on_conflict="member_id,provider").execute()
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_upsert_vendor_calendar_connection,
        )
        if _is_rest_quota_error(e):
            if not pg_upsert_vendor_calendar_connection(row):
                return {
                    "provider": "ics",
                    "connected": False,
                    "status": "error",
                    "detail": "Could not save calendar connection (Fair-Use).",
                }
        else:
            logger.warning("ICS calendar upsert failed: %s", e)
            return {
                "provider": "ics",
                "connected": False,
                "status": "error",
                "detail": f"Could not save ICS URL: {e}",
            }
    status = connection_status(row)
    status["ping"] = ping.get("via")
    return status


def connection_status(conn: dict | None) -> dict:
    """Honest status payload for UI — never fake Connected."""
    out = _connection_status(conn)
    c = conn or {}
    if out.get("connected"):
        out["last_synced_at"] = c.get("last_synced_at")
        out["last_sync_status"] = c.get("last_sync_status")
    return out


def _connection_status(conn: dict | None) -> dict:
    c = conn or {}
    provider = str(c.get("provider") or "").lower()
    token = c.get("access_token") or c.get("refresh_token")
    ics = (c.get("ics_url") or "").strip()
    caldav = (c.get("caldav_url") or "").strip()
    password = c.get("caldav_password") or c.get("app_password")

    if provider == "google":
        if token:
            return {"provider": "google", "connected": True, "status": "connected", "detail": "Google Calendar connected"}
        if not google_oauth_configured():
            return {
                "provider": "google",
                "connected": False,
                "status": "needs_env",
                "detail": "Google OAuth client id (and Supabase or client secret) not configured on this environment",
            }
        return {"provider": "google", "connected": False, "status": "not_connected", "detail": "Connect Google Calendar"}

    if provider == "microsoft":
        if token:
            return {"provider": "microsoft", "connected": True, "status": "connected", "detail": "Outlook / Microsoft 365 connected"}
        if not microsoft_oauth_configured():
            return {
                "provider": "microsoft",
                "connected": False,
                "status": "needs_env",
                "detail": "Microsoft OAuth client id/secret not configured on this environment",
            }
        return {"provider": "microsoft", "connected": False, "status": "not_connected", "detail": "Connect Outlook / Microsoft 365"}

    if provider == "apple":
        if caldav and password:
            return {"provider": "apple", "connected": True, "status": "connected", "detail": "Apple Calendar (CalDAV) connected"}
        return {
            "provider": "apple",
            "connected": False,
            "status": "needs_app_password",
            "detail": "Apple Calendar needs an app-specific password + CalDAV URL (iCloud)",
        }

    if provider == "ics":
        if ics:
            return {"provider": "ics", "connected": True, "status": "connected", "detail": "ICS feed connected"}
        return {"provider": "ics", "connected": False, "status": "not_connected", "detail": "Paste a public ICS calendar URL"}

    return {"provider": provider or "unknown", "connected": False, "status": "not_connected", "detail": "Not connected"}


async def _load_connections(db, member_id: str, *, decrypt: bool = True) -> list[dict]:
    """Load calendar connections. When decrypt=True (FreeBusy), secrets are plaintext.

    Encrypt-on-read backfill: if a key is configured and a row still has plaintext
    secrets, re-upsert encrypted (best-effort, soft-fail).
    """
    from .calendar_token_crypto import (
        decrypt_connection_secrets,
        encrypt_connection_secrets,
        needs_reencrypt,
    )

    rows: list[dict] = []
    try:
        res = await db.table("vendor_calendar_connections").select("*").eq("member_id", member_id).execute()
        rows = list(res.data or [])
        for i, row in enumerate(rows):
            if needs_reencrypt(row):
                try:
                    stored = encrypt_connection_secrets(row)
                    stored["updated_at"] = datetime.now(timezone.utc).isoformat()
                    try:
                        await db.table("vendor_calendar_connections").upsert(
                            stored, on_conflict="member_id,provider"
                        ).execute()
                        rows[i] = stored
                    except Exception as e:
                        from ..shared.pg_fallback import (
                            _is_rest_quota_error,
                            pg_upsert_vendor_calendar_connection,
                        )
                        if _is_rest_quota_error(e):
                            if pg_upsert_vendor_calendar_connection(stored):
                                rows[i] = stored
                            else:
                                logger.debug(
                                    "calendar re-encrypt Fair-Use PG soft-fail: %s", e
                                )
                        else:
                            logger.debug("calendar re-encrypt soft-fail: %s", e)
                except Exception as e:
                    logger.debug("calendar re-encrypt soft-fail: %s", e)
        if decrypt:
            rows = [decrypt_connection_secrets(r) for r in rows]
    except Exception as e:
        from ..shared.pg_fallback import (
            _is_rest_quota_error,
            pg_select_vendor_calendar_connections,
        )
        if _is_rest_quota_error(e):
            rows = list(pg_select_vendor_calendar_connections(member_id) or [])
            if decrypt and rows:
                rows = [decrypt_connection_secrets(r) for r in rows]
            return rows
        logger.debug("vendor_calendar_connections read failed (soft): %s", e)
        # Fall back to columns on vendor_members if table missing
        try:
            from ..shared.database import maybe_single_row
            m = await db.table("vendor_members").select(
                "id, google_calendar_token, microsoft_calendar_token, apple_caldav_url, "
                "apple_caldav_password, calendar_ics_url"
            ).eq("id", member_id).maybe_single().execute()
            doc = maybe_single_row(m)
            if doc.get("google_calendar_token"):
                rows.append({"provider": "google", "access_token": doc["google_calendar_token"], "member_id": member_id})
            if doc.get("microsoft_calendar_token"):
                rows.append({"provider": "microsoft", "access_token": doc["microsoft_calendar_token"], "member_id": member_id})
            if doc.get("apple_caldav_url"):
                rows.append({
                    "provider": "apple",
                    "caldav_url": doc.get("apple_caldav_url"),
                    "caldav_password": doc.get("apple_caldav_password"),
                    "member_id": member_id,
                })
            if doc.get("calendar_ics_url"):
                rows.append({"provider": "ics", "ics_url": doc["calendar_ics_url"], "member_id": member_id})
        except Exception:
            pass
    return rows


async def list_vendor_calendar_status(db, member_id: str) -> list[dict]:
    """Status for every Connect provider — Google, Outlook, Apple, ICS."""
    # decrypt=False: Connected means token ciphertext/plaintext present; no key needed for UI.
    existing = {
        str(r.get("provider") or "").lower(): r
        for r in await _load_connections(db, member_id, decrypt=False)
    }
    return [connection_status(existing.get(p) or {"provider": p}) for p in UI_CONNECT_PROVIDERS]


# Sentinel: FreeBusy got 401 — caller should refresh and retry once.
_AUTH_EXPIRED = object()


async def refresh_google_access_token(refresh_token: str) -> Optional[dict]:
    """Refresh Google access_token. Needs client id+secret on the API (fail-soft)."""
    if not refresh_token or not google_direct_oauth():
        return None
    data = {
        "client_id": google_client_id(),
        "client_secret": google_client_secret(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=data)
        payload = {}
        try:
            payload = resp.json() if hasattr(resp, "json") else {}
            if callable(payload):
                payload = payload()
        except Exception:
            payload = {}
        if getattr(resp, "status_code", 500) >= 400 or not isinstance(payload, dict):
            logger.warning("Google token refresh HTTP %s", getattr(resp, "status_code", "?"))
            return None
        if not payload.get("access_token"):
            return None
        # Google omits refresh_token on refresh responses — keep the old one.
        if not payload.get("refresh_token"):
            payload["refresh_token"] = refresh_token
        return payload
    except Exception as e:
        logger.warning("Google token refresh failed (soft): %s", e)
        return None


async def refresh_microsoft_access_token(refresh_token: str) -> Optional[dict]:
    """Refresh Microsoft Graph access_token. Needs Azure client id+secret (fail-soft)."""
    if not refresh_token or not microsoft_oauth_configured():
        return None
    data = {
        "client_id": microsoft_client_id(),
        "client_secret": microsoft_client_secret(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": "Calendars.Read offline_access User.Read",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        payload = {}
        try:
            payload = resp.json() if hasattr(resp, "json") else {}
            if callable(payload):
                payload = payload()
        except Exception:
            payload = {}
        if getattr(resp, "status_code", 500) >= 400 or not isinstance(payload, dict):
            logger.warning("Microsoft token refresh HTTP %s", getattr(resp, "status_code", "?"))
            return None
        if not payload.get("access_token"):
            return None
        if not payload.get("refresh_token"):
            payload["refresh_token"] = refresh_token
        return payload
    except Exception as e:
        logger.warning("Microsoft token refresh failed (soft): %s", e)
        return None


def _token_expired(conn: dict, skew_seconds: int = 120) -> bool:
    """True when token_expires_at is missing/past (refresh proactively)."""
    raw = conn.get("token_expires_at")
    if not raw:
        return False  # unknown expiry — try access_token first; refresh on 401
    try:
        exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc) + timedelta(seconds=skew_seconds)
    except (TypeError, ValueError):
        return False


async def _persist_refreshed_tokens(db, conn: dict, token_payload: dict) -> dict:
    """Write refreshed tokens back (encrypted) and return updated plaintext conn."""
    member_id = str(conn.get("member_id") or "")
    provider = str(conn.get("provider") or "").lower()
    if not member_id or not provider or db is None:
        updated = dict(conn)
        updated["access_token"] = token_payload.get("access_token")
        if token_payload.get("refresh_token"):
            updated["refresh_token"] = token_payload["refresh_token"]
        updated["token_expires_at"] = _expires_at_iso(token_payload.get("expires_in"))
        return updated
    try:
        stored = await upsert_oauth_connection(
            db,
            member_id=member_id,
            vendor_id=str(conn.get("vendor_id") or "default_vendor"),
            provider=provider,
            token_payload={
                "access_token": token_payload.get("access_token"),
                "refresh_token": token_payload.get("refresh_token") or conn.get("refresh_token"),
                "expires_in": token_payload.get("expires_in") or 3600,
            },
        )
        from .calendar_token_crypto import decrypt_connection_secrets
        return decrypt_connection_secrets(stored)
    except Exception as e:
        logger.warning("Persist refreshed calendar token soft-fail: %s", e)
        updated = dict(conn)
        updated["access_token"] = token_payload.get("access_token")
        if token_payload.get("refresh_token"):
            updated["refresh_token"] = token_payload["refresh_token"]
        updated["token_expires_at"] = _expires_at_iso(token_payload.get("expires_in") or 3600)
        return updated


async def ensure_fresh_access_token(conn: dict, db=None) -> Optional[str]:
    """Return a usable access_token, refreshing Google/Microsoft when expired.

    Fail-soft: missing secret / network → return existing token (or None).
    FreeBusy still treats unknown as continue; refreshed tokens make busy=True stick.
    """
    provider = str(conn.get("provider") or "").lower()
    access = (conn.get("access_token") or "").strip() or None
    refresh = (conn.get("refresh_token") or "").strip() or None
    if provider not in ("google", "microsoft"):
        return access
    needs_refresh = (not access) or _token_expired(conn)
    if not needs_refresh:
        return access
    if not refresh:
        return access
    payload = None
    if provider == "google":
        payload = await refresh_google_access_token(refresh)
    elif provider == "microsoft":
        payload = await refresh_microsoft_access_token(refresh)
    if not payload or not payload.get("access_token"):
        return access
    updated = await _persist_refreshed_tokens(db, conn, payload)
    conn.clear()
    conn.update(updated)
    return (conn.get("access_token") or "").strip() or None


async def _google_freebusy(token: str, start: datetime, end: datetime):
    """Return True/False busy, None unknown, or _AUTH_EXPIRED on 401."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "timeMin": _iso(start),
                    "timeMax": _iso(end),
                    "items": [{"id": "primary"}],
                },
            )
            if resp.status_code == 401:
                return _AUTH_EXPIRED
            resp.raise_for_status()
            data = resp.json()
        calendars = data.get("calendars") or {}
        primary = calendars.get("primary") or next(iter(calendars.values()), {})
        busy = primary.get("busy") or []
        return bool(busy)
    except Exception as e:
        logger.debug("Google FreeBusy failed (soft): %s", e)
        return None


async def _microsoft_schedule(token: str, start: datetime, end: datetime, email: str | None) -> Optional[bool]:
    """Busy if /me/calendarView (or getSchedule fallback) shows a busy block.

    calendarView is the FreeBusy equivalent on the signed-in mailbox and does
    not need the vendor email. getSchedule is used when calendarView is empty
    and we have an email.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/calendarView",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "startDateTime": _iso(start),
                    "endDateTime": _iso(end),
                    "$select": "id,showAs",
                    "$top": "20",
                },
            )
            if resp.status_code == 401:
                return _AUTH_EXPIRED
            if resp.status_code == 403:
                return None
            if resp.status_code < 400:
                data = resp.json() if hasattr(resp, "json") else {}
                if callable(data):
                    data = data()
                for item in (data or {}).get("value") or []:
                    show = str(item.get("showAs") or "").lower()
                    if show in ("busy", "oof", "workingelsewhere", "tentative"):
                        return True
                return False
            if not email:
                return None
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/me/calendar/getSchedule",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "schedules": [email],
                    "startTime": {"dateTime": _iso(start).replace("Z", ""), "timeZone": "UTC"},
                    "endTime": {"dateTime": _iso(end).replace("Z", ""), "timeZone": "UTC"},
                    "availabilityViewInterval": 15,
                },
            )
            if resp.status_code == 401:
                return _AUTH_EXPIRED
            if resp.status_code == 403:
                return None
            resp.raise_for_status()
            data = resp.json()
        for item in data.get("value") or []:
            for slot in item.get("scheduleItems") or []:
                status = str(slot.get("status") or "").lower()
                if status in ("busy", "oof", "workingelsewhere", "tentative"):
                    return True
            view = str(item.get("availabilityView") or "")
            # 0=free, 1=tentative, 2=busy, 3=oof, 4=working elsewhere
            if any(ch in "1234" for ch in view):
                return True
        return False
    except Exception as e:
        logger.debug("Microsoft schedule failed (soft): %s", e)
        return None


def _parse_ics_busy(text: str, start: datetime, end: datetime) -> Optional[bool]:
    """Minimal VEVENT overlap check — fail-soft on parse errors."""
    try:
        events = []
        blocks = text.replace("\r\n", "\n").split("BEGIN:VEVENT")
        for block in blocks[1:]:
            def _field(name: str) -> str:
                for line in block.split("\n"):
                    if line.startswith(name) or line.startswith(name.split(";")[0] + ";"):
                        return line.split(":", 1)[-1].strip()
                return ""
            ds = _field("DTSTART")
            de = _field("DTEND")
            if not ds:
                continue
            def _parse(v: str) -> Optional[datetime]:
                v = v.strip()
                if not v:
                    return None
                if v.endswith("Z"):
                    v = v[:-1]
                compact = v.replace("-", "").replace(":", "")
                if "T" in compact:
                    return datetime.strptime(compact[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                return datetime.strptime(compact[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            s = _parse(ds)
            e = _parse(de) if de else (s + __import__("datetime").timedelta(hours=1) if s else None)
            if s and e:
                events.append((s, e))
        s0 = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        e0 = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        for s, e in events:
            if s < e0 and e > s0:
                return True
        return False
    except Exception as e:
        logger.debug("ICS parse failed (soft): %s", e)
        return None


async def _ics_busy(url: str, start: datetime, end: datetime) -> Optional[bool]:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return _parse_ics_busy(resp.text, start, end)
    except Exception as e:
        logger.debug("ICS fetch failed (soft): %s", e)
        return None


async def _apple_caldav_busy(url: str, password: str, username: str, start: datetime, end: datetime) -> Optional[bool]:
    """REPORT calendar-query — needs app-specific password. Fail-soft."""
    if not url or not password:
        return None
    body = f"""<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:getetag/><C:calendar-data/></D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start.strftime('%Y%m%dT%H%M%SZ')}" end="{end.strftime('%Y%m%dT%H%M%SZ')}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.request(
                "REPORT",
                url,
                content=body,
                headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
                auth=(username or "user", password),
            )
            if resp.status_code in (401, 403):
                return None
            if resp.status_code >= 400:
                return None
            # Any VEVENT in response → busy for the window
            if "BEGIN:VEVENT" in resp.text:
                return True
            # Empty multistatus
            try:
                root = ET.fromstring(resp.text)
                if list(root):
                    return True
            except ET.ParseError:
                pass
            return False
    except Exception as e:
        logger.debug("Apple CalDAV busy check failed (soft): %s", e)
        return None


async def _provider_busy(conn: dict, member: dict, start: datetime, end: datetime, db=None):
    """Run FreeBusy for one connection with proactive refresh + one 401 retry."""
    provider = str(conn.get("provider") or "").lower()
    if provider in ("google", "microsoft"):
        token = await ensure_fresh_access_token(conn, db=db)
        if not token:
            return None
        if provider == "google":
            result = await _google_freebusy(token, start, end)
        else:
            result = await _microsoft_schedule(
                token, start, end, member.get("email") or conn.get("email")
            )
        if result is _AUTH_EXPIRED and conn.get("refresh_token"):
            # Force refresh even if expiry looked fine, then retry once.
            payload = None
            if provider == "google":
                payload = await refresh_google_access_token(conn["refresh_token"])
            else:
                payload = await refresh_microsoft_access_token(conn["refresh_token"])
            if payload and payload.get("access_token"):
                updated = await _persist_refreshed_tokens(db, conn, payload)
                conn.clear()
                conn.update(updated)
                token2 = conn.get("access_token")
                if token2:
                    if provider == "google":
                        result = await _google_freebusy(token2, start, end)
                    else:
                        result = await _microsoft_schedule(
                            token2, start, end, member.get("email") or conn.get("email")
                        )
        if result is _AUTH_EXPIRED:
            return None
        return result

    if provider == "apple":
        return await _apple_caldav_busy(
            conn.get("caldav_url") or "",
            conn.get("caldav_password") or conn.get("app_password") or "",
            conn.get("caldav_username") or member.get("email") or "",
            start,
            end,
        )
    if provider == "ics" and (conn.get("ics_url") or "").strip():
        return await _ics_busy(conn["ics_url"].strip(), start, end)
    return None


async def vendor_is_busy(member: Any, start: datetime, end: datetime, db=None) -> Optional[bool]:
    """True if ANY connected calendar is busy; False if at least one is free and none busy; None if unknown.

    Queries every Connected provider (Google, Outlook, Apple CalDAV, ICS). One busy
    calendar excludes the vendor for that slot. No calendars → None (caller uses
    Bricks job-overlap + manual hours). Fail-soft: API errors → None so auto-assign
    continues with other rules instead of stranding the roster.
    """
    m = _as_dict(member)
    member_id = str(m.get("id") or "")
    if not member_id or start >= end:
        return None

    connections: list[dict] = []
    if db is not None:
        connections = await _load_connections(db, member_id)
    else:
        # Inline tokens on member dict (tests / denormalized)
        if m.get("google_calendar_token") or m.get("access_token"):
            connections.append({
                "provider": "google",
                "member_id": member_id,
                "access_token": m.get("google_calendar_token") or m.get("access_token"),
                "refresh_token": m.get("refresh_token") or m.get("google_refresh_token"),
                "token_expires_at": m.get("token_expires_at"),
            })
        if m.get("microsoft_calendar_token"):
            connections.append({
                "provider": "microsoft",
                "member_id": member_id,
                "access_token": m["microsoft_calendar_token"],
                "refresh_token": m.get("microsoft_refresh_token"),
                "token_expires_at": m.get("token_expires_at"),
            })
        if m.get("apple_caldav_url"):
            connections.append({
                "provider": "apple",
                "caldav_url": m.get("apple_caldav_url"),
                "caldav_password": m.get("apple_caldav_password") or m.get("app_password"),
                "caldav_username": m.get("email"),
            })
        if m.get("calendar_ics_url") or m.get("ics_url"):
            connections.append({"provider": "ics", "ics_url": m.get("calendar_ics_url") or m.get("ics_url")})

    if not connections:
        return None

    saw_free = False
    for conn in connections:
        result = await _provider_busy(conn, m, start, end, db=db)
        if result is True:
            return True
        if result is False:
            saw_free = True

    if saw_free:
        return False
    return None
