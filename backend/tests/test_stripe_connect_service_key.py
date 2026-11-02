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
