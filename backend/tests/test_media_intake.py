from fastapi import HTTPException

from app.services.media_intake import (
    CAMERA_RAW_EXTS,
    IMAGE_EXTS,
    assert_listing_still_decodable,
    delivery_route,
    durable_media_filename,
    infer_media_type,
    is_camera_raw_file,
    is_listing_still_delivery_row,
    is_photo_delivery_candidate,
    is_photo_file,
    is_video_file,
    is_visit_file,
    pick_visit_upload,
    stills_storage_path,
    wants_staging,
)


def test_infer_explicit_and_aliases():
    assert infer_media_type(explicit="HDR") == "hdr"
    assert infer_media_type(explicit="photo") == "hdr"
    assert infer_media_type(explicit="drone") == "drone"
    assert infer_media_type(explicit="cinematic_reel") == "reel"
    assert infer_media_type(explicit="virtual_staging") == "staging"


def test_infer_from_filename_and_mime():
    assert infer_media_type("DJI_0042.MP4", "video/mp4") == "drone"
    assert infer_media_type("aerial-front.jpg") == "drone"
    assert infer_media_type("twilight-rear.jpg") == "twilight"
    assert infer_media_type("living-stage.jpg") == "staging"
    assert infer_media_type("floorplan_main.png") == "floorplan"
    assert infer_media_type("cubicasa_export.png") == "floorplan"
    assert infer_media_type("reel-cut.mov", "video/quicktime") == "reel"
    assert infer_media_type("living-01.JPG", "image/jpeg") == "hdr"


def test_photo_vs_video():
    assert is_photo_file("a.jpg", "image/jpeg", "hdr")
    assert is_photo_file("dusk.jpg", "image/jpeg", "twilight")
    assert not is_photo_file("clip.mp4", "video/mp4", "reel")
    assert is_video_file("clip.mp4", "video/mp4")
    assert is_video_file("x.bin", media_type="drone")


def test_delivery_routes():
    assert delivery_route({"file_url": "p/a.jpg", "filename": "a.jpg", "media_type": "hdr"}) == "vendor_photo"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "stage.jpg", "media_type": "staging"}) == "staging"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "twilight-front.jpg", "media_type": "twilight"}) == "twilight"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "dusk-rear.jpg", "media_type": "dusk"}) == "twilight"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "golden-hour.jpg"}) == "twilight"
    assert delivery_route({"file_url": "p/a.mp4", "filename": "drone.mp4", "media_type": "drone"}) == "as_is"
    # media_type is not a DB column — filename inference must keep aerial JPEGs as_is
    # (never vendor_photo + HDR/sky invent on drone stills).
    assert delivery_route({"file_url": "p/a.jpg", "filename": "aerial-front.jpg"}) == "as_is"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "DJI_0042.JPG"}) == "as_is"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "drone-front.jpg"}) == "as_is"
    assert delivery_route({"file_url": "p/a.jpg", "filename": "aerial-front.jpg", "media_type": "drone"}) == "as_is"
    assert delivery_route({"file_url": "p/a.splat", "filename": "visit.splat"}) == "visit"
    assert delivery_route({"filename": "a.jpg"}) == "skip"
    assert wants_staging({"requires_staging": True})
    assert wants_staging({"media_type": "staging", "filename": "x.jpg"})


def test_stills_path_stays_under_job():
    path = stills_storage_path("job-1", "up-9", "../../other/x.jpg")
    assert path == "bricks-go/jobs/job-1/up-9/x.jpg"



def test_visit_exts_all_three():
    for name in ("tour.splat", "scan.PLY", "cloud.spz", "Visit.SPZ"):
        assert is_visit_file(name)
        assert not is_photo_delivery_candidate(name)
        assert not is_photo_file(name)
    assert is_photo_delivery_candidate("living-01.jpg")
    assert delivery_route({"file_url": "p/a.ply", "filename": "a.ply"}) == "visit"
    assert delivery_route({"file_url": "p/a.spz", "filename": "a.spz"}) == "visit"


def test_pick_visit_upload_prefers_splat_then_ply_then_spz():
    assert pick_visit_upload([]) is None
    assert pick_visit_upload([{"filename": "a.jpg"}]) is None

    only_spz = pick_visit_upload([{"id": "1", "filename": "room.spz", "qc_status": "passed"}])
    assert only_spz["id"] == "1"

    only_ply = pick_visit_upload([
        {"id": "2", "filename": "room.ply", "qc_status": "passed"},
        {"id": "3", "filename": "a.jpg"},
    ])
    assert only_ply["id"] == "2"

    mixed = pick_visit_upload([
        {"id": "spz", "filename": "a.spz", "qc_status": "passed"},
        {"id": "ply", "filename": "b.ply", "qc_status": "passed"},
        {"id": "splat", "filename": "c.splat", "qc_status": "passed"},
        {"id": "jpg", "filename": "d.jpg"},
    ])
    assert mixed["id"] == "splat"

    ply_over_spz = pick_visit_upload([
        {"id": "spz", "filename": "a.spz", "qc_status": "passed"},
        {"id": "ply", "filename": "b.ply", "qc_status": "passed"},
    ])
    assert ply_over_spz["id"] == "ply"


def test_filter_photo_delivery_excludes_all_visit_exts():
    uploads = [
        {"filename": "living.jpg"},
        {"filename": "tour.splat"},
        {"filename": "scan.ply"},
        {"filename": "cloud.spz"},
        {"filename": "reel.mp4"},
    ]
    photos = [u for u in uploads if is_photo_delivery_candidate(u.get("filename"))]
    assert [u["filename"] for u in photos] == ["living.jpg", "reel.mp4"]


def test_listing_still_delivery_excludes_bracket_siblings():
    """HDR −/0/+ merge marks non-primary frames provider=bracket_sibling."""
    primary = {
        "filename": "living-0.jpg",
        "provider": "bricks_inhouse",
        "processed_url": "jobs/j/merged.jpg",
    }
    sibling_minus = {
        "filename": "living--1.jpg",
        "provider": "bracket_sibling",
        "processed_url": "jobs/j/merged.jpg",
    }
    sibling_plus = {
        "filename": "living-+1.jpg",
        "provider": "bracket_sibling",
        "processed_url": "jobs/j/merged.jpg",
    }
    visit = {"filename": "tour.splat", "provider": "bricks_inhouse"}
    assert is_listing_still_delivery_row(primary)
    assert is_listing_still_delivery_row({"filename": "a.jpg"})  # no provider → include
    assert not is_listing_still_delivery_row(sibling_minus)
    assert not is_listing_still_delivery_row(sibling_plus)
    assert not is_listing_still_delivery_row(visit)
    # filename-only helper still Visit-only (backward compatible)
    assert is_photo_delivery_candidate(sibling_minus["filename"])
    assert not is_photo_delivery_candidate("tour.ply")


def test_filter_listing_stills_keeps_one_per_bracket():
    uploads = [
        {"filename": "living--1.jpg", "provider": "bracket_sibling"},
        {"filename": "living-0.jpg", "provider": "bricks_inhouse"},
        {"filename": "living-+1.jpg", "provider": "bracket_sibling"},
        {"filename": "kitchen.jpg", "provider": "bricks_inhouse"},
        {"filename": "tour.splat", "provider": "bricks_inhouse"},
    ]
    kept = [u["filename"] for u in uploads if is_listing_still_delivery_row(u)]
    assert kept == ["living-0.jpg", "kitchen.jpg"]


def test_listing_still_excludes_provider_visit():
    """Parity with OS: provider=visit must never surface as a listing still."""
    assert not is_listing_still_delivery_row({
        "filename": "scan.bin",
        "provider": "visit",
        "processed_url": "visits/j/scan.bin",
    })
    assert is_listing_still_delivery_row({
        "filename": "living.jpg",
        "provider": "bricks_inhouse",
    })


def test_pick_visit_upload_includes_provider_visit():
    """GO enhance marks Visit rows provider=visit even if filename is odd."""
    row = {
        "id": "v1",
        "filename": "mesh.bin",
        "provider": "visit",
        "file_url": "visits/j/mesh.bin",
        "qc_status": "passed",
    }
    assert pick_visit_upload([{"filename": "a.jpg"}, row])["id"] == "v1"
    assert pick_visit_upload([{"filename": "a.jpg", "provider": "bricks_inhouse"}]) is None


def test_pick_visit_upload_skips_qc_rejected():
    """Broken Visit held by container QC must not register on the listing."""
    held = {
        "id": "bad",
        "filename": "room.splat",
        "provider": "visit",
        "qc_status": "rejected",
        "file_url": "visits/j/room.splat",
    }
    ok = {
        "id": "good",
        "filename": "room.ply",
        "provider": "visit",
        "qc_status": "passed",
        "file_url": "visits/j/room.ply",
    }
    assert pick_visit_upload([held]) is None
    assert pick_visit_upload([held, ok])["id"] == "good"
    # Prefer .splat over .ply when both pass — rejected splat must not win
    held_splat = {**held, "id": "bad-splat", "filename": "room.splat"}
    assert pick_visit_upload([held_splat, ok])["id"] == "good"


def test_durable_media_filename_prefixes_and_no_double():
    assert durable_media_filename("IMG_0001.JPG", "twilight") == "twilight_IMG_0001.JPG"
    assert durable_media_filename("IMG_0001.JPG", "drone") == "aerial_IMG_0001.JPG"
    assert durable_media_filename("living.jpg", "staging") == "staging_living.jpg"
    assert durable_media_filename("IMG_1.JPG", "reel") == "reel_IMG_1.JPG"
    # hdr / photo listing stills unchanged
    assert durable_media_filename("IMG_0001.JPG", "hdr") == "IMG_0001.JPG"
    assert durable_media_filename("IMG_0001.JPG", "photo") == "IMG_0001.JPG"
    # already cued → unchanged (no double prefix)
    assert durable_media_filename("DJI_0042.JPG", "drone") == "DJI_0042.JPG"
    assert durable_media_filename("aerial_front.jpg", "drone") == "aerial_front.jpg"
    assert durable_media_filename("twilight_IMG_0001.JPG", "twilight") == "twilight_IMG_0001.JPG"
    assert durable_media_filename("staging_living.jpg", "staging") == "staging_living.jpg"
    assert durable_media_filename("reel_cut.mp4", "reel") == "reel_cut.mp4"
    assert durable_media_filename("IMG_1.PNG", "floorplan") == "floorplan_IMG_1.PNG"
    assert durable_media_filename("floorplan_main.png", "floorplan") == "floorplan_main.png"
    # path-safe basename only
    assert durable_media_filename("../../other/IMG_9.JPG", "twilight") == "twilight_IMG_9.JPG"


def test_durable_filename_delivery_route_without_media_type_col():
    """DB row has no media_type — durable filename alone must route honestly."""
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/IMG_0001.JPG",
        "filename": durable_media_filename("IMG_0001.JPG", "twilight"),
    }) == "twilight"
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/IMG_0001.JPG",
        "filename": durable_media_filename("IMG_0001.JPG", "drone"),
    }) == "as_is"
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/living.jpg",
        "filename": durable_media_filename("living.jpg", "staging"),
    }) == "staging"
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/IMG_1.PNG",
        "filename": durable_media_filename("IMG_1.PNG", "floorplan"),
    }) == "as_is"
    assert delivery_route({
        "file_url": "bricks-go/jobs/j/u/IMG_0001.JPG",
        "filename": durable_media_filename("IMG_0001.JPG", "hdr"),
    }) == "vendor_photo"
    # bare IMG with no durable cue still falls to vendor_photo (the bug we fix at register)
    assert delivery_route({
        "file_url": "p/IMG_0001.JPG",
        "filename": "IMG_0001.JPG",
    }) == "vendor_photo"


def test_infer_reel_cue_and_file_url_basename():
    assert infer_media_type("reel_IMG_1.JPG") == "reel"
    assert infer_media_type(None, None, None, file_url="bricks-go/jobs/j/u/twilight_x.jpg") == "twilight"


def test_image_exts_include_heif():
    assert ".heif" in IMAGE_EXTS
    assert ".heic" in IMAGE_EXTS
    assert is_photo_file("shot.heif", "image/heif", "hdr")
    assert is_photo_file("shot.HEIC", "image/heic", "hdr")


def test_is_camera_raw_file_ext_and_mime():
    for name in ("a.CR3", "b.DNG", "c.cr2", "d.NEF", "e.arw", "f.raf", "g.orf"):
        assert is_camera_raw_file(name)
    assert is_camera_raw_file("x.bin", "image/x-adobe-dng")
    assert is_camera_raw_file(None, "image/x-canon-cr3")
    assert is_camera_raw_file("foo.JPG", "image/x-nikon-nef")  # mime wins
    assert not is_camera_raw_file("living.jpg")
    assert not is_camera_raw_file("shot.HEIC", "image/heic")
    assert not is_camera_raw_file("clip.MP4", "video/mp4")
    assert not is_camera_raw_file("tour.splat")
    assert not is_camera_raw_file("a.png", "image/png")
    # required coverage set present
    for ext in (".cr2", ".cr3", ".dng", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw", ".x3f", ".raw"):
        assert ext in CAMERA_RAW_EXTS


def test_assert_listing_still_decodable_rejects_raw():
    try:
        assert_listing_still_decodable("IMG_0001.CR3")
        raise AssertionError("expected api_error")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail["code"] == "camera_raw_unsupported"
        assert "JPEG" in exc.detail["message"] or "HEIC" in exc.detail["message"]

    try:
        assert_listing_still_decodable("x.bin", "image/x-adobe-dng")
        raise AssertionError("expected api_error")
    except HTTPException as exc:
        assert exc.detail["code"] == "camera_raw_unsupported"

    # visit / video / JPEG / HEIC pass through
    assert_listing_still_decodable("tour.splat")
    assert_listing_still_decodable("reel.mp4", "video/mp4", "reel")
    assert_listing_still_decodable("DJI_0042.JPG", "image/jpeg", "drone")
    assert_listing_still_decodable("living_0.jpg", "image/jpeg", "hdr")
    assert_listing_still_decodable("living_0.HEIC", "image/heic", "hdr")
    assert_listing_still_decodable("living_0.heif", "image/heif", "hdr")


def test_infer_delimited_plan_token_os_frontend_parity():
    """Floor Plan / level-1-plan → floorplan as_is; ground_floor / airplane stay hdr."""
    assert infer_media_type("Floor Plan.png") == "floorplan"
    assert infer_media_type("level-1-plan.jpg") == "floorplan"
    assert infer_media_type("site_plan.jpg") == "floorplan"
    assert infer_media_type("plan_view.jpg") == "floorplan"
    assert infer_media_type("ground_floor_living.jpg") == "hdr"
    assert infer_media_type("first_floor_kitchen.jpg") == "hdr"
    assert infer_media_type("airplane.jpg") == "hdr"
    assert infer_media_type("plant_corner.jpg") == "hdr"
    assert delivery_route({
        "filename": "Floor Plan.png",
        "mime_type": "image/png",
        "file_url": "p/Floor Plan.png",
    }) == "as_is"
    assert delivery_route({
        "filename": "ground_floor_living.jpg",
        "mime_type": "image/jpeg",
        "file_url": "p/gf.jpg",
    }) == "vendor_photo"


def test_pick_visit_upload_skips_unreviewed():
    """Null/pending Visit must not register on the listing (same as rejected)."""
    pending = {
        "id": "pend",
        "filename": "room.ply",
        "provider": "visit",
        "qc_status": "pending",
    }
    empty = {
        "id": "empty",
        "filename": "room.splat",
        "provider": "visit",
        "qc_status": None,
    }
    ok = {
        "id": "good",
        "filename": "room.ply",
        "provider": "visit",
        "qc_status": "passed",
    }
    flagged = {
        "id": "soft",
        "filename": "room.spz",
        "provider": "visit",
        "qc_status": "flagged",
    }
    assert pick_visit_upload([pending]) is None
    assert pick_visit_upload([empty]) is None
    assert pick_visit_upload([pending, empty]) is None
    assert pick_visit_upload([pending, ok])["id"] == "good"
    # flagged Visit still registers (reviewable, not held)
    assert pick_visit_upload([flagged])["id"] == "soft"
    # Prefer .splat over .ply when both reviewed — pending splat must not win
    pending_splat = {
        "id": "bad-splat",
        "filename": "room.splat",
        "provider": "visit",
        "qc_status": "pending",
    }
    assert pick_visit_upload([pending_splat, ok])["id"] == "good"
