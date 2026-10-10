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
