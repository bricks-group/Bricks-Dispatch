"""
E2E tests for the Bricks Dispatch payout pipeline.

Covers:
  - Payout amount calculation (Tier A/B/C × package price)
  - trigger_payout_for_job: happy path, idempotency, missing job, zero amount
  - Stripe Connect stub behaviour
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.payout_service import calculate_payout_amount, TIER_PERCENTAGES, DEFAULT_TIER_PERCENTAGE


# ── Unit: payout amount calculation ──────────────────────────────────────────

class TestCalculatePayoutAmount:
    def test_tier_c_entry_standard(self):
        # Tier C/entry (35%) of $299 → 10465 cents
        assert calculate_payout_amount(299, 1) == int(299 * 100 * 0.35)

    def test_tier_b_core_premium(self):
        # Tier B/core (45%) of $599 → 26955 cents
        assert calculate_payout_amount(599, 2) == int(599 * 100 * 0.45)

    def test_tier_a_elite_luxury(self):
        # Tier A/elite (55%) of $999 → 54945 cents
        assert calculate_payout_amount(999, 3) == int(999 * 100 * 0.55)

    def test_zero_price_returns_zero(self):
        assert calculate_payout_amount(0, 1) == 0
        assert calculate_payout_amount(None, 1) == 0

    def test_unknown_tier_falls_back_to_default(self):
        result = calculate_payout_amount(299, 99)
        assert result == int(299 * 100 * DEFAULT_TIER_PERCENTAGE)

    def test_tier_percentages_match_pitch(self):
        assert TIER_PERCENTAGES[1] == 0.35
        assert TIER_PERCENTAGES[2] == 0.45
        assert TIER_PERCENTAGES[3] == 0.55
        assert DEFAULT_TIER_PERCENTAGE == 0.35


# ── Unit: trigger_payout_for_job ─────────────────────────────────────────────

def _mock_db(job_data=None, payout_data=None, insert_data=None):
    """Build a minimal async Supabase mock for payout tests."""
    db = MagicMock()

    def make_chain(result_data):
        chain = MagicMock()
        chain.execute = AsyncMock(return_value=MagicMock(data=result_data))
        chain.eq = MagicMock(return_value=chain)
        chain.select = MagicMock(return_value=chain)
        chain.insert = MagicMock(return_value=chain)
        chain.update = MagicMock(return_value=chain)
        chain.maybe_single = MagicMock(return_value=chain)
        return chain

    job_chain = make_chain(job_data)
    payout_chain = make_chain(payout_data)
    insert_chain = make_chain(insert_data)

    def table_side_effect(name):
        if name == "jobs":
            return job_chain
        if name == "payouts":
            # First call: check existing (maybe_single); second call: insert
            return payout_chain
        return make_chain(None)

    db.table = MagicMock(side_effect=table_side_effect)
    return db


class TestTriggerPayoutForJob:
    @pytest.mark.asyncio
    async def test_job_not_found_returns_error(self):
        from app.services.payout_service import trigger_payout_for_job
        db = _mock_db(job_data=None)
        result = await trigger_payout_for_job(db, "job_missing")
        assert result["error"] == "job_not_found"

    @pytest.mark.asyncio
    async def test_zero_package_price_returns_error(self):
        from app.services.payout_service import trigger_payout_for_job
        job = {"id": "job_1", "vendor_id": "v1", "package_price": None, "vendor_tier": 1}
        db = _mock_db(job_data=job)
        result = await trigger_payout_for_job(db, "job_1")
        assert result["error"] == "zero_payout_amount"

    @pytest.mark.asyncio
    async def test_idempotent_if_payout_exists(self):
        from app.services.payout_service import trigger_payout_for_job
        job = {"id": "job_2", "vendor_id": "v1", "package_price": 299, "vendor_tier": 1, "booking_id": "bk_1"}
        existing_payout = {"id": "pay_existing", "status": "paid", "amount": 11960}
        db = MagicMock()

        job_chain = MagicMock()
        job_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=job)
        )

        payout_chain = MagicMock()
        payout_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=existing_payout)
        )
        payout_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        payout_chain.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        def table_dispatch(name):
            return job_chain if name == "jobs" else payout_chain

        db.table = MagicMock(side_effect=table_dispatch)

        result = await trigger_payout_for_job(db, "job_2")
        assert result["already_exists"] is True
        assert result["payout_id"] == "pay_existing"

    @pytest.mark.asyncio
    async def test_happy_path_tier_b(self):
        from app.services.payout_service import trigger_payout_for_job
        job = {
            "id": "job_3", "vendor_id": "v_test", "package_price": 599,
            "vendor_tier": 2, "booking_id": "bk_3",
        }
        new_payout = {"id": "pay_new"}

        job_chain = MagicMock()
        job_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=job)
        )
        job_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        payout_chain = MagicMock()
        # existing check → None
        payout_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=None)
        )
        # insert → new payout
        payout_chain.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[new_payout])
        )
        # update (mark paid)
        payout_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        def table_dispatch(name):
            return job_chain if name == "jobs" else payout_chain

        db = MagicMock()
        db.table = MagicMock(side_effect=table_dispatch)

        with patch(
            "app.services.payout_service.handle_payout_to_vendor",
            new_callable=AsyncMock,
            return_value="tr_test_stripe_id",
        ):
            result = await trigger_payout_for_job(db, "job_3")

        assert result["status"] == "paid"
        # Tier B/core (45%) of $599 × 100 = 26955 cents
        assert result["base_amount"] == int(599 * 100 * 0.45)
        assert result["stripe_transfer_id"] == "tr_test_stripe_id"

    @pytest.mark.asyncio
    async def test_stripe_failure_marks_payout_failed(self):
        from app.services.payout_service import trigger_payout_for_job
        job = {
            "id": "job_4", "vendor_id": "v_fail", "package_price": 299,
            "vendor_tier": 1, "booking_id": "bk_4",
        }
        new_payout = {"id": "pay_fail"}

        job_chain = MagicMock()
        job_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=job)
        )
        job_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        payout_chain = MagicMock()
        payout_chain.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
            return_value=MagicMock(data=None)
        )
        payout_chain.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[new_payout])
        )
        payout_chain.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        def table_dispatch(name):
            return job_chain if name == "jobs" else payout_chain

        db = MagicMock()
        db.table = MagicMock(side_effect=table_dispatch)

        with patch(
            "app.services.payout_service.handle_payout_to_vendor",
            new_callable=AsyncMock,
            side_effect=Exception("Stripe Connect error"),
        ):
            result = await trigger_payout_for_job(db, "job_4")

        assert result["status"] == "failed"
        assert "Stripe Connect error" in result["error"]
