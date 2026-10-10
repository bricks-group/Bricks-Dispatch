"""Floor plan PDF cut off mid-upload is held, not delivered as a plan that won't open."""
from app.services import qc_media_kinds as qmk

FULL = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + b"0" * 4000 + b"\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_complete_pdf_plan_passes():
    v = qmk.check_floorplan_bytes(FULL, "floorplan.pdf")
    assert v["qc_status"] == "passed", v


def test_eof_marker_tolerates_trailing_whitespace_and_junk():
    v = qmk.check_floorplan_bytes(FULL + b"\r\n\x00\x00  ", "floorplan.pdf")
    assert v["qc_status"] == "passed", v


def test_incremental_update_pdf_with_two_eof_markers_passes():
    v = qmk.check_floorplan_bytes(FULL + b"1 0 obj\n<<>>\nendobj\n%%EOF\n", "plan.pdf")
    assert v["qc_status"] == "passed", v


def test_truncated_pdf_plan_is_held():
    cut = FULL[: len(FULL) // 2]
    v = qmk.check_floorplan_bytes(cut, "floorplan.pdf")
    assert v["qc_status"] == "rejected", v
    assert "truncated" in v["qc_notes"]


def test_eof_only_far_before_end_is_still_truncated():
    # Linearized/updated PDF cut off in a later section: first %%EOF is >1 KB back.
    data = FULL + b"2 0 obj\n" + b"9" * 5000
    v = qmk.check_floorplan_bytes(data, "plan.pdf")
    assert v["qc_status"] == "rejected", v


def test_header_only_pdf_is_held():
    v = qmk.check_floorplan_bytes(b"%PDF-1.6\n...", "floorplan.pdf", "application/pdf")
    assert v["qc_status"] == "rejected" and "truncated" in v["qc_notes"], v
