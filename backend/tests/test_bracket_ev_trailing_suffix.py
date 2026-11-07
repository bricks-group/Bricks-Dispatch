"""Trailing-EV bracket names (fuze-lounge_-2EV.jpg, kitchen_+0.7ev.jpg) group as one merge.

Before: `_-2EV` didn't match the EV suffix, so each frame was its own stem and
Mark complete returned 400 "only 1 exposure" on a real 5-frame bracket.
"""
from app.services.bracket_require import validate_hdr_uploads_for_complete
from app.services.photo_enhance_service import (
    bracket_stem,
    exposure_bias_from_name,
    group_assets_by_bracket,
)


def test_trailing_ev_parses():
    assert exposure_bias_from_name("fuze-lounge_-2EV.jpg") == -2.0
    assert exposure_bias_from_name("fuze-lounge_+2EV.jpg") == 2.0
    assert exposure_bias_from_name("fuze-lounge_0EV.jpg") == 0.0
    assert exposure_bias_from_name("kitchen_+0.7ev.JPG") == 0.7
    assert bracket_stem("fuze-lounge_-1EV.jpg") == "fuze-lounge"


def test_two_digit_trailing_ev_is_not_a_counter():
    assert exposure_bias_from_name("dusk_10EV.jpg") == 10.0
    assert exposure_bias_from_name("IMG_4031.JPG") is None


def test_five_frame_trailing_ev_bracket_is_one_group():
    rows = [{"id": str(i), "filename": f"fuze-lounge_{ev}EV.jpg"}
            for i, ev in enumerate(["-2", "-1", "0", "+1", "+2"])]
    groups = group_assets_by_bracket(rows)
    assert len(groups) == 1 and len(groups[0]) == 5


def test_mark_complete_accepts_trailing_ev_bracket():
    ups = [{"filename": f"fuze-lounge_{ev}EV.jpg", "media_type": "hdr"}
           for ev in ["-2", "-1", "0", "+1", "+2"]]
    assert validate_hdr_uploads_for_complete(ups) is None
