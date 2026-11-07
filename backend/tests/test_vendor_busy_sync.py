from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.vendor_busy_sync import day_free_minutes, merge_intervals, parse_ics_intervals

TZ = ZoneInfo("America/Toronto")
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Toronto:20261020T090000
DTEND;TZID=America/Toronto:20261020T120000
SUMMARY:Private
END:VEVENT
BEGIN:VEVENT
DTSTART:20261021T130000Z
DTEND:20261021T140000Z
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261022
DTEND;VALUE=DATE:20261023
END:VEVENT
BEGIN:VEVENT
DTSTART:20261019T140000Z
DTEND:20261019T150000Z
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_intervals_tz_transparent_allday_rrule():
    s = datetime(2026, 10, 18, tzinfo=timezone.utc)
    iv = parse_ics_intervals(ICS, s, s + timedelta(days=30))
    starts = [a for a, _ in iv]
    assert datetime(2026, 10, 20, 13, tzinfo=timezone.utc) in starts  # 9am EDT
    assert not any(a == datetime(2026, 10, 21, 13, tzinfo=timezone.utc) for a in starts)
    assert datetime(2026, 10, 22, 4, tzinfo=timezone.utc) in starts  # all-day local
    assert sum(1 for a in starts if a.hour == 14 and a.weekday() == 0) == 3


def test_day_free_minutes_busy_and_capacity():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    d = date(2026, 10, 20)
    avail = {"available": True, "work_start": "08:00", "work_end": "17:00", "jobs_per_day": 2}
    assert day_free_minutes(d, TZ, avail, [], 0, now) == 540
    allday = [(datetime(2026, 10, 20, 4, tzinfo=timezone.utc), datetime(2026, 10, 21, 4, tzinfo=timezone.utc))]
    assert day_free_minutes(d, TZ, avail, allday, 0, now) == 0
    morning = [(datetime(2026, 10, 20, 12, tzinfo=timezone.utc), datetime(2026, 10, 20, 19, tzinfo=timezone.utc))]
    assert day_free_minutes(d, TZ, avail, morning, 0, now) == 120
    assert day_free_minutes(d, TZ, avail, [], 2, now) == 0
    assert day_free_minutes(d, TZ, {**avail, "available": False}, [], 0, now) == 0


def test_merge():
    a = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h = timedelta(hours=1)
    assert merge_intervals([(a, a + h), (a + h / 2, a + 2 * h)]) == [(a, a + 2 * h)]
