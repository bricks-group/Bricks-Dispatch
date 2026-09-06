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
# Vendors see Google / Apple / ICS only — Microsoft Graph stays in FreeBusy for
# any legacy connection, but Outlook Connect is not offered (Azure signup needs a card).
UI_CONNECT_PROVIDERS = ("google", "apple", "ics")


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
    return (
        os.environ.get("MICROSOFT_OAUTH_REDIRECT_URI")
        or os.environ.get("VENDOR_CALENDAR_OAUTH_REDIRECT")
        or ""
    ).strip()


GOOGLE_CAL_SCOPE = (
    "https://www.googleapis.com/auth/calendar.readonly "
    "https://www.googleapis.com/auth/calendar.freebusy"
)
# Fallback scope used by OS Calendar Connect when freebusy isn't granted via Supabase.
GOOGLE_CAL_SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events"


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
    """Verify calendar scope before marking Connected. Prefer FreeBusy; fall back to events."""
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
            if resp.status_code in (401, 403):
                # calendar.events may be granted without freebusy — try events list
                ev = await client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    params={"maxResults": 1, "singleEvents": "true"},
                    headers=headers,
                )
                if ev.status_code < 400:
                    return {"ok": True, "status": ev.status_code, "via": "events"}
                return {"ok": False, "status": ev.status_code, "via": "events"}
            return {"ok": False, "status": resp.status_code, "via": "freebusy"}
    except Exception as e:
        logger.warning("Google Calendar ping error: %s", e)
        return {"ok": False, "status": 0, "via": None}


def build_microsoft_authorize_url(member_id: str) -> str:
    params = {
        "client_id": microsoft_client_id(),
        "response_type": "code",
        "redirect_uri": microsoft_redirect_uri(),
        "scope": "Calendars.Read offline_access User.Read",
        "state": member_id,
    }
    return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urlencode(params)


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
    await db.table("vendor_calendar_connections").upsert(stored, on_conflict="member_id,provider").execute()
    return stored


def connection_status(conn: dict | None) -> dict:
    """Honest status payload for UI — never fake Connected."""
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
                    await db.table("vendor_calendar_connections").upsert(
                        stored, on_conflict="member_id,provider"
                    ).execute()
                    rows[i] = stored
                except Exception as e:
                    logger.debug("calendar re-encrypt soft-fail: %s", e)
        if decrypt:
            rows = [decrypt_connection_secrets(r) for r in rows]
    except Exception as e:
        logger.debug("vendor_calendar_connections read failed (soft): %s", e)
        # Fall back to columns on vendor_members if table missing
        try:
            m = await db.table("vendor_members").select(
                "id, google_calendar_token, microsoft_calendar_token, apple_caldav_url, "
                "apple_caldav_password, calendar_ics_url"
            ).eq("id", member_id).maybe_single().execute()
            doc = m.data or {}
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
    """Status for UI Connect providers — Microsoft omitted unless already connected."""
    # decrypt=False: Connected means token ciphertext/plaintext present; no key needed for UI.
    existing = {
        str(r.get("provider") or "").lower(): r
        for r in await _load_connections(db, member_id, decrypt=False)
    }
    out = []
    for p in UI_CONNECT_PROVIDERS:
        out.append(connection_status(existing.get(p) or {"provider": p}))
    # Keep a connected Microsoft row so disconnect still works; never offer Connect.
    ms = existing.get("microsoft")
    if ms:
        st = connection_status(ms)
        if st.get("connected"):
            out.append(st)
    return out


async def _google_freebusy(token: str, start: datetime, end: datetime) -> Optional[bool]:
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
                return None
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
    if not email:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
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
            if resp.status_code in (401, 403):
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
                    return datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                if "T" in v:
                    return datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                return datetime.strptime(v[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
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


async def vendor_is_busy(member: Any, start: datetime, end: datetime, db=None) -> Optional[bool]:
    """True if busy on any connected calendar; False if free; None if unknown.

    Fail-soft: network/auth errors → None so auto-assign continues with other rules.
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
            connections.append({"provider": "google", "access_token": m.get("google_calendar_token") or m.get("access_token")})
        if m.get("microsoft_calendar_token"):
            connections.append({"provider": "microsoft", "access_token": m["microsoft_calendar_token"]})
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
        provider = str(conn.get("provider") or "").lower()
        result: Optional[bool] = None
        if provider == "google" and conn.get("access_token"):
            result = await _google_freebusy(conn["access_token"], start, end)
        elif provider == "microsoft" and conn.get("access_token"):
            result = await _microsoft_schedule(
                conn["access_token"], start, end, m.get("email") or conn.get("email")
            )
        elif provider == "apple":
            result = await _apple_caldav_busy(
                conn.get("caldav_url") or "",
                conn.get("caldav_password") or conn.get("app_password") or "",
                conn.get("caldav_username") or m.get("email") or "",
                start,
                end,
            )
        elif provider == "ics" and (conn.get("ics_url") or "").strip():
            result = await _ics_busy(conn["ics_url"].strip(), start, end)

        if result is True:
            return True
        if result is False:
            saw_free = True

    if saw_free:
        return False
    return None
