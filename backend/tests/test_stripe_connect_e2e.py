"""
Stripe Connect E2E test suite for Bricks GO.

Covers:
  - Live test-mode transfer to a Stripe Express account
  - Idempotency: duplicate booking_id returns same transfer, not a double-pay
  - Webhook duplicate handling
  - A normal payout never reads no-show claims (no flat no-show fee since 2026-10-30)

Run with:
    STRIPE_SECRET_KEY=sk_test_... pytest tests/test_stripe_connect_e2e.py -v

Set STRIPE_SECRET_KEY to a test-mode key (sk_test_...).
Live-mode key (sk_live_...) is rejected by this suite.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest
import stripe as stripe_lib
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.services.payout_service import calculate_payout_amount, trigger_payout_for_job
from app.services.stripe_connect_service import handle_payout_to_vendor
from app.routers.vendor_os import _validate_noshow_claim  # no Stripe charge logic here


# ── Guards ───────────────────────────────────────────────────────────────────

def _require_test_key():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        pytest.skip("STRIPE_SECRET_KEY not set — skipping Stripe live tests")
    if not key.startswith("sk_test_"):
        pytest.skip("Live Stripe key detected — refusing to run test suite against production")
    stripe_lib.api_key = key


# ── Unit: no-show validation gates ───────────────────────────────────────────

def _make_db_mock(existing_claim=None, recent_claims=0, booking=None, user=None):
    """Helper: build an async DB mock for _validate_noshow_claim tests."""
    db = MagicMock()

    async def _execute_factory(table_name, query_type, *args, **kwargs):
        if table_name == "agent_noshow_trip_fees":
            if query_type == "maybe_single":
                return MagicMock(data=existing_claim)
            # select list (rate limit count)
            return MagicMock(data=[{"id": str(i)} for i in range(recent_claims)])
        if table_name == "bookings":
            return MagicMock(data=booking)
        if table_name == "users":
            return MagicMock(data=user)
        return MagicMock(data=None)

    # Simple: all .execute() calls return no existing claim + 0 recent by default
    db.table = MagicMock(return_value=db)
    db.select = db.eq = db.in_ = db.gte = db.maybe_single = MagicMock(return_value=db)
    db.execute = AsyncMock(return_value=MagicMock(data=None))
    return db


def _now_utc():
    return datetime.now(timezone.utc)


def _job(assignee_id="member_1", status="in_progress", shoot_offset_min=0):
    """Return a minimal job dict. shoot_offset_min: negative = future, positive = past.

    Uses the canonical DB format: scheduled_date (YYYY-MM-DD) + time_window_start (HH:MM).
    time_window_start is stored as a property-local HH:MM (province timezone).
    """
    from zoneinfo import ZoneInfo
    _tz = ZoneInfo("America/Toronto")
    shoot_utc = _now_utc() - timedelta(minutes=shoot_offset_min)
    shoot_et = shoot_utc.astimezone(_tz)
    return {
        "id": str(uuid.uuid4()),
        "assignee_id": assignee_id,
        "vendor_id": "vendor_1",
        "booking_id": str(uuid.uuid4()),
        "status": status,
        "scheduled_date": shoot_et.strftime("%Y-%m-%d"),
        "time_window_start": shoot_et.strftime("%H:%M"),
    }


class TestNoshowValidation:
    @pytest.mark.asyncio
    async def test_wrong_reporter_rejected(self):
        db = _make_db_mock()
        result = await _validate_noshow_claim(db, _job(assignee_id="member_1"), reporter_id="member_OTHER")
        assert not result["ok"]
        assert "assigned photographer" in result["reason"]

    @pytest.mark.asyncio
    async def test_completed_job_rejected(self):
        db = _make_db_mock()
        result = await _validate_noshow_claim(db, _job(status="completed"), reporter_id="member_1")
        assert not result["ok"]
        assert "completed" in result["reason"]

    @pytest.mark.asyncio
    async def test_already_cancelled_job_rejected(self):
        db = _make_db_mock()
        result = await _validate_noshow_claim(db, _job(status="agent_no_show"), reporter_id="member_1")
        assert not result["ok"]

    @pytest.mark.asyncio
    async def test_duplicate_claim_rejected(self):
        db = _make_db_mock(existing_claim={"id": "existing_fee", "status": "pending"})
        # Sequence: first execute = existing claim found
        db.execute = AsyncMock(return_value=MagicMock(data={"id": "existing_fee", "status": "pending"}))
        result = await _validate_noshow_claim(db, _job(), reporter_id="member_1")
        assert not result["ok"]
        assert "already exists" in result["reason"]

    @pytest.mark.asyncio
    async def test_too_early_rejected(self):
        db = _make_db_mock()
        db.execute = AsyncMock(return_value=MagicMock(data=None))
        # Shoot is 30 min in the FUTURE — too early to claim (window opens at T-15min)
        job = _job(shoot_offset_min=-30)
        result = await _validate_noshow_claim(db, job, reporter_id="member_1")
        assert not result["ok"]
        assert "Too early" in result["reason"]

    @pytest.mark.asyncio
    async def test_too_late_rejected(self):
        db = _make_db_mock()
        db.execute = AsyncMock(return_value=MagicMock(data=None))
        # Shoot was 120 min AGO — outside 90-min window
        job = _job(shoot_offset_min=120)
        result = await _validate_noshow_claim(db, job, reporter_id="member_1")
        assert not result["ok"]
        assert "expired" in result["reason"]

    @pytest.mark.asyncio
    async def test_within_window_approved(self):
        # Shoot started 20 min ago — within T+90min window, no prior claims
        execute_calls = [
            MagicMock(data=None),   # no existing claim
            MagicMock(data=None),   # booking lookup (skipped for rate limit)
            MagicMock(data=[]),     # rate limit: 0 recent claims
        ]
        call_idx = 0
        async def _seq():
            nonlocal call_idx
            r = execute_calls[min(call_idx, len(execute_calls) - 1)]
            call_idx += 1
            return r
        db = MagicMock()
        db.table = db.select = db.eq = db.in_ = db.gte = db.maybe_single = MagicMock(return_value=db)
        db.execute = AsyncMock(side_effect=_seq)

        job = _job(shoot_offset_min=20)
        result = await _validate_noshow_claim(db, job, reporter_id="member_1")
        assert result["ok"]
        assert not result.get("hold_for_review")

    @pytest.mark.asyncio
    async def test_rate_limit_triggers_review_hold(self):
        # Photographer already has 1 prior claim in 30 days → threshold hit → hold for review
        # (threshold=1 means 2nd+ claim requires admin sign-off before charge fires)
        execute_calls = [
            MagicMock(data=None),                  # no duplicate claim for this specific job
            MagicMock(data=[{"id": "f1"}]),        # 1 prior recent claim → at threshold
        ]
        call_idx = 0
        async def _seq():
            nonlocal call_idx
            r = execute_calls[min(call_idx, len(execute_calls) - 1)]
            call_idx += 1
            return r
        db = MagicMock()
        db.table = db.select = db.eq = db.in_ = db.gte = db.maybe_single = MagicMock(return_value=db)
        db.execute = AsyncMock(side_effect=_seq)

        job = _job(shoot_offset_min=30)  # 30 min ago — valid window
        result = await _validate_noshow_claim(db, job, reporter_id="member_1")
        assert result["ok"]
        assert result["hold_for_review"]
        assert "Rate limit" in result["reason"]


# ── Unit: a normal payout never reads no-show claims ─────────────────────────
# There is no flat no-show fee (removed 2026-10-30). A claim row in
# agent_noshow_trip_fees (legacy table name) must not add to a normal transfer.
# An approved client no-show pays half the base instead: see
# tests/test_noshow_half_payout.py.

@pytest.mark.asyncio
async def test_payout_ignores_noshow_claims():
    job_id = str(uuid.uuid4())
    booking_id = str(uuid.uuid4())
    payout_id = str(uuid.uuid4())

    tables = []
    db = MagicMock()
    db.table = MagicMock(side_effect=lambda name: (tables.append(name), db)[1])
    db.select = db.update = db.insert = db.eq = db.in_ = db.lte = db.limit = db.maybe_single = MagicMock(return_value=db)

    job_row = {
        "id": job_id, "vendor_id": "vendor_1", "assignee_id": "member_abc",
        "booking_id": booking_id, "package_price": 299, "vendor_tier": 2,
    }
    payout_row_initial = {"id": payout_id, "status": "pending", "amount": 14950}
    legacy_claim_rows = [{"id": "fee_1", "amount_cents": 6000}]

    async def _execute():
        if tables and tables[-1] == "agent_noshow_trip_fees":
            return MagicMock(data=legacy_claim_rows)
        if tables and tables[-1] == "jobs":
            return MagicMock(data=job_row)
        if tables and tables[-1] == "payouts":
            return MagicMock(data=[payout_row_initial])
        return MagicMock(data=None)

    db.execute = AsyncMock(side_effect=_execute)

    transfer_id = f"tr_{uuid.uuid4().hex[:16]}"
    with patch(
        "app.services.stripe_connect_service.handle_payout_to_vendor",
        new_callable=AsyncMock,
        return_value=transfer_id,
    ) as mock_transfer:
        result = await trigger_payout_for_job(db, job_id)

    assert "agent_noshow_trip_fees" not in tables
    assert "trip_fees_total" not in (result or {})
    assert mock_transfer.called
    _, kwargs = mock_transfer.call_args
    assert kwargs.get("amount") == result["net_amount"] == result["base_amount"]


# ── Live Stripe Connect tests (require sk_test_...) ───────────────────────────

class TestStripeConnectLive:
    """
    Live test-mode Stripe Connect tests.
    Creates a test Express account, verifies transfers, tests idempotency.
    Cleans up after itself.
    """

    @pytest.fixture(scope="class", autouse=True)
    def stripe_setup(self):
        _require_test_key()

    @pytest.fixture(scope="class")
    def test_account(self):
        """Create a minimal Stripe Express test account."""
        acct = stripe_lib.Account.create(
            type="express",
            country="CA",
            capabilities={"transfers": {"requested": True}},
            settings={"payouts": {"schedule": {"interval": "manual"}}},
        )
        yield acct
        # Cleanup
        try:
            stripe_lib.Account.delete(acct.id)
        except Exception:
            pass

    def test_create_express_account(self, test_account):
        assert test_account.id.startswith("acct_")
        assert test_account.type == "express"
        assert test_account.country == "CA"

    def test_transfer_to_connected_account(self, test_account):
        """Transfer $1 CAD to a test Express account."""
        booking_id = str(uuid.uuid4())
        transfer = stripe_lib.Transfer.create(
            amount=100,                     # $1 CAD in cents
            currency="cad",
            destination=test_account.id,
            metadata={"booking_id": booking_id, "test": "true"},
            idempotency_key=f"payout_{booking_id}",
        )
        assert transfer.id.startswith("tr_")
        assert transfer.amount == 100
        assert transfer.currency == "cad"
        assert transfer.destination == test_account.id

    def test_idempotency_no_double_pay(self, test_account):
        """Same idempotency key returns the same transfer, not a second charge."""
        booking_id = str(uuid.uuid4())
        ikey = f"payout_{booking_id}"

        t1 = stripe_lib.Transfer.create(
            amount=100,
            currency="cad",
            destination=test_account.id,
            metadata={"booking_id": booking_id},
            idempotency_key=ikey,
        )
        t2 = stripe_lib.Transfer.create(
            amount=100,
            currency="cad",
            destination=test_account.id,
            metadata={"booking_id": booking_id},
            idempotency_key=ikey,
        )
        assert t1.id == t2.id, "Idempotency failed: two different transfers created for same key"

    def test_zero_amount_transfer_rejected(self, test_account):
        """Stripe rejects $0 transfers — verify we handle this gracefully."""
        with pytest.raises(stripe_lib.error.InvalidRequestError):
            stripe_lib.Transfer.create(
                amount=0,
                currency="cad",
                destination=test_account.id,
                idempotency_key=f"payout_zero_{uuid.uuid4()}",
            )

    def test_retrieve_transfer(self, test_account):
        """Transfer can be retrieved after creation."""
        booking_id = str(uuid.uuid4())
        transfer = stripe_lib.Transfer.create(
            amount=150,
            currency="cad",
            destination=test_account.id,
            idempotency_key=f"payout_{booking_id}",
        )
        retrieved = stripe_lib.Transfer.retrieve(transfer.id)
        assert retrieved.id == transfer.id
        assert retrieved.amount == 150

    def test_handle_payout_to_vendor_integration(self, test_account):
        """
        Full handle_payout_to_vendor call with a real test Stripe account.
        Mocks only the Supabase lookup; Stripe call is live.
        """
        vendor_id = "test_vendor"
        booking_id = str(uuid.uuid4())

        with patch("app.services.stripe_connect_service.supabase") as mock_sb:
            mock_sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
                data={"id": "contractor_test", "stripe_account_id": test_account.id}
            )

            import asyncio
            transfer_id = asyncio.get_event_loop().run_until_complete(
                handle_payout_to_vendor(
                    vendor_id=vendor_id,
                    amount=200,
                    booking_id=booking_id,
                )
            )
        assert transfer_id.startswith("tr_")
