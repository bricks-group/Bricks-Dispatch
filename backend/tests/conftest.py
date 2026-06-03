"""
Test environment bootstrap.

Stubs out heavy external dependencies (stripe, supabase, httpx transports, etc.)
that are not installed in the CI/test environment, so unit tests can import
production modules without needing a live Stripe or Supabase connection.
"""
import sys
from unittest.mock import AsyncMock, MagicMock

# ── Stub external packages before any production module imports them ──────────

for _mod in ("stripe", "supabase", "gotrue", "postgrest", "realtime", "storage3"):
    sys.modules.setdefault(_mod, MagicMock())

# ── Stub stripe_connect_service so payout_service can do its local import ────
# The function under test (trigger_payout_for_job) does:
#   from .stripe_connect_service import handle_payout_to_vendor
# Patching "app.services.stripe_connect_service.handle_payout_to_vendor" in
# individual tests will override this stub at call time.

_mock_scs = MagicMock()
_mock_scs.handle_payout_to_vendor = AsyncMock(return_value="tr_stub")
sys.modules.setdefault("app.services.stripe_connect_service", _mock_scs)
