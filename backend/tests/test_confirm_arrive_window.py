"""Unit coverage for arrive-confirm formatting (no DB)."""
from app.services.notifications import format_shoot_window


def test_format_shoot_window_with_confirmed_arrive():
    s = format_shoot_window({
        "preferred_window": "afternoon",
        "time_window_start": "13:30",
        "time_window_end": "16:00",
        "confirmed_arrive_at": "13:30",
    })
    assert "Afternoon" in s
    assert "13:30" in s
    assert "arrives" in s.lower()


def test_format_shoot_window_without_confirm_keeps_range():
    s = format_shoot_window({
        "preferred_window": "morning",
        "time_window_start": "09:00",
        "time_window_end": "12:00",
    })
    assert s == "Morning · 09:00–12:00 ET"
