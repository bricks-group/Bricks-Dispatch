from app.services.capture_entitlements import stamp_capture
from app.services.visit_checklist import (
    TRADE_STEPS,
    checklist_title,
    ensure_visit_checklist,
    seed_visit_checklist,
    swap_untouched_checklist,
    trade_checklist_key,
)


def _joined(items):
    return " | ".join(i["label"].lower() for i in items)


def test_cleaning_job_lists_proof_of_work():
    joined = _joined(seed_visit_checklist({"job_type": "cleaning"}))
    for word in ("after photos of each room", "keys left or returned", "lights off",
                 "doors and windows locked", "garbage out", "damage or issues, with a photo"):
        assert word in joined
    assert "hdr" not in joined and "floor plan" not in joined


def test_turnover_crew_maps_to_cleaner_list():
    assert trade_checklist_key({"job_type": "turnover"}) == "cleaner"


def test_staging_install_has_before_after_and_inventory():
    joined = _joined(seed_visit_checklist({"job_type": "staging"}))
    assert "before photos of each room" in joined and "after photos of each staged room" in joined
    assert "inventory of pieces placed: item and room" in joined


def test_destaging_gets_after_photos_and_inventory_check():
    for job in ({"job_type": "staging", "package_name": "De-staging pickup"},
                {"job_type": "destaging"},
                {"job_type": "staging", "package_id": "staging_pickup"}):
        assert trade_checklist_key(job) == "destage"
        joined = _joined(seed_visit_checklist(job))
        assert "after photos" in joined and "inventory check" in joined
        assert "before photos" not in joined


def test_repair_job_has_before_after_per_fix_and_other_findings():
    joined = _joined(seed_visit_checklist({"job_type": "repair"}))
    assert "before photo of each fix" in joined and "after photo of each fix" in joined
    assert "anything else you found" in joined


def test_inspector_and_measurer_only_report_and_access_notes():
    for jt in ("inspection", "measurement"):
        items = seed_visit_checklist({"job_type": jt})
        assert len(items) == 2
        joined = _joined(items)
        assert "upload your report" in joined and "access and lock-up notes" in joined


def test_explicit_required_vendor_type_wins():
    assert trade_checklist_key({"job_type": "service", "required_vendor_type": "handyman"}) == "handyman"


def test_media_jobs_keep_their_checklists():
    assert trade_checklist_key({"job_type": "photo"}) is None
    assert trade_checklist_key({"job_type": "drone", "package_id": "cre_aerial"}) is None
    assert "hdr / stills" in _joined(seed_visit_checklist({"job_type": "photo"}))
    assert checklist_title({"job_type": "photo"}) == "Shoot checklist"
    assert checklist_title({"job_type": "cleaning"}) == "Job checklist"


def test_trade_lists_are_numbered_and_plain():
    for key in TRADE_STEPS:
        job = {"job_type": "destaging"} if key == "destage" else {"required_vendor_type": key}
        items = seed_visit_checklist(job)
        assert [i["label"].split(".")[0] for i in items] == [str(n) for n in range(1, len(items) + 1)]
        for i in items:
            text = i["label"].split(". ", 1)[1]
            assert text[0].isupper()
            assert "GO" not in text.split() and "Bricks OS" not in text


def test_untouched_generic_checklist_on_trade_job_is_swapped():
    generic = seed_visit_checklist({"job_type": "photo"})
    fixed = ensure_visit_checklist({"job_type": "cleaning", "checklist": generic})
    assert "garbage out" in _joined(fixed) and "hdr" not in _joined(fixed)


def test_ticked_generic_checklist_on_trade_job_is_kept():
    generic = seed_visit_checklist({"job_type": "photo"})
    generic[0]["completed"] = True
    kept = ensure_visit_checklist({"job_type": "cleaning", "checklist": generic})
    assert kept[0]["completed"] is True and "hdr" in _joined(kept)
    assert swap_untouched_checklist({"job_type": "cleaning", "checklist": generic}) is None


def test_trade_checklist_with_ticks_survives_ensure():
    items = seed_visit_checklist({"job_type": "repair"})
    items[0]["completed"] = True
    kept = ensure_visit_checklist({"job_type": "repair", "checklist": items})
    assert kept == items


def test_read_path_swaps_untouched_generic_with_stable_ids():
    generic = seed_visit_checklist({"job_type": "photo"})
    first = stamp_capture({"id": "job-1", "job_type": "inspection", "checklist": list(generic)})
    second = stamp_capture({"id": "job-1", "job_type": "inspection", "checklist": list(generic)})
    assert "upload your report" in _joined(first["checklist"])
    assert [i["id"] for i in first["checklist"]] == [i["id"] for i in second["checklist"]]
    assert first["checklist_title"] == "Job checklist"


def test_read_path_keeps_ticked_and_media_checklists():
    generic = seed_visit_checklist({"job_type": "photo"})
    generic[1]["completed"] = True
    row = stamp_capture({"id": "job-2", "job_type": "staging", "checklist": generic})
    assert "hdr" in _joined(row["checklist"])
    media = seed_visit_checklist({"job_type": "photo"})
    row = stamp_capture({"id": "job-3", "job_type": "photo", "checklist": media})
    assert row["checklist"] == media and row["checklist_title"] == "Shoot checklist"


def test_read_path_also_applies_5c6ad2c_rule_to_client_services():
    generic = seed_visit_checklist({"job_type": "drone"})
    row = stamp_capture({"id": "job-4", "job_type": "drone", "package_id": "cre_aerial", "checklist": generic})
    assert "hdr" not in _joined(row["checklist"])
