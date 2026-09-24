from app.services.media_intake import (
    delivery_route,
    infer_media_type,
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
    assert delivery_route({"file_url": "p/a.mp4", "filename": "drone.mp4", "media_type": "drone"}) == "as_is"
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

    only_spz = pick_visit_upload([{"id": "1", "filename": "room.spz"}])
    assert only_spz["id"] == "1"

    only_ply = pick_visit_upload([{"id": "2", "filename": "room.ply"}, {"id": "3", "filename": "a.jpg"}])
    assert only_ply["id"] == "2"

    mixed = pick_visit_upload([
        {"id": "spz", "filename": "a.spz"},
        {"id": "ply", "filename": "b.ply"},
        {"id": "splat", "filename": "c.splat"},
        {"id": "jpg", "filename": "d.jpg"},
    ])
    assert mixed["id"] == "splat"

    ply_over_spz = pick_visit_upload([
        {"id": "spz", "filename": "a.spz"},
        {"id": "ply", "filename": "b.ply"},
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
    row = {"id": "v1", "filename": "mesh.bin", "provider": "visit", "file_url": "visits/j/mesh.bin"}
    assert pick_visit_upload([{"filename": "a.jpg"}, row])["id"] == "v1"
    assert pick_visit_upload([{"filename": "a.jpg", "provider": "bricks_inhouse"}]) is None
