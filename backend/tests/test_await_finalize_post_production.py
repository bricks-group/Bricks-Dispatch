"""Regression: Vercel drops BackgroundTasks after response — finalize must await."""
from __future__ import annotations

from pathlib import Path

from app.routers import post_production as pp


def test_callbacks_and_reconcile_await_finalize_not_background_tasks():
    """Hollow media_delivery if _finalize_job_if_complete is only scheduled via BackgroundTasks."""
    src = Path(pp.__file__).read_text()
    assert "background_tasks.add_task(_finalize_job_if_complete" not in src
    assert "await _finalize_job_if_complete" in src
    # Callbacks + reconcile loop
    assert src.count("await _finalize_job_if_complete") >= 4
    # PIL-rescued jobs must be tracked for finalize (not only remaining stuck)
    assert "job_ids_to_finalize" in src
