"""Vendor-facing copy: calendar connect drives auto-assign, not a hidden setting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_edu_copy_names_native_auto_assign_and_all_providers():
    from app.services.vendor_calendars import (
        UI_CONNECT_PROVIDERS,
        VENDOR_CALENDAR_EDU_COPY,
        VENDOR_CALENDAR_NUDGE,
        VENDOR_CALENDAR_USED_FOR_OFFERS,
        VENDOR_CALENDAR_WELCOME,
    )

    assert UI_CONNECT_PROVIDERS == ("google", "microsoft", "apple", "ics")
    assert "native auto-assign" in VENDOR_CALENDAR_EDU_COPY
    assert "Busy on any connected calendar" in VENDOR_CALENDAR_EDU_COPY
    assert "Manual hours" in VENDOR_CALENDAR_EDU_COPY
    assert "Coming Soon" not in VENDOR_CALENDAR_EDU_COPY
    assert VENDOR_CALENDAR_USED_FOR_OFFERS == "Used for job offers."
    assert VENDOR_CALENDAR_NUDGE == "Connect a calendar so we don’t double-book you."
    assert "Dispatch knows your availability for auto-assign" in VENDOR_CALENDAR_WELCOME


def test_calendars_endpoint_returns_education_copy():
    src = (ROOT / "app/routers/vendor_os.py").read_text()
    assert "VENDOR_CALENDAR_EDU_COPY" in src
    assert "used_for_offers" in src
    assert "VENDOR_CALENDAR_NUDGE" in src
    assert "Coming Soon" not in src


def test_welcome_approved_email_links_availability():
    from app.services.notifications import welcome_approved_email_html

    html = welcome_approved_email_html("https://go.brxs.ca/availability")
    assert "Connecting a calendar is how Dispatch knows your availability for auto-assign" in html
    assert "https://go.brxs.ca/availability" in html
    assert "Open Availability" in html
    assert "Coming Soon" not in html


def test_approve_sends_welcome_with_availability_link():
    admin = (ROOT / "app/routers/admin_ops.py").read_text()
    onboarding = (ROOT / "app/services/vendor_onboarding.py").read_text()
    assert "await _vo.send_onboarding_welcome(approved_row)" in admin
    assert "Open Availability to connect Google, Outlook, Apple, or an ICS feed" in onboarding
    assert "Dispatch checks calendar free/busy before offering jobs" in onboarding


def test_go_surfaces_carry_the_nudge():
    repo = ROOT.parent
    availability = (repo / "web/src/pages/AvailabilityPage.tsx").read_text()
    overview = (repo / "web/src/pages/OverviewPage.tsx").read_text()
    settings = (repo / "frontend/app/settings.tsx").read_text()
    today = (repo / "frontend/app/(tabs)/index.tsx").read_text()
    slides = (repo / "frontend/app/onboarding/slides.tsx").read_text()
    assert "CALENDAR_EDU_LINES" in availability
    assert "CALENDAR_USED_FOR_OFFERS" in availability
    assert "CalendarConnectNudge" in availability
    assert "CalendarConnectNudge" in overview
    assert "CALENDAR_EDU_LINES" in settings
    assert "CalendarConnectNudge" in settings
    assert "CalendarConnectNudge" in today
    assert "Connect a calendar so auto-assign only offers jobs that fit your real schedule" in slides
    assert "Coming Soon" not in availability
    assert "Coming Soon" not in settings
