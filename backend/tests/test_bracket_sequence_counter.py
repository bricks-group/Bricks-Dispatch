"""Camera frame counters (IMG_4031, DSC_0012) are file numbers, not +EV."""
from app.services.bracket_require import validate_hdr_uploads_for_complete
from app.services.photo_enhance_service import (
    bracket_stem,
    exposure_bias_from_name,
    group_assets_by_bracket,
)


def test_camera_counter_not_ev():
    assert exposure_bias_from_name("IMG_4031.JPG") is None
    assert exposure_bias_from_name("DSC_0012.jpg") is None
    assert exposure_bias_from_name("untitled shoot-4031.CR3") is None
    assert bracket_stem("IMG_4031.JPG") == "IMG_4031"
    assert bracket_stem("untitled shoot-4031.CR3") == "untitled shoot-4031"


def test_camera_named_shoot_does_not_collapse_into_one_merge():
    rows = [{"id": str(i), "filename": f"IMG_{4031 + i}.JPG"} for i in range(5)]
    groups = group_assets_by_bracket(rows)
    assert len(groups) == 5
    assert all(len(g) == 1 for g in groups)


def test_ev_suffixes_still_parse():
    assert exposure_bias_from_name("IMG_4031_-2.jpg") == -2.0
    assert bracket_stem("IMG_4031_+2.jpg") == "IMG_4031"
    assert exposure_bias_from_name("kitchen_0.jpg") == 0.0
    assert exposure_bias_from_name("kitchen_2.jpg") == 2.0
    assert exposure_bias_from_name("k_ev10.jpg") == 10.0
    assert exposure_bias_from_name("k_+1.5.jpg") == 1.5
    rows = [{"id": str(i), "filename": f"IMG_4031_{ev}.jpg"} for i, ev in enumerate(["-2", "0", "+2"])]
    assert len(group_assets_by_bracket(rows)) == 1


def test_complete_gate_names_real_angle_not_IMG():
    rows = [{"filename": f"IMG_{4031 + i}.JPG"} for i in range(5)]
    issue = validate_hdr_uploads_for_complete(rows)
    assert issue is not None
    assert issue["stem"] == "IMG_4031"
    assert issue["count"] == 1
