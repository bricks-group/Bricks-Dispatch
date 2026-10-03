"""Floorplan / CubiCasa / PDF leftovers must deliver as_is — no HDR enhance."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

from app.services import post_production as pp
from app.services.media_intake import (
    delivery_route,
    durable_media_filename,
    infer_media_type,
    is_listing_still_delivery_row,
    is_pdf_file,
    is_photo_file,
)


def _run(coro):
    return asyncio.run(coro)


def test_infer_and_route_floorplan_filename_cues():
    assert infer_media_type("floorplan_main.png") == "floorplan"
    assert infer_media_type("floor_plan_main.pdf", "application/pdf") == "floorplan"
    assert infer_media_type("floor-plan_main.png") == "floorplan"
    assert infer_media_type("cubicasa_export.png") == "floorplan"
    assert infer_media_type("roomplan_1.png") == "floorplan"
    assert infer_media_type("room_plan_1.png") == "floorplan"
    assert infer_media_type(explicit="floorplan") == "floorplan"
    assert infer_media_type(explicit="floor_plan") == "floorplan"
    assert infer_media_type(explicit="plan") == "floorplan"
    assert infer_media_type(explicit="plans") == "floorplan"
    # CubiCasa / broker basename plan.png — must NOT HDR + 4:3 crop
    assert infer_media_type("plan.png") == "floorplan"
    assert infer_media_type("plans.PNG") == "floorplan"
    # Substring must not false-positive ordinary stills
    assert infer_media_type("airplane.jpg") == "hdr"
    assert infer_media_type("explainer.png") == "hdr"

    assert delivery_route({
        "file_url": "p/floorplan_main.png",
        "filename": "floorplan_main.png",
    }) == "as_is"
    assert delivery_route({
        "file_url": "p/plan.png",
        "filename": "plan.png",
    }) == "as_is"
    assert delivery_route({
        "file_url": "p/export.png",
        "filename": "export.png",
        "media_type": "plan",
    }) == "as_is"
    assert delivery_route({
        "file_url": "p/living.jpg",
        "filename": "living.jpg",
    }) == "vendor_photo"
    assert delivery_route({
        "file_url": "p/floor_plan_main.pdf",
        "filename": "floor_plan_main.pdf",
        "mime_type": "application/pdf",
    }) == "as_is"
    assert delivery_route({
        "file_url": "p/cubicasa_export.png",
        "filename": "cubicasa_export.png",
    }) == "as_is"
    # Aerial drone still correct
    assert delivery_route({
        "file_url": "p/aerial_drone.jpg",
        "filename": "aerial_drone.jpg",
    }) == "as_is"


def test_plain_pdf_as_is_even_without_floorplan_cue():
    assert is_pdf_file("plan.pdf")
    assert is_pdf_file("x.bin", "application/pdf")
    assert not is_photo_file("plan.pdf", "application/pdf", "")
    assert delivery_route({
        "file_url": "p/plan.pdf",
        "filename": "plan.pdf",
        "mime_type": "application/pdf",
    }) == "as_is"
    # Empty media_type must not make PDF look like a photo via PHOTO_TYPES ""
    assert delivery_route({
        "file_url": "p/brochure.pdf",
        "filename": "brochure.pdf",
    }) == "as_is"


def test_durable_media_filename_prefixes_floorplan():
    assert durable_media_filename("IMG_0001.PNG", "floorplan") == "floorplan_IMG_0001.PNG"
    assert durable_media_filename("plan.pdf", "floor_plan") == "plan.pdf"  # stem plan already cues
    assert durable_media_filename("export.png", "plan") == "floorplan_export.png"
    assert durable_media_filename("plan.png", "plan") == "plan.png"  # stem already cues
    assert durable_media_filename("floorplan_main.png", "floorplan") == "floorplan_main.png"
    assert durable_media_filename("cubicasa_export.png", "floorplan") == "cubicasa_export.png"
    # durable alone routes without media_type col
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/IMG_0001.PNG",
        "filename": durable_media_filename("IMG_0001.PNG", "floorplan"),
    }) == "as_is"


def test_listing_still_delivery_keeps_floorplan_png_and_pdf():
    assert is_listing_still_delivery_row({
        "filename": "floorplan_main.png",
        "provider": "as_is",
        "processed_url": "p/floorplan_main.png",
    })
    assert is_listing_still_delivery_row({
        "filename": "floorplan_plan.pdf",
        "provider": "as_is",
        "processed_url": "p/floorplan_plan.pdf",
    })
    assert not is_listing_still_delivery_row({
        "filename": "tour.splat",
        "provider": "visit",
    })


def test_deliver_floorplan_skips_enhance_and_marks_as_is(monkeypatch):
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    fake_pes = ModuleType("app.services.photo_enhance_service")

    def _boom(*_a, **_k):
        raise AssertionError("enhance must not be called for floorplan")

    fake_pes.enhance_image = _boom
    fake_pes.enhance_bracket = _boom
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    asset = {
        "id": "up-fp",
        "file_url": "bricks-go/jobs/j1/up-fp/floorplan_main.png",
        "filename": "floorplan_main.png",
    }
    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-fp"
    assert update["processing_status"] == "done"
    assert update["processed_url"] == asset["file_url"]
    assert update["provider"] == "as_is"


def test_deliver_pdf_skips_enhance(monkeypatch):
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    fake_pes = ModuleType("app.services.photo_enhance_service")

    def _boom(*_a, **_k):
        raise AssertionError("enhance must not be called for PDF")

    fake_pes.enhance_image = _boom
    fake_pes.enhance_bracket = _boom
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    asset = {
        "id": "up-pdf",
        "file_url": "bricks-go/jobs/j1/up-pdf/plan.pdf",
        "filename": "plan.pdf",
        "mime_type": "application/pdf",
    }
    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))
    assert updates[0][1]["provider"] == "as_is"
    assert updates[0][1]["processed_url"] == asset["file_url"]


def test_leftovers_route_floorplan_to_others_not_hdr_photos(monkeypatch):
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(photos):
        grouped_inputs.append([a["id"] for a in photos])
        assert all("floorplan" not in str(a.get("filename") or "").lower() for a in photos)
        assert all(not str(a.get("filename") or "").lower().endswith(".pdf") for a in photos)
        if not photos:
            return []
        return [list(photos)]

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.group_assets_by_bracket = fake_group
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "deliver_asset_without_vendor", fake_deliver)
    monkeypatch.setattr(pp, "deliver_bracket_group_without_vendor", fake_bracket)

    leftover = [
        {
            "id": "up-photo-0",
            "filename": "living-0.jpg",
            "file_url": "p/living-0.jpg",
        },
        {
            "id": "up-fp",
            "filename": "floorplan_main.png",
            "file_url": "p/floorplan_main.png",
        },
        {
            "id": "up-pdf",
            "filename": "plan.pdf",
            "file_url": "p/plan.pdf",
            "mime_type": "application/pdf",
        },
        {
            "id": "up-cubi",
            "filename": "cubicasa_export.png",
            "file_url": "p/cubicasa_export.png",
        },
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert "up-fp" in delivered
    assert "up-pdf" in delivered
    assert "up-cubi" in delivered
    assert grouped_inputs == [["up-photo-0"]]
    assert bracket_calls == []


def test_listing_still_delivery_urls_tags_floorplan_kind(monkeypatch):
    """GO CubiCasa/PDF leftovers stay in delivery_urls but with kind=floorplan."""
    from app.routers import post_production as router_pp

    async def fake_signed(path):
        return f"https://cdn.example/signed/{path}"

    monkeypatch.setattr(router_pp, "get_signed_download_url", fake_signed)

    uploads = [
        {
            "id": "up-photo",
            "filename": "living-0.jpg",
            "processed_url": "p/living-0.jpg",
            "processing_status": "done",
            "qc_status": "passed",
            "provider": "bricks_inhouse",
        },
        {
            "id": "up-fp",
            "filename": "floorplan_main.png",
            "processed_url": "p/floorplan_main.png",
            "processing_status": "done",
            "qc_status": "passed",
            "provider": "as_is",
        },
        {
            "id": "up-cubi",
            "filename": "cubicasa_export.png",
            "file_url": "p/cubicasa_export.png",
            "processing_status": "done",
            "provider": "as_is",
        },
        {
            "id": "up-pdf",
            "filename": "plan.pdf",
            "file_url": "p/plan.pdf",
            "mime_type": "application/pdf",
            "processing_status": "done",
            "provider": "as_is",
        },
    ]
    urls = _run(router_pp._listing_still_delivery_urls(uploads))
    by_id = {u["upload_id"]: u for u in urls}
    assert "up-photo" in by_id
    assert "kind" not in by_id["up-photo"]
    assert by_id["up-fp"]["kind"] == "floorplan"
    assert by_id["up-fp"]["filename"] == "floorplan_main.png"
    assert by_id["up-fp"]["url"]
    assert by_id["up-cubi"]["kind"] == "floorplan"
    assert by_id["up-pdf"]["kind"] == "floorplan"
    # Keep in listing still delivery (Companion + late-fold need them)
    assert is_listing_still_delivery_row(uploads[1])



def test_listing_still_delivery_urls_tags_drone_reel_kind(monkeypatch):
    """Aerial/DJI/reel leftovers stay in delivery_urls with kind for gallery sectioning."""
    from app.routers import post_production as router_pp

    async def fake_signed(path):
        return f"https://cdn.example/signed/{path}"

    monkeypatch.setattr(router_pp, "get_signed_download_url", fake_signed)

    uploads = [
        {
            "id": "up-photo",
            "filename": "living-0.jpg",
            "processed_url": "p/living-0.jpg",
            "processing_status": "done",
            "qc_status": "passed",
            "provider": "bricks_inhouse",
        },
        {
            "id": "up-dji",
            "filename": "DJI_0042.JPG",
            "processed_url": "p/DJI_0042.JPG",
            "processing_status": "done",
            "provider": "as_is",
        },
        {
            "id": "up-aerial",
            "filename": "aerial-front.jpg",
            "file_url": "p/aerial-front.jpg",
            "processing_status": "done",
            "provider": "as_is",
        },
        {
            "id": "up-reel",
            "filename": "reel_clip.mp4",
            "file_url": "p/reel_clip.mp4",
            "mime_type": "video/mp4",
            "processing_status": "done",
            "provider": "as_is",
        },
    ]
    urls = _run(router_pp._listing_still_delivery_urls(uploads))
    by_id = {u["upload_id"]: u for u in urls}
    assert "kind" not in by_id["up-photo"]
    assert by_id["up-dji"]["kind"] == "drone"
    assert by_id["up-aerial"]["kind"] == "drone"
    assert by_id["up-reel"]["kind"] == "reel"



def test_listing_still_delivery_urls_tags_staging_twilight_kind(monkeypatch):
    """Staging/twilight leftovers stay in delivery_urls (Photos) with kind stamps."""
    from app.routers import post_production as router_pp

    async def fake_signed(path):
        return f"https://cdn.example/signed/{path}"

    monkeypatch.setattr(router_pp, "get_signed_download_url", fake_signed)

    uploads = [
        {
            "id": "up-photo",
            "filename": "living-0.jpg",
            "processed_url": "p/living-0.jpg",
            "processing_status": "done",
            "qc_status": "passed",
            "provider": "bricks_inhouse",
        },
        {
            "id": "up-stage",
            "filename": "staging_kitchen.jpg",
            "processed_url": "p/staging_kitchen.jpg",
            "processing_status": "done",
            "provider": "as_is",
        },
        {
            "id": "up-twilight",
            "filename": "twilight_rear.jpg",
            "file_url": "p/twilight_rear.jpg",
            "processing_status": "done",
            "provider": "bricks_inhouse",
        },
        {
            "id": "up-dusk-alias",
            "filename": "rear.jpg",
            "file_url": "p/rear.jpg",
            "media_type": "dusk",
            "processing_status": "done",
            "provider": "bricks_inhouse",
        },
        {
            "id": "up-vs",
            "filename": "room.jpg",
            "file_url": "p/room.jpg",
            "media_type": "virtual_staging",
            "processing_status": "done",
            "provider": "as_is",
        },
    ]
    urls = _run(router_pp._listing_still_delivery_urls(uploads))
    by_id = {u["upload_id"]: u for u in urls}
    assert "kind" not in by_id["up-photo"]
    assert by_id["up-stage"]["kind"] == "staging"
    assert by_id["up-twilight"]["kind"] == "twilight"
    assert by_id["up-dusk-alias"]["kind"] == "twilight"
    assert by_id["up-vs"]["kind"] == "staging"


def test_delivery_url_kind_for_upload_filename_cues():
    from app.routers.post_production import _delivery_url_kind_for_upload

    assert _delivery_url_kind_for_upload({"filename": "floorplan_main.png"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "floor_plan_a.png"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "cubicasa_export.png"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "roomplan_1.png"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "plan.pdf"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "x.bin", "mime_type": "application/pdf"}) == "floorplan"
    assert _delivery_url_kind_for_upload({"filename": "living-0.jpg"}) is None
    # Drone / reel leftovers — OS listing sections by kind (Photos must not absorb)
    assert _delivery_url_kind_for_upload({"filename": "aerial-front.jpg"}) == "drone"
    assert _delivery_url_kind_for_upload({"filename": "DJI_0042.JPG"}) == "drone"
    assert _delivery_url_kind_for_upload({"filename": "aerial_IMG_9.JPG"}) == "drone"
    assert _delivery_url_kind_for_upload({"filename": "reel_clip.mp4", "mime_type": "video/mp4"}) == "reel"
    assert _delivery_url_kind_for_upload({"filename": "walkthrough.mov", "mime_type": "video/quicktime"}) == "reel"
    # staging/twilight stamped for partition honesty; still belong in Photos
    assert _delivery_url_kind_for_upload({"filename": "twilight_rear.jpg"}) == "twilight"
    assert _delivery_url_kind_for_upload({"filename": "staging_kitchen.jpg"}) == "staging"
    assert _delivery_url_kind_for_upload({"filename": "dusk_front.jpg"}) == "twilight"
    assert _delivery_url_kind_for_upload({"filename": "golden_hour.jpg"}) == "twilight"
    assert _delivery_url_kind_for_upload({"filename": "x.jpg", "media_type": "virtual_staging"}) == "staging"
    assert _delivery_url_kind_for_upload({"filename": "x.jpg", "media_type": "dusk"}) == "twilight"
    assert _delivery_url_kind_for_upload({"filename": "x.jpg", "media_type": "day_to_dusk"}) == "twilight"
    assert _delivery_url_kind_for_upload({"filename": "x.jpg", "media_type": "stage"}) == "staging"
    assert _delivery_url_kind_for_upload({"filename": "living-0.jpg", "media_type": "hdr"}) is None
    assert _delivery_url_kind_for_upload({"filename": "living-0.jpg", "media_type": "photo"}) is None


def test_listing_delivery_filename_jpeg_when_processed_is_jpg():
    """HEIC/PNG/WebP capture + JPEG processed_url → advertise .jpg (Companion honesty)."""
    from app.routers.post_production import _listing_delivery_filename, _still_delivery_url_entry

    assert _listing_delivery_filename({
        "filename": "staging_room.heic",
        "processed_url": "enhanced/job/u-heic.jpg",
        "file_url": "raw/staging_room.heic",
    }) == "staging_room.jpg"
    assert _listing_delivery_filename({
        "filename": "living.png",
        "processed_url": "enhanced/job/u.png.jpg",
        "mime_type": "image/png",
    }) == "living.jpg"
    assert _listing_delivery_filename({
        "filename": "phone.webp",
        "processed_url": "enhanced/job/u.webp.jpg",
    }) == "phone.jpg"
    # Already JPEG — keep
    assert _listing_delivery_filename({
        "filename": "living-0.jpg",
        "processed_url": "enhanced/job/u.jpg",
    }) == "living-0.jpg"
    # Floorplan PNG as_is — processed stays PNG, keep extension
    assert _listing_delivery_filename({
        "filename": "floorplan_main.png",
        "processed_url": "p/floorplan_main.png",
    }) == "floorplan_main.png"
    # HEIC convert failed — processed still HEIC
    assert _listing_delivery_filename({
        "filename": "aerial_yard.heic",
        "processed_url": "raw/drone.heic",
    }) == "aerial_yard.heic"
    # PDF untouched
    assert _listing_delivery_filename({
        "filename": "plan.pdf",
        "processed_url": "raw/plan.pdf",
        "mime_type": "application/pdf",
    }) == "plan.pdf"

    entry = _still_delivery_url_entry(
        {
            "id": "u1",
            "filename": "staging_room.heic",
            "processed_url": "enhanced/job/u-heic.jpg",
            "qc_status": "passed",
        },
        "https://cdn.example/u.jpg",
        "2099-01-01T00:00:00+00:00",
    )
    assert entry["filename"] == "staging_room.jpg"
    assert entry["upload_id"] == "u1"
