"""RAW intake (shoot standard v0.2 requires RAW; Kam confirmed 2026-11-07).

Default ON: CR2/CR3/DNG/NEF/ARW are accepted with a stable image/x-* type.
RAW_INTAKE_ENABLED=0 is an explicit kill switch that returns a clear 415.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import media_intake as mi

RAWS = ["IMG_0001.CR2", "IMG_0002.CR3", "DSC_0003.NEF", "_DSC0004.ARW", "DJI_0005.DNG"]


@pytest.mark.parametrize("name", RAWS)
def test_raw_accepted_by_default(monkeypatch, name):
    monkeypatch.delenv("RAW_INTAKE_ENABLED", raising=False)
    assert mi.raw_intake_enabled() is True
    assert mi.is_camera_raw_file(name, "application/octet-stream")
    mi.assert_listing_still_decodable(name, "application/octet-stream", None)  # no raise
    ctype = mi.upload_content_type(name, "application/octet-stream")
    assert ctype.startswith("image/x-")


@pytest.mark.parametrize("name", RAWS)
def test_raw_routes_to_photo_pipeline_not_as_is(name):
    row = {"file_url": f"jobs/j/u/{name}", "filename": name}
    # Drone DNG (DJI_) is aerial → as_is, which holds RAW for review (never delivers RAW).
    expected = "as_is" if name.startswith("DJI_") else "vendor_photo"
    assert mi.delivery_route(row) == expected


@pytest.mark.parametrize("name", RAWS)
def test_kill_switch_rejects_raw_with_415(monkeypatch, name):
    monkeypatch.setenv("RAW_INTAKE_ENABLED", "0")
    with pytest.raises(HTTPException) as exc:
        mi.assert_listing_still_decodable(name, "", None)
    assert exc.value.status_code == 415
    assert exc.value.detail["code"] == "raw_intake_disabled"


def test_kill_switch_never_blocks_jpeg(monkeypatch):
    monkeypatch.setenv("RAW_INTAKE_ENABLED", "off")
    mi.assert_listing_still_decodable("IMG_0001.JPG", "image/jpeg", None)


def test_raw_over_size_cap_gets_raw_specific_413(monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_FILE_MB", "50")
    with pytest.raises(HTTPException) as exc:
        mi.assert_upload_size(80 * 1024 * 1024, "IMG_0002.CR3", "", None)
    assert exc.value.status_code == 413
    assert "RAW" in exc.value.detail["message"]
