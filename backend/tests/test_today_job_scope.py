"""Today / job-detail visibility: default_vendor jobs assigned to Kam render."""
from datetime import date

from app.shared.job_scope import (
    KAM_MEMBER_ID,
    ORG_VENDOR_ID,
    job_visible_to,
    parse_scheduled_date,
    photographer_assignee_ids,
    pick_canonical_member,
    vendor_scope_ids,
)


KAM = {
    "id": KAM_MEMBER_ID,
    "vendor_id": KAM_MEMBER_ID,  # historical: member UUID used as vendor_id
    "role": "Owner",
    "title": "Owner",
    "email": "akashorter@icloud.com",
    "payout_mode": "owner_full",
}

DUPLICATE_IC = {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "vendor_id": ORG_VENDOR_ID,
    "role": "Photographer",
    "email": "akashorter@icloud.com",
}

PAID_BOOK_JOB = {
    "id": "job-paid-book",
    "vendor_id": ORG_VENDOR_ID,
    "assignee_id": KAM_MEMBER_ID,
    "scheduled_date": "2026-08-29",
    "status": "assigned",
    "address": "10 Main St, Mississauga",
}


def test_kam_aliases_include_canonical_id():
    aliases = photographer_assignee_ids(DUPLICATE_IC["id"], KAM)
    assert KAM_MEMBER_ID in aliases
    assert DUPLICATE_IC["id"] in aliases


def test_default_vendor_job_assigned_to_kam_is_visible_on_owner_session():
    # Member.vendor_id is Kam's UUID; job.vendor_id is default_vendor.
    assert job_visible_to(PAID_BOOK_JOB, KAM_MEMBER_ID, KAM, assigned_only=True)


def test_duplicate_login_row_still_sees_kam_assigned_job():
    # Auth user id is a different UUID created at OTP login; jobs are assigned to 0c3fa7c2.
    assert job_visible_to(PAID_BOOK_JOB, DUPLICATE_IC["id"], KAM, assigned_only=True)


def test_other_photographer_does_not_see_kams_job():
    ic = {"id": "ic-1", "vendor_id": ORG_VENDOR_ID, "role": "Photographer IC", "email": "ic@brxs.ca"}
    assert not job_visible_to(PAID_BOOK_JOB, "ic-1", ic, assigned_only=True)


def test_vendor_scope_always_includes_default_vendor():
    assert ORG_VENDOR_ID in vendor_scope_ids(KAM_MEMBER_ID, KAM)


def test_pick_canonical_prefers_0c3fa7c2():
    picked = pick_canonical_member([DUPLICATE_IC, KAM], actor_id=DUPLICATE_IC["id"], email=KAM["email"])
    assert picked["id"] == KAM_MEMBER_ID


def test_parse_scheduled_date_iso_and_ymd():
    assert parse_scheduled_date("2026-08-29") == date(2026, 8, 29)
    assert parse_scheduled_date("2026-08-29T18:00:00Z") == date(2026, 8, 29)
    assert parse_scheduled_date("") is None


def test_today_filter_would_render_assigned_job():
    scheduled = parse_scheduled_date(PAID_BOOK_JOB["scheduled_date"])
    assert scheduled == date(2026, 8, 29)
    assert job_visible_to(PAID_BOOK_JOB, KAM_MEMBER_ID, KAM)
