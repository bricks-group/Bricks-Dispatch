"""No network / no runtime credentials: cold-start import regression."""
import subprocess
import sys
from pathlib import Path


def test_calendar_and_capture_import_does_not_load_numpy():
    code = "import sys; from app.services import equirect_stitcher; assert 'numpy' not in sys.modules, 'capture eagerly loads numpy'; assert equirect_stitcher.yaw_span_deg([0, 90]) == 90"
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_empty_reconcile_does_not_import_image_processing(monkeypatch):
    import asyncio
    import builtins
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.routers import post_production as pp
    class Empty:
        def __getattr__(self, name):
            if name == 'execute': return AsyncMock(return_value=SimpleNamespace(data=[]))
            return lambda *args, **kwargs: self
    monkeypatch.setenv('INTERNAL_API_KEYS', 'fixture-only')
    monkeypatch.delenv('FOTELLO_API_KEY', raising=False)
    monkeypatch.delenv('AUTOENHANCE_API_KEY', raising=False)
    monkeypatch.setenv('POSTPROD_STILLS_PROVIDER', 'inhouse')
    monkeypatch.setattr(pp, 'get_db', AsyncMock(return_value=Empty()))
    monkeypatch.setattr(pp, 'retry_autoenhance_holds', AsyncMock(return_value=[]))
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.endswith('photo_enhance_service'), 'empty reconcile imports image processing'
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    result = asyncio.run(pp.reconcile_stuck_uploads(x_internal_api_key='fixture-only', since='2026-11-01'))
    assert result['pil_tier3_applied'] == 0
