"""
Tests for enhancement provider routing + runtime failover (Phase 0/1, 2026-09-11).

Covers:
  - select_enhancement_provider(): staging is out of scope, in-house is the
    default primary, disabling in-house falls back to the pre-existing
    config-time Fotello -> Autoenhance chain.
  - _select_and_run_enhancement(): the NEW runtime-failover behavior -- an
    in-house failure (or a Fotello runtime failure) falls through to the next
    configured provider within the same attempt, instead of the asset just
    being dropped as it was before this change.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.post_production import select_enhancement_provider, _select_and_run_enhancement


# ── select_enhancement_provider ──────────────────────────────────────────────

class TestSelectEnhancementProvider:
    def test_staging_asset_is_out_of_scope(self, monkeypatch):
        """Staging assets are handled entirely by trigger_boxbrownie_staging in the
        caller, before this function is ever consulted -- it must never claim a
        staging asset for itself, regardless of in-house/vendor config."""
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "true")
        asset = {"id": "u1", "requires_staging": True}

        assert select_enhancement_provider(asset) is None

    def test_inhouse_enabled_is_primary_by_default(self, monkeypatch):
        monkeypatch.delenv("INHOUSE_CV_ENHANCEMENT_ENABLED", raising=False)
        asset = {"id": "u1"}

        assert select_enhancement_provider(asset) == "bricks_inhouse_cv"

    def test_inhouse_explicitly_enabled(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "true")
        asset = {"id": "u1"}

        assert select_enhancement_provider(asset) == "bricks_inhouse_cv"

    def test_inhouse_disabled_falls_back_to_fotello(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "false")
        with patch("app.services.post_production.FOTELLO_API_KEY", "ft_key"), \
             patch("app.services.post_production.AUTOENHANCE_API_KEY", "ae_key"):
            asset = {"id": "u1"}
            assert select_enhancement_provider(asset) == "fotello"

    def test_inhouse_disabled_and_no_fotello_falls_back_to_autoenhance(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "false")
        with patch("app.services.post_production.FOTELLO_API_KEY", None), \
             patch("app.services.post_production.AUTOENHANCE_API_KEY", "ae_key"):
            asset = {"id": "u1"}
            assert select_enhancement_provider(asset) == "autoenhance"

    def test_inhouse_disabled_and_no_vendor_keys_returns_none(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "false")
        with patch("app.services.post_production.FOTELLO_API_KEY", None), \
             patch("app.services.post_production.AUTOENHANCE_API_KEY", None):
            asset = {"id": "u1"}
            assert select_enhancement_provider(asset) is None


# ── _select_and_run_enhancement: real runtime failover ───────────────────────

class TestRuntimeFailover:
    @pytest.mark.asyncio
    async def test_inhouse_failure_falls_through_to_vendor_within_same_attempt(self, monkeypatch):
        """The concrete bug this refactor fixes: previously there was NO runtime
        fallback at all when the primary path failed for a given asset -- it was
        just dropped. Now an in-house exception must fall through to a configured
        vendor within the same process_job_assets attempt."""
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "true")
        asset = {"id": "u1"}
        db = MagicMock()

        with patch(
            "app.services.post_production._run_inhouse_cv_enhancement",
            new=AsyncMock(side_effect=RuntimeError("cv pipeline blew up")),
        ) as mock_inhouse, patch(
            "app.services.post_production.FOTELLO_API_KEY", "ft_key"
        ), patch(
            "app.services.post_production.AUTOENHANCE_API_KEY", None
        ), patch(
            "app.services.post_production.trigger_fotello_edit",
            new=AsyncMock(return_value="img_123"),
        ) as mock_fotello:
            result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u1.jpg")

        mock_inhouse.assert_awaited_once()
        mock_fotello.assert_awaited_once_with("https://signed.example/u1.jpg")
        assert result == {"asset_id": "u1", "provider": "fotello", "provider_job_id": "img_123"}

    @pytest.mark.asyncio
    async def test_inhouse_failure_falls_through_all_the_way_to_autoenhance(self, monkeypatch):
        """If Fotello is unconfigured (or also fails), the chain keeps going to
        Autoenhance -- not just a single fallback hop."""
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "true")
        asset = {"id": "u2"}
        db = MagicMock()

        with patch(
            "app.services.post_production._run_inhouse_cv_enhancement",
            new=AsyncMock(side_effect=RuntimeError("cv pipeline blew up")),
        ), patch(
            "app.services.post_production.FOTELLO_API_KEY", None
        ), patch(
            "app.services.post_production.AUTOENHANCE_API_KEY", "ae_key"
        ), patch(
            "app.services.post_production.trigger_autoenhance_edit",
            new=AsyncMock(return_value="ae_456"),
        ) as mock_autoenhance:
            result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u2.jpg")

        mock_autoenhance.assert_awaited_once()
        assert result == {"asset_id": "u2", "provider": "autoenhance", "provider_job_id": "ae_456"}

    @pytest.mark.asyncio
    async def test_fotello_runtime_failure_falls_through_to_autoenhance(self, monkeypatch):
        """The originally-reported bug, verbatim: Fotello's key IS set but the
        actual API call fails at runtime -- must now fall back to Autoenhance
        for that asset instead of dropping it."""
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "false")
        asset = {"id": "u3"}
        db = MagicMock()

        with patch(
            "app.services.post_production.FOTELLO_API_KEY", "ft_key"
        ), patch(
            "app.services.post_production.AUTOENHANCE_API_KEY", "ae_key"
        ), patch(
            "app.services.post_production.trigger_fotello_edit",
            new=AsyncMock(return_value=None),  # runtime failure -- Fotello call failed
        ) as mock_fotello, patch(
            "app.services.post_production.trigger_autoenhance_edit",
            new=AsyncMock(return_value="ae_789"),
        ) as mock_autoenhance:
            result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u3.jpg")

        mock_fotello.assert_awaited_once()
        mock_autoenhance.assert_awaited_once()
        assert result == {"asset_id": "u3", "provider": "autoenhance", "provider_job_id": "ae_789"}

    @pytest.mark.asyncio
    async def test_inhouse_success_returns_none_and_never_calls_vendor(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "true")
        asset = {"id": "u4"}
        db = MagicMock()

        with patch(
            "app.services.post_production._run_inhouse_cv_enhancement",
            new=AsyncMock(return_value=None),
        ) as mock_inhouse, patch(
            "app.services.post_production.FOTELLO_API_KEY", "ft_key"
        ), patch(
            "app.services.post_production.trigger_fotello_edit", new=AsyncMock()
        ) as mock_fotello:
            result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u4.jpg")

        mock_inhouse.assert_awaited_once()
        mock_fotello.assert_not_awaited()
        assert result is None

    @pytest.mark.asyncio
    async def test_staging_asset_never_reaches_enhancement_routing(self):
        """select_enhancement_provider() (and therefore the whole chain) is a no-op
        for staging assets -- BoxBrownie routing in process_job_assets handles them
        before this function is ever called."""
        asset = {"id": "u5", "requires_staging": True}
        db = MagicMock()

        result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u5.jpg")

        assert result is None

    @pytest.mark.asyncio
    async def test_all_providers_failing_drops_the_asset(self, monkeypatch):
        monkeypatch.setenv("INHOUSE_CV_ENHANCEMENT_ENABLED", "false")
        asset = {"id": "u6"}
        db = MagicMock()

        with patch("app.services.post_production.FOTELLO_API_KEY", "ft_key"), \
             patch("app.services.post_production.AUTOENHANCE_API_KEY", "ae_key"), \
             patch("app.services.post_production.trigger_fotello_edit", new=AsyncMock(return_value=None)), \
             patch("app.services.post_production.trigger_autoenhance_edit", new=AsyncMock(return_value=None)):
            result = await _select_and_run_enhancement(db, "job1", asset, "https://signed.example/u6.jpg")

        assert result is None
