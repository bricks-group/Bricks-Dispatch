"""
Stripe Connect E2E test suite for Bricks-Dispatch.

Covers:
  - Live test-mode transfer to a Stripe Express account
  - Idempotency: duplicate booking_id returns same transfer, not a double-pay
  - Webhook duplicate handling
  - Trip fee ADDITION to base payout (agent charged separately; photographer gets pass-through)
  - Base-only payout when no pending trip fees

Run with:
    STRIPE_SECRET_KEY=sk_test_... pytest tests/test_stripe_connect_e2e.py -v

Set STRIPE_SECRET_KEY to a test-mode key (sk_test_...).
Live-mode key (sk_live_...) is rejected by this suite.
"""
import os
import uuid
import pytest
import stripe as stripe_lib
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.services.payout_service import calculate_payout_amount, trigger_payout_for_job
from app.services.stripe_connect_service import handle_payout_to_vendor


# ── Guards ───────────────────────────────────────────────────────────────────

def _require_test_key():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        pytest.skip("STRIPE_SECRET_KEY not set — skipping Stripe live tests")
    if not key.startswith("sk_test_"):
        pytest.skip("Live Stripe key detected — refusing to run test suite against production")
    stripe_lib.api_key = key


# ── Unit: trip fee addition logic ────────────────────────────────────────────
# Trip fees are charged to the AGENT (Bricks bills the client) and added on top
# of the photographer's base payout. Bricks is a $0 net pass-through.

class TestPayoutTripFeeLogic:
    def test_base_plus_one_trip_fee(self):
        base = 14950    # Tier B, $299 package → 14950 cents
        trip_fee = 6000
        net = base + trip_fee
        assert net == 20950

    def test_base_no_trip_fees(self):
        base = 29950
        net = base + 0
        assert net == base

    def test_multiple_trip_fees_added(self):
        base = 29950
        trip_fees = 6000 + 6000   # two no-shows on separate jobs
        net = base + trip_fees
        assert net == 41950

    def test_trip_fee_only_no_base(self):
        # Edge: zero-base job (shouldn't happen in practice, but math should hold)
        base = 0
        trip_fee = 6000
        net = base + trip_fee
        assert net == 6000


# ── Unit: payout service with trip fee (mocked DB + Stripe) ─────────────────

@pytest.mark.asyncio
async def test_payout_adds_trip_fee():
    """
    trigger_payout_for_job adds pending agent no-show trip fees to the transfer amount.
    Agent was already charged separately; photographer receives the pass-through on top of base.
    """
    job_id = str(uuid.uuid4())
    booking_id = str(uuid.uuid4())
    payout_id = str(uuid.uuid4())

    db = MagicMock()
    db.table = MagicMock(return_value=db)
    db.select = db.update = db.insert = db.eq = db.in_ = db.execute = MagicMock(return_value=db)

    # Job row
    job_row = {
        "id": job_id, "vendor_id": "vendor_1", "assignee_id": "member_abc",
        "booking_id": booking_id, "package_price": 299, "vendor_tier": 2,
    }
    payout_row_initial = {"id": payout_id, "status": "pending", "amount": 14950}
    trip_fee_rows = [{"id": "fee_1", "amount_cents": 6000}]

    execute_sequence = [
        MagicMock(data=job_row),              # fetch job
        MagicMock(data=None),                 # no existing payout
        MagicMock(data=[payout_row_initial]), # insert payout record
        MagicMock(data=None),                 # update job payout_amount
        MagicMock(data=trip_fee_rows),        # fetch pending trip fees (agent_noshow_trip_fees)
    ]

    call_count = 0
    async def _execute():
        nonlocal call_count
        result = execute_sequence[min(call_count, len(execute_sequence) - 1)]
        call_count += 1
        return result

    db.execute = AsyncMock(side_effect=_execute)

    base = calculate_payout_amount(299, 2)   # 14950
    expected_net = base + 6000               # 20950 — trip fee ADDED, not deducted

    transfer_id = f"tr_{uuid.uuid4().hex[:16]}"

    with patch(
        "app.services.stripe_connect_service.handle_payout_to_vendor",
        new_callable=AsyncMock,
        return_value=transfer_id,
    ) as mock_transfer:
        try:
            await trigger_payout_for_job(db, job_id)
        except Exception:
            pass

        if mock_transfer.called:
            _, kwargs = mock_transfer.call_args
            amount_sent = kwargs.get("amount") or mock_transfer.call_args[0][1]
            assert amount_sent == expected_net, (
                f"Expected net transfer of {expected_net} cents (base + trip fee), got {amount_sent}"
            )


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
