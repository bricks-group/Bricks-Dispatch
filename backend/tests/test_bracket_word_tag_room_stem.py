"""Word EV tags (_under/_normal/_over/_ov) read the suffix, not the room stem.

"laundry_over" / "Sundeck_over" / "Grounds_ov" contain "und" and used to read
as -2 EV: a correctly named under/normal/over set failed Mark complete as
"same-side frames", and enhance (no EXIF bias) used the +2 frame as the mid
body with no real over plate.
"""
from PIL import Image

from app.services.bracket_require import validate_hdr_uploads_for_complete
from app.services.photo_enhance_service import (
    _pick_under_mid_over,
    bracket_stem,
    exposure_bias_from_name,
)


def test_room_stem_with_und_does_not_flip_over():
    assert exposure_bias_from_name("laundry_over.jpg") == 2.0
    assert exposure_bias_from_name("Sundeck_over.JPG") == 2.0
    assert exposure_bias_from_name("Grounds_ov.jpg") == 2.0
    assert exposure_bias_from_name("Underground parking_over.jpg") == 2.0
    assert exposure_bias_from_name("laundry_under.jpg") == -2.0
    assert exposure_bias_from_name("laundry_und.jpg") == -2.0
    assert exposure_bias_from_name("laundry_normal.jpg") == 0.0
    assert exposure_bias_from_name("Overlook_under.jpg") == -2.0
    assert exposure_bias_from_name("Overlook_normal.jpg") == 0.0


def test_existing_tags_unchanged():
    assert exposure_bias_from_name("kitchen_under.jpg") == -2.0
    assert exposure_bias_from_name("kitchen_over.jpg") == 2.0
    assert exposure_bias_from_name("kitchen_normal.jpg") == 0.0
    assert exposure_bias_from_name("kitchen-minus2.jpg") == -2.0
    assert exposure_bias_from_name("kitchen_m1.jpg") == -1.0
    assert exposure_bias_from_name("kitchen_+1.jpg") == 1.0
    assert exposure_bias_from_name("IMG_4031.jpg") is None
    assert exposure_bias_from_name("plain.jpg") is None
    assert bracket_stem("laundry_over.jpg") == "laundry"


def test_laundry_bracket_passes_mark_complete():
    rows = [{"filename": f"laundry_{t}.jpg"} for t in ("under", "normal", "over")]
    assert validate_hdr_uploads_for_complete(rows) is None
    rows = [{"filename": f"Sundeck_{t}.jpg"} for t in ("under", "normal", "ov")]
    assert validate_hdr_uploads_for_complete(rows) is None


def test_enhance_plate_pick_uses_real_over_without_exif():
    names = ["laundry_under.jpg", "laundry_normal.jpg", "laundry_over.jpg"]
    imgs = [Image.new("RGB", (8, 8), (v, v, v)) for v in (40, 128, 220)]
    biases = [exposure_bias_from_name(n) for n in names]
    mid, under, over = _pick_under_mid_over(imgs, biases)
    assert mid is imgs[1]
    assert under is imgs[0]
    assert over is imgs[2]
