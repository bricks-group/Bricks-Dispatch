"""
Gemini model centralization (2026-09-12 fix for the gemini-1.5-flash 404
outage across qc_vision_provider.py, rpas_validator.py,
compliance_validator.py, and bricks_ai_dispatch.py).

Covers:
  - app.shared.gemini_config is the single source of the model name/URL,
    the env var override actually takes effect, and the four call sites
    genuinely import and use it (not a stray hardcoded string reintroduced).
  - the previously-silent failure paths now log at a visible level, with
    the QC vision provider (the most critical of the four) covered by a
    simulated Gemini 404.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# app.shared.gemini_config itself
# ---------------------------------------------------------------------------

def test_default_model_is_not_a_retired_gemini_1_x(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL_NAME", raising=False)
    from app.shared.gemini_config import get_gemini_model
    model = get_gemini_model()
    assert "gemini-1." not in model
    assert "gemini-1-" not in model


def test_env_override_wins_over_default(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-test-override")
    from app.shared.gemini_config import get_gemini_model, gemini_generate_content_url
    assert get_gemini_model() == "gemini-test-override"
    assert gemini_generate_content_url() == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-test-override:generateContent"
    )


def test_no_env_override_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL_NAME", raising=False)
    from app.shared.gemini_config import get_gemini_model, gemini_generate_content_url
    model = get_gemini_model()
    assert gemini_generate_content_url() == (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )


def test_explicit_model_argument_overrides_env():
    from app.shared.gemini_config import gemini_generate_content_url
    url = gemini_generate_content_url("some-other-model")
    assert url.endswith("/some-other-model:generateContent")


# ---------------------------------------------------------------------------
# Every former hardcode site actually imports the shared helper, not its own
# copy of the string.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_path",
    [
        "app.services.qc_vision_provider",
        "app.services.rpas_validator",
        "app.services.compliance_validator",
        "app.integrations.bricks_ai_dispatch",
    ],
)
def test_call_site_has_no_hardcoded_retired_model_string(module_path):
    """No call site may build its own generateContent URL against the
    retired model — only mentions in explanatory comments (like this
    module's own incident writeup) are fine."""
    import importlib
    import inspect
    module = importlib.import_module(module_path)
    src = inspect.getsource(module)
    assert "gemini-1.5-flash:generateContent" not in src
    assert '"gemini-1.5-flash"' not in src
    assert "'gemini-1.5-flash'" not in src


@pytest.mark.parametrize(
    "module_path",
    [
        "app.services.qc_vision_provider",
        "app.services.rpas_validator",
        "app.services.compliance_validator",
        "app.integrations.bricks_ai_dispatch",
    ],
)
def test_call_site_imports_shared_gemini_config(module_path):
    import importlib
    import inspect
    module = importlib.import_module(module_path)
    src = inspect.getsource(module)
    assert "gemini_config import" in src


# ---------------------------------------------------------------------------
# QC vision provider — the most critical of the four. Simulate a Gemini
# outage (404) end to end and confirm: (a) the request actually targets the
# centralized model/URL, (b) the failure is logged at ERROR (previously this
# exact failure mode 404'd silently for as long as gemini-1.5-flash was
# dead), and (c) the fail-open contract (raise, let the caller decide) is
# preserved.
# ---------------------------------------------------------------------------

class _FakeErrorResponse:
    status_code = 404

    def raise_for_status(self):
        raise RuntimeError(
            "Client error '404 Not Found' for url "
            "'https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-1.5-flash:generateContent?key=x'"
        )

    def json(self):
        return {}


class _FakeOkResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "candidates": [
                {"content": {"parts": [{"text": '{"score": 95, "status": "passed", "issues": [], "notes": "ok"}'}]}}
            ]
        }


class _FakeAsyncClient:
    """Captures the URL each call was made against; response class is injected."""

    last_url = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        _FakeAsyncClient.last_url = url
        return self._response()

    def _response(self):
        raise NotImplementedError


def _client_returning(response_cls):
    class _Client(_FakeAsyncClient):
        def _response(self):
            return response_cls()
    return _Client


@pytest.mark.asyncio
async def test_run_vision_qc_uses_centralized_model_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
    from app.services import qc_vision_provider

    with patch("app.services.qc_vision_provider.httpx.AsyncClient", _client_returning(_FakeOkResponse)):
        result = await qc_vision_provider.run_vision_qc(b"\xff\xd8\xff fake jpeg", "prompt")

    assert result["status"] == "passed"
    assert "gemini-3.5-flash-lite:generateContent" in _FakeAsyncClient.last_url
    assert "gemini-1.5-flash" not in _FakeAsyncClient.last_url


@pytest.mark.asyncio
async def test_run_vision_qc_logs_error_and_still_raises_on_failure(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
    from app.services import qc_vision_provider

    with patch("app.services.qc_vision_provider.httpx.AsyncClient", _client_returning(_FakeErrorResponse)):
        with caplog.at_level(logging.ERROR, logger="app.services.qc_vision_provider"):
            with pytest.raises(Exception):
                await qc_vision_provider.run_vision_qc(b"\xff\xd8\xff fake jpeg", "prompt")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Gemini failure must be logged at ERROR, not swallowed silently"
    joined = " ".join(r.getMessage() for r in error_records)
    assert "gemini-3.5-flash-lite" in joined
    assert "404" in joined


@pytest.mark.asyncio
async def test_qc_agent_fails_open_and_logs_error_on_vision_failure(monkeypatch, caplog):
    """End-to-end through qc_agent.run_qc_on_upload: a Gemini outage must
    still deliver (fail open) but must no longer be invisible."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("USE_QC_AGENT", "true")
    from app.services import qc_agent

    async def _fake_fetch(url):
        return b"\xff\xd8\xff fake jpeg bytes"

    async def _fail(*a, **k):
        raise RuntimeError("404 Not Found (gemini-3.5-flash-lite)")

    with patch("app.services.qc_agent._fetch_image_bytes", _fake_fetch), \
         patch("app.services.qc_agent.run_vision_qc", _fail), \
         caplog.at_level(logging.ERROR, logger="app.services.qc_agent"):
        result = await qc_agent.run_qc_on_upload("upload-123", "https://example.com/photo.jpg")

    # Fail-open behavior preserved: never blocks delivery on a QC outage.
    assert result["qc_status"] == "passed"
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    assert "upload-123" in " ".join(r.getMessage() for r in error_records)


# ---------------------------------------------------------------------------
# RPAS + compliance validators: previously bare `except Exception as e:
# result["issues"] = [...]` with NO logger call at all.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rpas_gemini_failure_now_logs_error(monkeypatch, caplog):
    from app.services import rpas_validator

    async def _fail_extract(*a, **k):
        raise RuntimeError("404 Not Found")

    with patch("app.services.rpas_validator._fetch_image_bytes", return_value=b"bytes"), \
         patch("app.services.rpas_validator._ocr_extract", return_value={
             "cert_number": None, "level": None, "confidence": 0.0, "source": "ocr_unavailable",
         }), \
         patch("app.services.rpas_validator._gemini_extract", _fail_extract), \
         caplog.at_level(logging.ERROR, logger="app.services.rpas_validator"):
        result = await rpas_validator.validate_rpas_certificate(
            "https://example.com/cert.jpg", vendor_name="Jane Vendor"
        )

    assert result["outcome"] == "failed"
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "RPAS Gemini extraction failures must be logged, not silently swallowed"
    assert "Jane Vendor" in " ".join(r.getMessage() for r in error_records)


@pytest.mark.asyncio
async def test_compliance_gemini_failure_now_logs_error(monkeypatch, caplog):
    from app.services import compliance_validator

    async def _fail_extract(*a, **k):
        raise RuntimeError("404 Not Found")

    with patch("app.services.compliance_validator._fetch_document_bytes", return_value=b"bytes"), \
         patch("app.services.compliance_validator._gemini_extract", _fail_extract), \
         caplog.at_level(logging.ERROR, logger="app.services.compliance_validator"):
        result = await compliance_validator.validate_compliance_document(
            "https://example.com/coi.pdf", doc_type="coi"
        )

    assert result["outcome"] == "failed"
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Compliance Gemini extraction failures must be logged, not silently swallowed"
    assert "coi" in " ".join(r.getMessage() for r in error_records)


# ---------------------------------------------------------------------------
# Dispatch explainability: previously a bare `except Exception: return None`
# with NO log at all (not even debug).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_explanation_gemini_failure_now_logs_warning(monkeypatch, caplog):
    from app.integrations import bricks_ai_dispatch
    from app.shared.schemas import Job, VendorMember

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    job = Job(id="job-1", address="123 Main St", job_type="photo", time_window_start="09:00")
    vendor = VendorMember(id="v1", name="Vendor One", email="v1@test.com", specialty="photo")

    async def _raise_post(*a, **k):
        raise RuntimeError("404 Not Found")

    with patch("app.integrations.bricks_ai_dispatch.httpx.AsyncClient") as mock_client_cls, \
         caplog.at_level(logging.WARNING, logger="app.integrations.bricks_ai_dispatch"):
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post.side_effect = _raise_post
        explanation = await bricks_ai_dispatch.generate_dispatch_explanation(job, vendor, 87.0)

    assert explanation is None
    warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warn_records, "Dispatch explainability Gemini failures must be logged, not silently swallowed"
    assert "job-1" in " ".join(r.getMessage() for r in warn_records)
