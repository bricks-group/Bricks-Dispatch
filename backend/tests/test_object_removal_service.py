"""
Tests for object-removal failover (Phase 2, 2026-09-12).

Mirrors test_post_production_enhancement.py's style for
_select_and_run_enhancement: mocks the two provider calls
(_run_mac_inpaint / _run_gemini_inpaint) directly rather than the
underlying httpx transport, and verifies the chain-building + fallback
behavior of remove_object() itself.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.object_removal_service import remove_object


_IMG = b"fake-image-bytes"
_MASK = b"fake-mask-bytes"


class TestProviderConfiguration:
    @pytest.mark.asyncio
    async def test_no_provider_configured_returns_none(self, monkeypatch):
        monkeypatch.delenv("MAC_INFERENCE_URL", raising=False)
        monkeypatch.delenv("MAC_INFERENCE_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        assert result is None

    @pytest.mark.asyncio
    async def test_mac_configured_without_key_counts_as_unconfigured(self, monkeypatch):
        """MAC_INFERENCE_URL alone (no key) must not be treated as a usable
        Mac -- both env vars are required."""
        monkeypatch.setenv("MAC_INFERENCE_URL", "https://mac.example.com")
        monkeypatch.delenv("MAC_INFERENCE_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        assert result is None


class TestFailoverChain:
    @pytest.mark.asyncio
    async def test_mac_tried_first_when_configured_and_succeeds(self, monkeypatch):
        monkeypatch.setenv("MAC_INFERENCE_URL", "https://mac.example.com")
        monkeypatch.setenv("MAC_INFERENCE_KEY", "mac-secret")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

        with patch(
            "app.services.object_removal_service._run_mac_inpaint",
            new=AsyncMock(return_value=b"mac-result"),
        ) as mock_mac, patch(
            "app.services.object_removal_service._run_gemini_inpaint",
            new=AsyncMock(),
        ) as mock_gemini:
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        mock_mac.assert_awaited_once()
        mock_gemini.assert_not_awaited()
        assert result == {"provider": "bricks_mac_inference", "image_bytes": b"mac-result"}

    @pytest.mark.asyncio
    async def test_mac_failure_falls_through_to_gemini(self, monkeypatch):
        """The concrete failover behavior this module exists for: a
        configured-but-failing (unreachable/timed-out/erroring) Mac must not
        drop the asset -- Gemini gets tried within the same call."""
        monkeypatch.setenv("MAC_INFERENCE_URL", "https://mac.example.com")
        monkeypatch.setenv("MAC_INFERENCE_KEY", "mac-secret")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

        with patch(
            "app.services.object_removal_service._run_mac_inpaint",
            new=AsyncMock(side_effect=RuntimeError("Mac unreachable")),
        ) as mock_mac, patch(
            "app.services.object_removal_service._run_gemini_inpaint",
            new=AsyncMock(return_value=b"gemini-result"),
        ) as mock_gemini:
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        mock_mac.assert_awaited_once()
        mock_gemini.assert_awaited_once()
        assert result == {"provider": "gemini", "image_bytes": b"gemini-result"}

    @pytest.mark.asyncio
    async def test_mac_not_configured_goes_straight_to_gemini(self, monkeypatch):
        """The 'vice versa' case: with no Mac configured at all, Gemini is
        used directly -- not attempted as a fallback-of-a-fallback, just the
        only thing in the chain."""
        monkeypatch.delenv("MAC_INFERENCE_URL", raising=False)
        monkeypatch.delenv("MAC_INFERENCE_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

        with patch(
            "app.services.object_removal_service._run_mac_inpaint", new=AsyncMock()
        ) as mock_mac, patch(
            "app.services.object_removal_service._run_gemini_inpaint",
            new=AsyncMock(return_value=b"gemini-result"),
        ) as mock_gemini:
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        mock_mac.assert_not_awaited()
        mock_gemini.assert_awaited_once()
        assert result == {"provider": "gemini", "image_bytes": b"gemini-result"}

    @pytest.mark.asyncio
    async def test_gemini_not_configured_stops_after_mac_failure(self, monkeypatch):
        """Mac fails and Gemini isn't configured -- the chain has nothing
        left, so the asset is dropped (returns None), not an exception."""
        monkeypatch.setenv("MAC_INFERENCE_URL", "https://mac.example.com")
        monkeypatch.setenv("MAC_INFERENCE_KEY", "mac-secret")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with patch(
            "app.services.object_removal_service._run_mac_inpaint",
            new=AsyncMock(side_effect=RuntimeError("Mac unreachable")),
        ) as mock_mac, patch(
            "app.services.object_removal_service._run_gemini_inpaint", new=AsyncMock()
        ) as mock_gemini:
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        mock_mac.assert_awaited_once()
        mock_gemini.assert_not_awaited()
        assert result is None

    @pytest.mark.asyncio
    async def test_both_configured_and_both_fail_drops_the_asset(self, monkeypatch):
        monkeypatch.setenv("MAC_INFERENCE_URL", "https://mac.example.com")
        monkeypatch.setenv("MAC_INFERENCE_KEY", "mac-secret")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

        with patch(
            "app.services.object_removal_service._run_mac_inpaint",
            new=AsyncMock(side_effect=RuntimeError("Mac unreachable")),
        ), patch(
            "app.services.object_removal_service._run_gemini_inpaint",
            new=AsyncMock(side_effect=RuntimeError("Gemini returned no image")),
        ):
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        assert result is None

    @pytest.mark.asyncio
    async def test_gemini_failure_alone_when_mac_unconfigured_drops_the_asset(self, monkeypatch):
        """Gemini down/failing with no Mac configured -- nothing left to try."""
        monkeypatch.delenv("MAC_INFERENCE_URL", raising=False)
        monkeypatch.delenv("MAC_INFERENCE_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

        with patch(
            "app.services.object_removal_service._run_gemini_inpaint",
            new=AsyncMock(side_effect=RuntimeError("Gemini down")),
        ):
            result = await remove_object(_IMG, _MASK, job_id="job1", upload_id="u1")

        assert result is None
