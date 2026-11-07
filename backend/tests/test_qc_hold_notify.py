"""Owner hears about QC holds: SMS + raphael@brxs.ca email with held files."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.qc_hold_notify import (
    QC_HOLD_ADMIN_EMAIL,
    held_rows,
    notify_owner_qc_hold,
    qc_hold_email_body,
    qc_hold_sms_body,
)


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return _Res(self._data)


class _DB:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return _Q(self._t.get(name))


UPLOADS = [
    {"id": "u1", "filename": "kitchen.jpg", "qc_status": "rejected",
     "qc_notes": "Auto-edit failed — unedited original held."},
    {"id": "u2", "filename": "living.jpg", "qc_status": "passed", "qc_notes": ""},
    {"id": "u3", "filename": "bath.jpg", "qc_status": "rejected", "qc_notes": "Blurry"},
]
JOB = {"id": "j1", "address": "10 King St", "scheduled_date": "2026-10-10",
       "booking_id": "bk_abcdef123456"}


def _db():
    return _DB({"uploads": UPLOADS, "jobs": JOB, "vendor_members": {"phone": "+15555550100"}})


def test_held_rows_filters_rejected_and_ids():
    assert [u["id"] for u in held_rows(UPLOADS)] == ["u1", "u3"]
    assert [u["id"] for u in held_rows(UPLOADS, only_ids=["u3", "u2"])] == ["u3"]
    assert held_rows(UPLOADS, only_ids=[]) == []


def test_copy_is_plain_and_has_no_email_in_sms():
    sms = qc_hold_sms_body("10 King St", 2)
    assert "2 listing photos" in sms and "@" not in sms
    assert "1 listing photo " in qc_hold_sms_body("", 1)
    body = qc_hold_email_body(JOB, held_rows(UPLOADS))
    assert "• kitchen.jpg: Auto-edit failed" in body
    assert "• bath.jpg: Blurry" in body
    assert "bk_abcde" in body and "bk_abcdef123456" not in body
    assert "not sent to the client" in body


@pytest.mark.asyncio
async def test_notify_sends_sms_and_admin_email():
    email = AsyncMock(return_value=True)
    with patch("app.notifications.send_dispatch_sms", return_value=True) as sms, \
         patch("app.services.notifications.send_email_via_resend", email):
        ok = await notify_owner_qc_hold(_db(), "j1", {"rejected": 2})
    assert ok is True
    sms.assert_called_once()
    assert sms.call_args[0][0] == "+15555550100"
    assert email.await_args.kwargs["to"] == QC_HOLD_ADMIN_EMAIL == "raphael@brxs.ca"
    assert "2 listing photos" in email.await_args.kwargs["subject"]


@pytest.mark.asyncio
async def test_no_notice_without_rejections_or_outside_only_ids():
    email = AsyncMock(return_value=True)
    with patch("app.notifications.send_dispatch_sms", return_value=True) as sms, \
         patch("app.services.notifications.send_email_via_resend", email):
        assert await notify_owner_qc_hold(_db(), "j1", {"rejected": 0}) is False
        assert await notify_owner_qc_hold(_db(), "j1", None) is False
        assert await notify_owner_qc_hold(_db(), "j1", {"rejected": 2}, only_ids=["u2"]) is False
    sms.assert_not_called()
    email.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_raises_when_channels_fail():
    with patch("app.notifications.send_dispatch_sms", side_effect=RuntimeError("x")), \
         patch("app.services.notifications.send_email_via_resend",
               AsyncMock(side_effect=RuntimeError("y"))):
        assert await notify_owner_qc_hold(_db(), "j1", {"rejected": 1}) is False
