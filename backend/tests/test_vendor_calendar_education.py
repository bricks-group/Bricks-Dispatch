"""Vendor-facing copy: calendar connect drives auto-assign, not a hidden setting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_edu_copy_explains_calendar_checks_in_plain_words():
    from app.services.vendor_calendars import (
        UI_CONNECT_PROVIDERS,
        VENDOR_CALENDAR_EDU_COPY,
        VENDOR_CALENDAR_NUDGE,
        VENDOR_CALENDAR_USED_FOR_OFFERS,
        VENDOR_CALENDAR_WELCOME,
    )

    assert UI_CONNECT_PROVIDERS == ("google", "microsoft", "apple", "ics")
    assert "avoid times you’ve already booked" in VENDOR_CALENDAR_EDU_COPY
    assert "Your working hours still apply" in VENDOR_CALENDAR_EDU_COPY
    assert "native auto-assign" not in VENDOR_CALENDAR_EDU_COPY
    assert "Coming Soon" not in VENDOR_CALENDAR_EDU_COPY
    assert VENDOR_CALENDAR_USED_FOR_OFFERS == "Your calendar helps us find a time that works."
    assert VENDOR_CALENDAR_NUDGE == "Jobs that fit your schedule"
    assert "avoid times you’ve already booked" in VENDOR_CALENDAR_WELCOME


def test_calendars_endpoint_returns_education_copy():
    src = (ROOT / "app/routers/vendor_os.py").read_text()
    assert "VENDOR_CALENDAR_EDU_COPY" in src
    assert "used_for_offers" in src
    assert "VENDOR_CALENDAR_NUDGE" in src
    assert "Coming Soon" not in src


def test_welcome_approved_email_links_availability():
    from app.services.notifications import welcome_approved_email_html

    html = welcome_approved_email_html("https://go.brxs.ca/availability")
    assert "Connect your calendar so we can avoid times you’ve already booked." in html
    assert "https://go.brxs.ca/availability" in html
    assert "Open Availability" in html
    assert "Coming Soon" not in html


def test_approve_sends_welcome_with_availability_link():
    admin = (ROOT / "app/routers/admin_ops.py").read_text()
    onboarding = (ROOT / "app/services/vendor_onboarding.py").read_text()
    assert "await _vo.send_onboarding_welcome(approved_row)" in admin
    assert "Open Availability to connect your calendar" in onboarding
    assert "We’ll avoid times you’ve already booked." in onboarding


def test_go_surfaces_carry_the_nudge():
    repo = ROOT.parent
    availability = (repo / "web/src/pages/AvailabilityPage.tsx").read_text()
    overview = (repo / "web/src/pages/OverviewPage.tsx").read_text()
    settings = (repo / "frontend/app/settings.tsx").read_text()
    today = (repo / "frontend/app/(tabs)/index.tsx").read_text()
    slides = (repo / "frontend/app/onboarding/slides.tsx").read_text()
    assert "CALENDAR_EDU_LINES" in availability
    assert "CALENDAR_PROVIDER_HINTS" in availability
    assert "CalendarConnectNudge" not in availability
    assert "CalendarConnectNudge" in overview
    assert "CALENDAR_EDU_LINES" in settings
    assert "CalendarConnectNudge" not in settings
    assert "CalendarConnectNudge" in today
    assert "Connect your calendar so we can avoid times you’ve already booked" in slides
    assert "Coming Soon" not in availability
    assert "Coming Soon" not in settings
