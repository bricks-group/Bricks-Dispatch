from app.services.media_intake import (
    delivery_route,
    infer_media_type,
    is_photo_file,
    is_video_file,
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
