from app.services.visit_checklist import (
    CLIENT_SERVICE_STEPS,
    ensure_visit_checklist,
    seed_visit_checklist,
)


def _labels(items):
    return [i["label"].lower() for i in items]


def test_aerial_only_job_has_no_stills_step():
    labels = _labels(seed_visit_checklist({"job_type": "drone", "package_id": "cre_aerial"}))
    joined = " | ".join(labels)
    assert "hdr" not in joined and "full property" not in joined
    assert "drone stills" in joined and "aerial video" in joined
    assert labels[0].startswith("1. arrive") and "submit" in labels[-1]


def test_condition_report_is_room_by_room_without_floor_plan():
    joined = " | ".join(_labels(seed_visit_checklist({"job_type": "photo", "package_id": "pm_condition_report"})))
    assert "room-by-room" in joined and "damage" in joined
    assert "floor plan" not in joined and "hdr" not in joined


def test_turnover_is_condition_report_plus_listing_set():
    joined = " | ".join(_labels(seed_visit_checklist({"job_type": "photo", "package_id": "pm_turnover"})))
    assert "room-by-room" in joined and "listing photos" in joined and "floor plan" in joined


def test_numbering_is_sequential():
    for key in CLIENT_SERVICE_STEPS:
        items = seed_visit_checklist({"package_id": key})
        assert [i["label"].split(".")[0] for i in items] == [str(n) for n in range(1, len(items) + 1)]


def test_mapped_acquisition_includes_mapping_flight():
    joined = " | ".join(_labels(seed_visit_checklist({"job_type": "site_mapped_capture", "package_id": "inv_acquisition_mapped"})))
    assert "mapping flight" in joined and "virtual visit" in joined


def test_listing_packages_keep_generic_checklist():
    joined = " | ".join(_labels(seed_visit_checklist({"job_type": "photo", "package_id": "premium", "add_ons": [{"key": "drone"}]})))
    assert "hdr / stills" in joined and "drone" in joined


def test_untouched_generic_checklist_on_client_job_is_reseeded():
    generic = seed_visit_checklist({"job_type": "drone"})
    fixed = ensure_visit_checklist({"job_type": "drone", "package_id": "cre_aerial", "checklist": generic})
    assert "hdr" not in " | ".join(_labels(fixed))


def test_ticked_generic_checklist_is_kept():
    generic = seed_visit_checklist({"job_type": "drone"})
    generic[0]["completed"] = True
    kept = ensure_visit_checklist({"job_type": "drone", "package_id": "cre_aerial", "checklist": generic})
    assert kept[0]["completed"] is True and "hdr" in " | ".join(_labels(kept))
