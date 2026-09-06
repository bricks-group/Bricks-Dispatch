"""Unit coverage for booked-clock when-line (no DB)."""
from app.services.notifications import format_shoot_window, format_when_line


def test_format_shoot_window_with_confirmed_arrive():
    s = format_shoot_window({
        "preferred_window": "afternoon",
        "time_window_start": "13:30",
        "time_window_end": "16:00",
        "confirmed_arrive_at": "13:30",
    })
    assert s == "1:30 PM ET"
    assert "Afternoon" not in s
    assert "–" not in s


def test_format_shoot_window_booked_start_not_range():
    s = format_shoot_window({
        "preferred_window": "morning",
        "time_window_start": "09:00",
        "time_window_end": "12:00",
    })
    assert s == "9:00 AM ET"
    assert "Morning" not in s
    assert "–" not in s


def test_format_when_line_booked_clock():
    assert format_when_line({
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "16:00",
        "preferred_window": "afternoon",
    }) == "Fri, Sep 25 · 2:00 PM ET"


def test_format_when_line_prefers_confirmed_arrive():
    assert format_when_line({
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "16:00",
        "confirmed_arrive_at": "14:30",
        "preferred_window": "afternoon",
    }) == "Fri, Sep 25 · 2:30 PM ET"
