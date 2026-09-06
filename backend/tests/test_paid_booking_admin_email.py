"""Admin 'new paid booking' email: branded card, short subject, no raw UUIDs."""
from pathlib import Path

from app.services.notifications import (
    _EMAIL_FONT_STACK,
    _EMAIL_LOGO_URL,
    _build_html_email,
    _paid_booking_admin_email_html,
    format_package_line,
    format_when_line,
    paid_booking_admin_subject,
    short_booking_ref,
)

ROOT = Path(__file__).resolve().parents[1]
BOOKING_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
LONG_ADDRESS = (
    "1847 Lakeshore Boulevard West, Unit 2402, Etobicoke, Ontario M8V 0A1, Canada"
)


def test_short_booking_ref_is_eight_chars_never_full_uuid():
    assert short_booking_ref(BOOKING_UUID) == "a1b2c3d4"
    assert BOOKING_UUID not in short_booking_ref(BOOKING_UUID)
    assert short_booking_ref("") == "n/a"
    assert short_booking_ref(None) == "n/a"
    assert short_booking_ref("bk_99") == "bk_99"


def test_paid_booking_subject_examples():
    # Before: "New paid booking: 1847 Lakeshore Boulevard West, Unit 2402, …"
    # After:  "New booking paid · {address truncated ~60}"
    subject = paid_booking_admin_subject(LONG_ADDRESS)
    assert subject.startswith("New booking paid · ")
    assert "New paid booking" not in subject
    assert BOOKING_UUID not in subject
    addr_part = subject.split(" · ", 1)[1]
    assert len(addr_part) <= 61  # 60 + ellipsis
    assert not addr_part.endswith("Canada")
    assert "…" in addr_part or len(LONG_ADDRESS) <= 60
    assert " " not in addr_part[-2:] or addr_part.endswith("…")


def test_paid_booking_subject_short_address_unchanged():
    assert paid_booking_admin_subject("10 Main St") == "New booking paid · 10 Main St"


def test_paid_booking_subject_never_uses_uuid_or_emoji():
    assert paid_booking_admin_subject(BOOKING_UUID) == "New booking paid"
    assert paid_booking_admin_subject("") == "New booking paid"
    assert paid_booking_admin_subject(None) == "New booking paid"
    assert "🎉" not in paid_booking_admin_subject("10 Main")
    assert "📧" not in paid_booking_admin_subject("10 Main")


def test_paid_booking_html_uses_brand_card_and_rows():
    html = _paid_booking_admin_email_html(
        address="10 Main St",
        package="Essentials ($399)",
        when="Fri, Sep 25 · 2:00 PM ET",
        assignee="unassigned",
        booking_ref="a1b2c3d4",
    )
    assert "bricks" in html
    assert _EMAIL_LOGO_URL in html
    assert _EMAIL_FONT_STACK in html
    assert "#D2542B" in html or "#d2542b" in html
    assert "#f4f1eb" in html
    assert "New booking paid" in html
    assert "Address" in html and "10 Main St" in html
    assert "Package" in html and "Essentials ($399)" in html
    assert "When" in html and "Fri, Sep 25 · 2:00 PM ET" in html
    assert "Afternoon" not in html
    assert "12:00–16:00" not in html
    assert "Assignee" in html and "unassigned" in html
    assert "Booking ref" in html and "a1b2c3d4" in html
    assert BOOKING_UUID not in html
    assert "<tr>" in html and "Address" in html


def test_paid_booking_html_escapes_user_fields():
    html = _paid_booking_admin_email_html(
        address='<img src=x onerror=alert(1)>',
        package="A & B <script>",
        when="now",
        assignee="x",
        booking_ref="abcd1234",
    )
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "&amp;" in html
    assert "&lt;script&gt;" in html


def test_build_html_email_escapes_then_converts_newlines():
    html = _build_html_email("Hi <b>x</b>", "Line 1\nLine 2 & more")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html
    assert "Line 1<br>Line 2 &amp; more" in html
    assert "Line 1\nLine 2" not in html


def test_dispatch_sends_branded_html_not_blob_subject():
    src = (ROOT / "app/routers/dispatch.py").read_text()
    assert "email_html=_paid_booking_admin_email_html" in src
    assert "email_subject=paid_booking_admin_subject" in src
    assert "format_when_line" in src
    assert "format_package_line" in src
    assert 'f"New paid booking: {job_data.get(' not in src
    assert "short_booking_ref" in src
    # In-app body stays plain text
    assert "New job from Booking.\\n" in src or 'f"New job from Booking.\\n"' in src


def test_paid_booking_when_line_is_booked_clock_not_window():
    job = {
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "time_window_end": "16:00",
        "preferred_window": "afternoon",
    }
    when = format_when_line(job)
    assert when == "Fri, Sep 25 · 2:00 PM ET"
    assert "Afternoon" not in when
    assert "–" not in when
    html = _paid_booking_admin_email_html(
        address="10 Main St",
        package="Showcase — HDR photo + Drone + Cinematic reel + Virtual tour + Floor plan ($599)",
        when=when,
        assignee="unassigned",
        booking_ref="a1b2c3d4",
    )
    assert "Fri, Sep 25 · 2:00 PM ET" in html
    assert "Afternoon" not in html


def test_paid_booking_when_line_uses_confirmed_arrive_when_set():
    assert format_when_line({
        "scheduled_date": "2026-09-25",
        "time_window_start": "14:00",
        "confirmed_arrive_at": "14:15",
    }) == "Fri, Sep 25 · 2:15 PM ET"


def test_paid_booking_when_line_legacy_preferred_window_fills_start_only():
    when = format_when_line({
        "scheduled_date": "2026-09-25",
        "preferred_window": "afternoon",
    })
    assert when == "Fri, Sep 25 · 12:00 PM ET"
    assert "Afternoon" not in when
    assert "12:00–16:00" not in when


def test_format_package_line_expands_showcase():
    assert format_package_line("Showcase") == (
        "Showcase — HDR photo + Drone + Cinematic reel + Virtual tour + Floor plan"
    )
    already = "Showcase — HDR photo + Drone + Cinematic reel + Virtual tour + Floor plan"
    assert format_package_line(already) == already
    assert format_package_line(None, "showcase") == already
