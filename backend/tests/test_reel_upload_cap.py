"""Reel / video upload cap: 2 GB target, never above what storage accepts."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import media_intake as mi
from app.services import upload_backend as ub

MB = 1024 * 1024
REEL_4K = 79 * MB


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("UPLOAD_MAX_FILE_MB", "UPLOAD_MAX_VIDEO_MB", "SUPABASE_STORAGE_GLOBAL_MAX_MB", "STILLS_UPLOAD_BACKEND"):
        monkeypatch.delenv(k, raising=False)


def test_is_video_upload():
    assert mi.is_video_upload("master.mp4", "")
    assert mi.is_video_upload("x.bin", "video/quicktime")
    assert mi.is_video_upload("x.bin", "", "reel")
    assert not mi.is_video_upload("room_0.jpg", "image/jpeg", "hdr")


def test_supabase_free_reel_cap_stays_at_storage_ceiling():
    # Free plan: global 50 MB — never mint a cap the storage will reject.
    assert ub.max_file_bytes(video=True) == 50 * MB
    with pytest.raises(HTTPException) as ei:
        mi.assert_upload_size(REEL_4K, "master.mp4", "video/mp4", "reel")
    assert ei.value.status_code == 413
    assert "video is over 50 MB" in str(ei.value.detail)


def test_reel_cap_2gb_once_storage_allows(monkeypatch):
    monkeypatch.setenv("SUPABASE_STORAGE_GLOBAL_MAX_MB", "500000")  # Pro ceiling
    assert ub.max_file_bytes(video=True) == 2048 * MB
    mi.assert_upload_size(REEL_4K, "master.mp4", "video/mp4", "reel")  # accepted
    with pytest.raises(HTTPException) as ei:
        mi.assert_upload_size(2049 * MB, "master.mp4", "video/mp4", "reel")
    assert "over 2 GB" in str(ei.value.detail)
    # Stills unchanged
    assert ub.max_file_bytes() == 50 * MB


def test_reel_cap_never_above_2gb(monkeypatch):
    monkeypatch.setenv("SUPABASE_STORAGE_GLOBAL_MAX_MB", "500000")
    monkeypatch.setenv("UPLOAD_MAX_VIDEO_MB", "10000")
    assert ub.max_file_bytes(video=True) == 2048 * MB
    monkeypatch.setenv("UPLOAD_MAX_VIDEO_MB", "500")
    assert ub.max_file_bytes(video=True) == 500 * MB


def test_r2_reel_cap_2gb(monkeypatch):
    monkeypatch.setattr(ub, "backend_name", lambda: "r2")
    assert ub.max_file_bytes(video=True) == 2048 * MB
