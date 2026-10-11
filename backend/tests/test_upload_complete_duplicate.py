"""GO /api/uploads/complete duplicate guard (retry / re-picked file)."""
from app.routers.uploads import find_duplicate_upload


ROWS = [
    {"id": "u1", "filename": "IMG_4031_-2.jpg", "file_size": 5_120_000, "qc_status": "passed", "processing_status": "done"},
    {"id": "u2", "filename": "IMG_4031_0.jpg", "file_size": 5_300_000, "qc_status": None, "processing_status": "pending"},
    {"id": "u3", "filename": "kitchen.jpg", "file_size": 4_000_000, "qc_status": "rejected", "processing_status": "done"},
    {"id": "u4", "filename": "bath.jpg", "file_size": 3_000_000, "qc_status": None, "processing_status": "failed"},
]


def test_same_upload_id_retry_returns_existing():
    assert find_duplicate_upload(ROWS, upload_id="u2", filename="other.jpg", file_size=1)["id"] == "u2"


def test_same_name_and_size_is_duplicate():
    dup = find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4031_-2.jpg", file_size=5_120_000)
    assert dup["id"] == "u1"


def test_name_match_is_case_insensitive():
    assert find_duplicate_upload(ROWS, upload_id="new", filename="img_4031_0.JPG", file_size=5_300_000)["id"] == "u2"


def test_same_name_different_size_is_not_duplicate():
    assert find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4031_-2.jpg", file_size=5_120_001) == {}


def test_unknown_size_never_dedupes_by_name():
    assert find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4031_-2.jpg", file_size=0) == {}


def test_rejected_or_failed_can_be_reuploaded():
    assert find_duplicate_upload(ROWS, upload_id="new", filename="kitchen.jpg", file_size=4_000_000) == {}
    assert find_duplicate_upload(ROWS, upload_id="new", filename="bath.jpg", file_size=3_000_000) == {}


def test_empty_rows():
    assert find_duplicate_upload([], upload_id="x", filename="a.jpg", file_size=10) == {}
    assert find_duplicate_upload(None, upload_id="x", filename="a.jpg", file_size=10) == {}


def test_browser_copy_suffix_same_size_is_duplicate():
    # Re-picked from Downloads/Finder: "(1)", "copy", "- Copy" of the same bytes.
    for name in ("IMG_4031_-2 (1).jpg", "IMG_4031_-2(2).JPG", "IMG_4031_-2 copy.jpg",
                 "IMG_4031_-2 copy 2.jpg", "IMG_4031_-2 - Copy.jpg", "IMG_4031_-2_copy.jpg"):
        dup = find_duplicate_upload(ROWS, upload_id="new", filename=name, file_size=5_120_000)
        assert dup.get("id") == "u1", name


def test_copy_suffix_on_existing_row_matches_original_name():
    rows = [{"id": "c1", "filename": "living (1).jpg", "file_size": 777, "qc_status": None, "processing_status": "pending"}]
    assert find_duplicate_upload(rows, upload_id="new", filename="living.jpg", file_size=777)["id"] == "c1"


def test_copy_suffix_different_size_is_not_duplicate():
    assert find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4031_-2 (1).jpg", file_size=5_120_001) == {}


def test_bracket_ev_and_counter_names_not_treated_as_copies():
    # Different exposures / frames stay distinct even at an identical byte size.
    assert find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4031_-1.jpg", file_size=5_120_000) == {}
    assert find_duplicate_upload(ROWS, upload_id="new", filename="IMG_4032_-2.jpg", file_size=5_120_000) == {}
    rows = [{"id": "s1", "filename": "canopy.jpg", "file_size": 9, "qc_status": None, "processing_status": "pending"}]
    assert find_duplicate_upload(rows, upload_id="new", filename="can.jpg", file_size=9) == {}
