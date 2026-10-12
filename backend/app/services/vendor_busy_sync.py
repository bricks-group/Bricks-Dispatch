"""Periodic free/busy sync from photographers' linked calendars + per-day availability.

Linked calendars are how Bricks sees photographer availability (no manual days-off
screen). This module:
  * pulls busy *intervals* (times only, no titles) from Google FreeBusy, iCloud CalDAV
    and ICS feeds for the next SYNC_DAYS and stores them in ``vendor_busy_blocks``;
  * answers "is this photographer busy in [start, end)" from the cache (auto-assign);
  * aggregates per-day availability for booking calendars (www / Companion).

Everything fails soft: an unknown calendar never blocks a photographer.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

SYNC_DAYS = 62
DEFAULT_TZ = "America/Toronto"
MIN_SHOOT_MIN = 90  # a day is bookable if a photographer has a 90-minute free gap
ASSIGNABLE_ONBOARDING = {"approved", "active"}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        return _utc(datetime.fromisoformat(str(v).replace("Z", "+00:00")))
    except Exception:
        return None


# ── ICS parsing (intervals) ───────────────────────────────────────────────────

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _ics_dt(prop: str, value: str, default_tz: str) -> tuple[Optional[datetime], bool]:
    """Return (datetime, is_all_day)."""
    value = value.strip()
    m = re.search(r"TZID=([^;:]+)", prop)
    try:
        if "VALUE=DATE" in prop or re.fullmatch(r"\d{8}", value):
            d = datetime.strptime(value[:8], "%Y%m%d")
            return d.replace(tzinfo=ZoneInfo(default_tz)), True
        naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        if value.endswith("Z"):
            return naive.replace(tzinfo=timezone.utc), False
        tzname = m.group(1) if m else default_tz
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = ZoneInfo(default_tz)
        return naive.replace(tzinfo=tz), False
    except Exception:
        return None, False


def parse_ics_intervals(text: str, start: datetime, end: datetime, default_tz: str = DEFAULT_TZ) -> list[tuple[datetime, datetime]]:
    """Busy intervals overlapping [start, end). Skips TRANSP:TRANSPARENT / cancelled.

    Recurring events: only the first instance is used plus simple weekly/daily RRULEs.
    """
    s0, e0 = _utc(start), _utc(end)
    out: list[tuple[datetime, datetime]] = []
    lines = _unfold(text)
    ev: Optional[dict] = None
    for line in lines:
        if line.startswith("BEGIN:VEVENT"):
            ev = {}
            continue
        if line.startswith("END:VEVENT") and ev is not None:
            if ev.get("transp") == "TRANSPARENT" or ev.get("status") == "CANCELLED" or not ev.get("ds"):
                ev = None
                continue
            ds, all_day = ev["ds"]
            de = ev.get("de", (None, False))[0]
            if ds is None:
                ev = None
                continue
            if de is None:
                de = ds + (timedelta(days=1) if all_day else timedelta(hours=1))
            dur = de - ds
            starts = [ds]
            rrule = ev.get("rrule") or ""
            fm = re.search(r"FREQ=(DAILY|WEEKLY)", rrule)
            if fm:
                step = timedelta(days=1 if fm.group(1) == "DAILY" else 7)
                iv = re.search(r"INTERVAL=(\d+)", rrule)
                step *= int(iv.group(1)) if iv else 1
                cnt = re.search(r"COUNT=(\d+)", rrule)
                until = re.search(r"UNTIL=(\d{8}(T\d{6}Z?)?)", rrule)
                until_dt = _ics_dt("", until.group(1), DEFAULT_TZ)[0] if until else None
                limit = int(cnt.group(1)) if cnt else 400
                cur = ds
                starts = []
                for _ in range(limit):
                    if _utc(cur) >= e0 or (until_dt and _utc(cur) > _utc(until_dt)):
                        break
                    starts.append(cur)
                    cur = cur + step
            for st in starts:
                a, b = _utc(st), _utc(st + dur)
                if a < e0 and b > s0:
                    out.append((max(a, s0), min(b, e0)))
            ev = None
            continue
        if ev is None or ":" not in line:
            continue
        prop, value = line.split(":", 1)
        key = prop.split(";", 1)[0].upper()
        if key == "DTSTART":
            ev["ds"] = _ics_dt(prop, value, default_tz)
        elif key == "DTEND":
            ev["de"] = _ics_dt(prop, value, default_tz)
        elif key == "TRANSP":
            ev["transp"] = value.strip().upper()
        elif key == "STATUS":
            ev["status"] = value.strip().upper()
        elif key == "RRULE":
            ev["rrule"] = value.strip().upper()
    return merge_intervals(out)


def merge_intervals(items: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    srt = sorted((a, b) for a, b in items if b > a)
    merged: list[tuple[datetime, datetime]] = []
    for a, b in srt:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


# ── Provider fetchers ─────────────────────────────────────────────────────────

async def google_busy_intervals(token: str, start: datetime, end: datetime):
    """List of intervals, None on failure, 'auth' on 401."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers={"Authorization": f"Bearer {token}"},
                json={"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": "primary"}]},
            )
        if resp.status_code == 401:
            return "auth"
        if resp.status_code >= 400:
            return None
        cals = resp.json().get("calendars") or {}
        primary = cals.get("primary") or next(iter(cals.values()), {})
        if primary.get("errors"):
            return None
        out = []
        for b in primary.get("busy") or []:
            a, z = _parse_ts(b.get("start")), _parse_ts(b.get("end"))
            if a and z:
                out.append((a, z))
        return merge_intervals(out)
    except Exception as e:
        logger.debug("google busy sync soft-fail: %s", e)
        return None


async def ics_busy_intervals(url: str, start: datetime, end: datetime):
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400 or "BEGIN:VCALENDAR" not in resp.text:
            return None
        return parse_ics_intervals(resp.text, start, end)
    except Exception as e:
        logger.debug("ics busy sync soft-fail: %s", e)
        return None


async def caldav_busy_intervals(url: str, username: str, password: str, start: datetime, end: datetime):
    if not url or not password:
        return None
    body = f"""<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><C:calendar-data/></D:prop>
  <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">
    <C:time-range start="{_utc(start).strftime('%Y%m%dT%H%M%SZ')}" end="{_utc(end).strftime('%Y%m%dT%H%M%SZ')}"/>
  </C:comp-filter></C:comp-filter></C:filter>
</C:calendar-query>"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                "REPORT", url, content=body,
                headers={"Content-Type": "application/xml; charset=utf-8", "Depth": "1"},
                auth=(username or "user", password),
            )
        if resp.status_code >= 400:
            return None
        texts: list[str] = []
        try:
            root = ET.fromstring(resp.text)
            for el in root.iter():
                if el.tag.endswith("calendar-data") and el.text:
                    texts.append(el.text)
        except ET.ParseError:
            texts = [resp.text]
        out: list[tuple[datetime, datetime]] = []
        for t in texts:
            out.extend(parse_ics_intervals(t, start, end))
        return merge_intervals(out)
    except Exception as e:
        logger.debug("caldav busy sync soft-fail: %s", e)
        return None


async def fetch_connection_intervals(conn: dict, member: dict, start: datetime, end: datetime, db=None):
    from .vendor_calendars import ensure_fresh_access_token, refresh_google_access_token, _persist_refreshed_tokens

    provider = str(conn.get("provider") or "").lower()
    if provider == "google":
        token = await ensure_fresh_access_token(conn, db=db)
        if not token:
            return None
        res = await google_busy_intervals(token, start, end)
        if res == "auth" and conn.get("refresh_token"):
            payload = await refresh_google_access_token(conn["refresh_token"])
            if payload and payload.get("access_token"):
                updated = await _persist_refreshed_tokens(db, conn, payload)
                res = await google_busy_intervals(updated.get("access_token") or "", start, end)
        return None if res == "auth" else res
    if provider == "apple":
        return await caldav_busy_intervals(
            conn.get("caldav_url") or "",
            conn.get("caldav_username") or member.get("email") or "",
            conn.get("caldav_password") or conn.get("app_password") or "",
            start, end,
        )
    if provider == "ics" and (conn.get("ics_url") or "").strip():
        return await ics_busy_intervals(conn["ics_url"], start, end)
    return None


# ── Sync ──────────────────────────────────────────────────────────────────────

async def sync_member(db, member_id: str, member: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    from .vendor_calendars import _load_connections

    now = _utc(now or datetime.now(timezone.utc))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = start + timedelta(days=SYNC_DAYS + 1)
    conns = await _load_connections(db, member_id)
    result = {"member_id": member_id, "providers": {}}
    for conn in conns:
        provider = str(conn.get("provider") or "").lower()
        intervals = await fetch_connection_intervals(conn, member or {}, start, end, db=db)
        status = "ok" if intervals is not None else "error"
        result["providers"][provider] = {"status": status, "blocks": len(intervals or [])}
        if intervals is None:
            # Keep the previous blocks; a flaky feed must not mark someone free.
            await _mark(db, member_id, provider, "error")
            continue
        try:
            await db.table("vendor_busy_blocks").delete().eq("member_id", member_id).eq("provider", provider).execute()
            if intervals:
                rows = [
                    {"member_id": member_id, "provider": provider, "starts_at": _iso(a), "ends_at": _iso(b), "synced_at": _iso(now)}
                    for a, b in intervals[:1000]
                ]
                await db.table("vendor_busy_blocks").insert(rows).execute()
            await _mark(db, member_id, provider, "ok", now)
        except Exception as e:
            logger.warning("busy block write failed for %s/%s: %s", member_id, provider, e)
            result["providers"][provider]["status"] = "error"
    return result


async def _mark(db, member_id: str, provider: str, status: str, now: Optional[datetime] = None) -> None:
    patch: dict = {"last_sync_status": status}
    if now:
        patch["last_synced_at"] = _iso(now)
    try:
        await db.table("vendor_calendar_connections").update(patch).eq("member_id", member_id).eq("provider", provider).execute()
    except Exception:
        pass


async def sync_all(db) -> dict:
    try:
        res = await db.table("vendor_calendar_connections").select("member_id").execute()
        ids = sorted({str(r.get("member_id")) for r in (res.data or []) if r.get("member_id")})
    except Exception as e:
        logger.warning("sync_all: connections read failed: %s", e)
        return {"ok": False, "members": 0}
    out = []
    for mid in ids:
        try:
            out.append(await sync_member(db, mid))
        except Exception as e:
            logger.warning("sync_member %s failed: %s", mid, e)
    return {"ok": True, "members": len(ids), "results": out}


async def cached_busy(db, member_id: str, start: datetime, end: datetime) -> Optional[bool]:
    """True if a synced busy block overlaps [start, end); None otherwise (fail-soft)."""
    try:
        res = await (
            db.table("vendor_busy_blocks").select("id")
            .eq("member_id", member_id).lt("starts_at", _iso(end)).gt("ends_at", _iso(start))
            .limit(1).execute()
        )
        return True if res.data else None
    except Exception:
        return None


# ── Per-day availability (booking calendars) ──────────────────────────────────

def _hm(v: Any, default: str) -> time:
    try:
        h, m = str(v or default).split(":")[:2]
        return time(int(h), int(m))
    except Exception:
        h, m = default.split(":")
        return time(int(h), int(m))


def _area_match(member: dict, area: str) -> bool:
    if not area:
        return True
    areas = [str(a).lower() for a in (member.get("service_areas") or [])] + [str(member.get("home_city") or "").lower()]
    areas = [a for a in areas if a]
    if not areas:
        return True  # no areas set = covers everywhere (matches dispatch fallback)
    a = area.lower()
    return any(x in a or a in x or x == "gta" for x in areas)


def day_free_minutes(day: date, tz: ZoneInfo, avail: dict, busy: list[tuple[datetime, datetime]], jobs_that_day: int, now: datetime) -> int:
    """Longest free gap (minutes) inside working hours, 0 if unavailable."""
    if avail.get("available") is False:
        return 0
    dows = avail.get("days_of_week")
    if dows:
        names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        wd = day.weekday()
        ok = any(str(x).lower()[:3] == names[wd] or str(x) == str(wd) or str(x) == str((wd + 1) % 7) for x in dows)
        if not ok:
            return 0
    cap = int(avail.get("jobs_per_day") or 0)
    if cap and jobs_that_day >= cap:
        return 0
    ws = datetime.combine(day, _hm(avail.get("work_start"), "08:00"), tz)
    we = datetime.combine(day, _hm(avail.get("work_end"), "17:00"), tz)
    ws = max(_utc(ws), now)
    we = _utc(we)
    if we <= ws:
        return 0
    blocks = list(busy)
    for w in avail.get("blocked_windows") or []:
        if isinstance(w, dict):
            a, b = _parse_ts(w.get("start")), _parse_ts(w.get("end"))
            if a and b:
                blocks.append((a, b))
    best, cur = 0, ws
    for a, b in merge_intervals(blocks):
        if b <= cur:
            continue
        if a >= we:
            break
        best = max(best, int((min(a, we) - cur).total_seconds() // 60))
        cur = max(cur, b)
    if cur < we:
        best = max(best, int((we - cur).total_seconds() // 60))
    return best


async def availability_days(db, start: date, days: int, area: str = "", tz_name: str = DEFAULT_TZ, now: Optional[datetime] = None) -> dict:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz, tz_name = ZoneInfo(DEFAULT_TZ), DEFAULT_TZ
    now = _utc(now or datetime.now(timezone.utc))
    days = max(1, min(days, 92))
    end_day = start + timedelta(days=days)
    span_a = _utc(datetime.combine(start, time(0), tz))
    span_b = _utc(datetime.combine(end_day, time(0), tz))

    mres = await db.table("vendor_members").select("id, role, is_active, onboarding_status, service_areas, home_city").execute()
    members = [
        m for m in (mres.data or [])
        if m.get("is_active") and str(m.get("onboarding_status") or "").lower() in ASSIGNABLE_ONBOARDING
        and str(m.get("role") or "").lower() not in ("applicant", "editor", "admin")
        and _area_match(m, area)
    ]
    ids = [str(m["id"]) for m in members]
    if not ids:
        return {"ok": True, "source": "none", "tz": tz_name, "days": []}

    from .vendor_availability_gate import load_availability_by_member
    avail = await load_availability_by_member(db, ids)

    busy: dict[str, list] = {i: [] for i in ids}
    try:
        bres = await (
            db.table("vendor_busy_blocks").select("member_id, starts_at, ends_at")
            .in_("member_id", ids).lt("starts_at", _iso(span_b)).gt("ends_at", _iso(span_a))
            .limit(5000).execute()
        )
        for r in bres.data or []:
            a, b = _parse_ts(r.get("starts_at")), _parse_ts(r.get("ends_at"))
            if a and b:
                busy.setdefault(str(r["member_id"]), []).append((a, b))
    except Exception as e:
        logger.debug("busy blocks read soft-fail: %s", e)

    jobs: dict[tuple[str, str], int] = {}
    try:
        jres = await (
            db.table("jobs").select("assignee_id, scheduled_date, status")
            .in_("assignee_id", ids)
            .in_("status", ["assigned", "in_progress", "pending_assignment"])
            .gte("scheduled_date", start.isoformat()).lt("scheduled_date", end_day.isoformat())
            .limit(2000).execute()
        )
        for r in jres.data or []:
            k = (str(r.get("assignee_id")), str(r.get("scheduled_date") or "")[:10])
            jobs[k] = jobs.get(k, 0) + 1
    except Exception as e:
        logger.debug("jobs read soft-fail: %s", e)

    today_local = now.astimezone(tz).date()
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        if d <= today_local:
            out.append({"date": d.isoformat(), "available": False, "open": 0, "reason": "past"})
            continue
        open_count = 0
        for mid in ids:
            mins = day_free_minutes(d, tz, avail.get(mid) or {}, busy.get(mid, []), jobs.get((mid, d.isoformat()), 0), now)
            if mins >= MIN_SHOOT_MIN:
                open_count += 1
        out.append({
            "date": d.isoformat(),
            "available": open_count > 0,
            "open": open_count,
            **({} if open_count else {"reason": "booked"}),
        })
    return {"ok": True, "source": "live", "tz": tz_name, "photographers": len(ids), "days": out}
