"""OpenCV QC can hold stills without Gemini; vision errors must not drop an OpenCV hold."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import app.services.qc_agent as qc


def _run(coro):
    return asyncio.run(coro)


def _cv(status: str, score: int, issues=None, source: str = "opencv"):
    return {
        "status": status,
        "score": score,
        "source": source,
        "issues": issues or [],
        "metrics": {},
    }


def test_vision_unconfigured_opencv_rejected_holds(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_vision_qc_configured", lambda: False)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=b"jpeg-bytes"))

    def fake_cv(_bytes, is_exterior=False):
        return _cv("rejected", 40, ["soft_focus"])

    monkeypatch.setattr("app.services.qc_opencv.analyze_image_cv", fake_cv)

    result = _run(qc.run_qc_on_upload("up-1", "https://example.test/a.jpg"))
    assert result["qc_status"] == "rejected"
    assert result["qc_score"] == 40
    assert result["qc_source"] == "opencv"


def test_vision_unconfigured_opencv_passed(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_vision_qc_configured", lambda: False)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=b"jpeg-bytes"))
    monkeypatch.setattr(
        "app.services.qc_opencv.analyze_image_cv",
        lambda *_a, **_k: _cv("passed", 95),
    )

    result = _run(qc.run_qc_on_upload("up-2", "https://example.test/a.jpg"))
    assert result["qc_status"] == "passed"
    assert result["qc_score"] == 95
    assert result["qc_source"] == "opencv"


def test_vision_raises_keeps_opencv_rejected(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_vision_qc_configured", lambda: True)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=b"jpeg-bytes"))
    monkeypatch.setattr(
        "app.services.qc_opencv.analyze_image_cv",
        lambda *_a, **_k: _cv("rejected", 35, ["blown_highlights"]),
    )

    async def boom(*_a, **_k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(qc, "run_vision_qc", boom)

    result = _run(qc.run_qc_on_upload("up-3", "https://example.test/a.jpg"))
    assert result["qc_status"] == "rejected"
    assert result["qc_score"] == 35
    assert result["qc_source"] == "opencv"
    assert "vision error" in result["qc_notes"].lower() or "RuntimeError" in result["qc_notes"]


def test_vision_raises_opencv_passed_fail_open(monkeypatch):
    """High-value package forces vision even after OpenCV pass; vision error fail-opens."""
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_vision_qc_configured", lambda: True)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=b"jpeg-bytes"))
    monkeypatch.setattr(
        "app.services.qc_opencv.analyze_image_cv",
        lambda *_a, **_k: _cv("passed", 96),
    )

    async def boom(*_a, **_k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(qc, "run_vision_qc", boom)

    result = _run(
        qc.run_qc_on_upload("up-4", "https://example.test/a.jpg", package="luxury")
    )
    assert result["qc_status"] == "passed"
    assert result["qc_source"] == "skipped"
    assert "error" in result["qc_notes"].lower()


def test_qc_agent_disabled_passed(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", False)
    # Should not fetch or analyze when disabled
    monkeypatch.setattr(
        qc,
        "_fetch_image_bytes",
        AsyncMock(side_effect=AssertionError("must not fetch when disabled")),
    )

    result = _run(qc.run_qc_on_upload("up-5", "https://example.test/a.jpg"))
    assert result["qc_status"] == "passed"
    assert result["qc_source"] == "disabled"
    assert result["qc_score"] == 100
