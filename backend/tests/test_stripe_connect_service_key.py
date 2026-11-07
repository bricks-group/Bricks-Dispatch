"""stripe_connect_service must use the service-role key: contractors has RLS."""
import importlib
import sys
from unittest.mock import patch


def _load(env):
    sys.modules.pop("app.services.stripe_connect_service", None)
    with patch.dict("os.environ", env, clear=False), patch("supabase.create_client") as cc:
        mod = importlib.import_module("app.services.stripe_connect_service")
        return mod, cc


def test_prefers_service_key():
    mod, _ = _load({"SUPABASE_SERVICE_KEY": "svc", "SUPABASE_KEY": "anon"})
    assert mod.SUPABASE_KEY == "svc"


def test_falls_back_to_service_role_then_key():
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        mod, _ = _load({"SUPABASE_SERVICE_ROLE_KEY": "role", "SUPABASE_KEY": "anon"})
        assert mod.SUPABASE_KEY == "role"


def test_account_link_uses_collection_options():
    """Stripe rejects collect_options (400 parameter_unknown)."""
    import asyncio
    mod, _ = _load({"SUPABASE_SERVICE_KEY": "svc"})
    row = {"id": "c1", "stripe_account_id": "acct_test", "member_id": "m1"}
    with patch.object(mod, "load_contractor_row", return_value=row), \
         patch.object(mod.stripe_lib.AccountLink, "create") as link:
        link.return_value.url = "https://connect.stripe.com/setup/e/acct_test/x"
        url = asyncio.run(mod.create_connect_account_and_link(
            "v1", "https://go.brxs.ca/", "https://go.brxs.ca/", member_id="m1"))
    assert url.startswith("https://connect.stripe.com/")
    kwargs = link.call_args.kwargs
    assert "collect_options" not in kwargs
    assert kwargs["collection_options"] == {"fields": "currently_due"}
