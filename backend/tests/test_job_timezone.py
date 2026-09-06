"""Canada-wide job timezone: explicit IANA, province fallback, silent Toronto default."""
from app.services.notifications import (
    _clock_hhmm,
    format_shoot_window,
    format_when_line,
)
from app.shared.job_timezone import (
    DEFAULT_IANA,
    infer_province_code,
    resolve_job_iana,
    stamp_job_timezone,
    tz_abbrev,
)


def test_explicit_timezone_wins():
    job = {
        "timezone": "America/Vancouver",
        "state": "ON",
        "address": "10 Main St, Toronto, ON",
        "time_window_start": "14:00",
        "scheduled_date": "2026-09-25",
    }
    assert resolve_job_iana(job) == "America/Vancouver"
    assert tz_abbrev(job) == "PT"
    assert format_shoot_window(job) == "2:00 PM PT"
    assert format_when_line(job) == "Fri, Sep 25 · 2:00 PM PT"


def test_province_fallback_bc():
    job = {"state": "BC", "time_window_start": "14:00", "scheduled_date": "2026-09-25"}
    assert resolve_job_iana(job) == "America/Vancouver"
    assert format_when_line(job) == "Fri, Sep 25 · 2:00 PM PT"


def test_address_fallback_vancouver():
    job = {
        "address": "200 Granville St, Vancouver, BC",
        "time_window_start": "14:00",
        "scheduled_date": "2026-09-25",
    }
    assert resolve_job_iana(job) == "America/Vancouver"
    assert format_shoot_window(job) == "2:00 PM PT"


def test_postal_fallback_v():
    assert resolve_job_iana({"zip_code": "V6B 1A1"}) == "America/Vancouver"
    assert resolve_job_iana({"postal_code": "T2P 0A1"}) == "America/Edmonton"
    assert tz_abbrev({"postal_code": "T2P 0A1", "scheduled_date": "2026-09-25"}) == "MT"


def test_unknown_defaults_toronto_silently():
    job = {"time_window_start": "14:00", "scheduled_date": "2026-09-25"}
    assert resolve_job_iana(job) == DEFAULT_IANA
    assert format_when_line(job) == "Fri, Sep 25 · 2:00 PM ET"
    assert "America/Toronto" not in format_when_line(job)


def test_invalid_timezone_falls_through_to_province():
    job = {"timezone": "Not/AZone", "state": "AB", "time_window_start": "09:00"}
    assert resolve_job_iana(job) == "America/Edmonton"
    assert format_shoot_window(job) == "9:00 AM MT"


def test_alias_pt():
    assert resolve_job_iana({"timezone": "PT"}) == "America/Vancouver"
    assert resolve_job_iana({"timezone": "Pacific"}) == "America/Vancouver"


def test_iso_utc_converts_in_job_zone():
    # 13:00Z on 2026-09-20 is 09:00 ET / 06:00 PT.
    assert _clock_hhmm("2026-09-20T13:00:00.000Z") == "09:00"
    van = {"timezone": "America/Vancouver"}
    assert _clock_hhmm("2026-09-20T13:00:00.000Z", van) == "06:00"
    assert format_shoot_window({
        "time_window_start": "2026-09-20T13:00:00.000Z",
        "timezone": "America/Vancouver",
        "scheduled_date": "2026-09-20",
    }) == "6:00 AM PT"


def test_bare_hhmm_not_converted():
    assert _clock_hhmm("14:00", {"timezone": "America/Vancouver"}) == "14:00"
    assert format_shoot_window({
        "time_window_start": "14:00",
        "timezone": "America/Vancouver",
    }) == "2:00 PM PT"


def test_stamp_fills_state_from_address_not_timezone_column():
    stamped = stamp_job_timezone({
        "address": "200 Granville St, Vancouver, BC",
        "time_window_start": "14:00",
    })
    assert stamped["timezone"] == "America/Vancouver"
    assert stamped["state"] == "BC"


def test_infer_province_empty_when_unknown():
    assert infer_province_code({}) == ""
    assert infer_province_code({"address": "10 Main St"}) == ""


def test_user_facing_strings_never_say_america_toronto():
    for job in (
        {"time_window_start": "14:00", "scheduled_date": "2026-09-25"},
        {"state": "BC", "time_window_start": "14:00", "scheduled_date": "2026-09-25"},
        {"timezone": "America/Vancouver", "time_window_start": "14:00"},
    ):
        text = format_when_line(job)
        assert "America/Toronto" not in text
        assert "America/Vancouver" not in text
