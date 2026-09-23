"""Dispatch: public floorplan URL for agent notify + stub refuse kept."""
import os
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_public_floorplan_helper_in_worker_source():
    src = (ROOT / "app/services/capture_worker.py").read_text()
    assert "def public_floorplan_url" in src
    assert "/api/public/floorplans/" in src
    assert "MIN_FLOORPLAN_PNG_BYTES = 200" in src
    assert "Floor-plan PNG too small" in src


def test_notify_prefers_public_floorplan_url():
    src = (ROOT / "app/routers/capture.py").read_text()
    fn = src.split("async def _notify_capture_session_delivered")[1].split(
        "async def _deliver_session"
    )[0]
    assert "public_floorplan_url" in fn
    assert "Floor plan" in fn


def test_register_property_visit_retries_once():
    src = (ROOT / "app/services/client_comms.py").read_text()
    fn = src.split("async def register_property_visit")[1].split("async def _load_job")[0]
    assert "visit-ready retry" in fn
    assert "for attempt in range(2)" in fn


def test_public_floorplan_url_runtime():
    from app.services.capture_worker import public_floorplan_url, MIN_FLOORPLAN_PNG_BYTES

    assert MIN_FLOORPLAN_PNG_BYTES == 200
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BRICKS_OS_API_URL", None)
        assert public_floorplan_url("job-1", "sess-9") == (
            "https://api.brxs.ca/api/public/floorplans/job-1/sess-9"
        )
    with patch.dict(os.environ, {"BRICKS_OS_API_URL": "https://os.example"}, clear=False):
        assert public_floorplan_url("j", "s") == "https://os.example/api/public/floorplans/j/s"
    assert public_floorplan_url("", "s") is None
