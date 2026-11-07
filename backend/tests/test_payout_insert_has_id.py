"""payouts.id is text NOT NULL with no DB default, so the insert must carry an id.
Without it every first payout failed 23502 after delivery (E2E, 2026-11-02).
Stripe is mocked: no live charges."""
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from app.services import payout_service as ps


def test_new_payout_record_has_uuid_id():
    job = {"id": "job-1", "vendor_id": "default_vendor", "assignee_id": "member_1",
           "booking_id": "bk_1", "rack_price": 225, "package_id": "hdr_photo"}
    inserted = {}

    async def ins(db, rec):
        inserted.update(rec)
        return dict(rec)

    with patch.object(ps, "_db_select_job", AsyncMock(return_value=job)), \
         patch.object(ps, "_db_select_vendor_member", AsyncMock(return_value={"tier": 2})), \
         patch.object(ps, "_db_select_payout_by_job", AsyncMock(return_value=None)), \
         patch.object(ps, "_db_insert_payout", side_effect=ins), \
         patch.object(ps, "_db_update_payout", AsyncMock()), \
         patch.object(ps, "_db_update_job_payout_amount", AsyncMock()), \
         patch.object(ps, "get_fixed_payout_amount", AsyncMock(return_value=7150)), \
         patch("app.services.vendor_payout_rate_card.addon_payout_cents", return_value=0), \
         patch("app.services.client_service_payouts.set_payout_cents", return_value=None), \
         patch("app.services.commercial_jobs.is_employee", return_value=False), \
         patch("app.shared.vendor_identity.is_owner_member", return_value=False), \
         patch("app.services.stripe_connect_service.handle_payout_to_vendor",
               AsyncMock(return_value="tr_test_1")):
        result = asyncio.run(ps.trigger_payout_for_job(object(), "job-1"))

    uuid.UUID(inserted["id"])
    assert result["payout_id"] == inserted["id"]
    assert result["status"] == "paid"
