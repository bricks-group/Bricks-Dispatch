from app.services.job_checklist import proof_kind
from app.services.visit_checklist import (
    ensure_visit_checklist,
    seed_visit_checklist,
    swap_untouched_checklist,
)


def _joined(items):
    return " | ".join(i["label"].lower() for i in items)


def test_visual_site_visit_is_aerial_not_listing_stills():
    for jt in ("site_progress_visual", "progress_capture"):
        items = seed_visit_checklist({"job_type": jt})
        joined = _joined(items)
        assert "hdr" not in joined and "full property" not in joined
        assert "aerial stills" in joined and "flyover video" in joined
        assert "mapping flight" not in joined
        assert items[0]["label"].lower().startswith("1. arrive") and "submit" in items[-1]["label"].lower()


def test_mapped_site_visit_has_mapping_flight_and_setup_only_when_bought():
    plain = _joined(seed_visit_checklist({"job_type": "site_mapped_capture"}))
    assert "mapping flight" in plain and "orthomosaic" in plain and "ground control" not in plain
    first = _joined(seed_visit_checklist({"job_type": "site_mapped_capture", "add_ons": ["mapped_setup"]}))
    assert "ground control" in first


def test_monitoring_visit_follows_its_plan():
    assert "mapping flight" in _joined(seed_visit_checklist({"job_type": "site_monitoring", "add_ons": ["mapped"]}))
    visual = _joined(seed_visit_checklist({"job_type": "site_monitoring"}))
    assert "flyover video" in visual and "mapping flight" not in visual


def test_numbering_is_sequential():
    items = seed_visit_checklist({"job_type": "site_mapped_capture", "add_ons": ["mapped_setup"]})
    assert [i["label"].split(".")[0] for i in items] == [str(n) for n in range(1, len(items) + 1)]


def test_client_service_key_still_wins_on_mapped_acquisition():
    joined = _joined(seed_visit_checklist({"job_type": "site_mapped_capture", "package_id": "inv_acquisition_mapped"}))
    assert "virtual visit" in joined and "interior record" in joined


GENERIC = [
    {"id": "a", "label": "1. Arrive & confirm property access", "completed": False},
    {"id": "b", "label": "2. HDR / stills (full property)", "completed": False},
    {"id": "c", "label": "3. Drone / aerial (same visit)", "completed": False},
    {"id": "d", "label": "4. Submit / deliver package", "completed": False},
]


def test_untouched_generic_list_on_site_job_is_swapped():
    job = {"id": "j1", "job_type": "site_progress_visual", "checklist": GENERIC}
    assert "hdr" not in _joined(ensure_visit_checklist(job))
    swapped = swap_untouched_checklist(job)
    assert swapped is not None and "flyover video" in _joined(swapped)


def test_ticked_generic_list_on_site_job_is_kept():
    ticked = [dict(GENERIC[0], completed=True), *GENERIC[1:]]
    job = {"id": "j1", "job_type": "site_progress_visual", "checklist": ticked}
    assert "hdr" in _joined(ensure_visit_checklist(job))
    assert swap_untouched_checklist(job) is None


def test_listing_jobs_unchanged():
    joined = _joined(seed_visit_checklist({"job_type": "photo", "add_ons": ["drone"]}))
    assert "hdr / stills (full property)" in joined and "drone / aerial" in joined


def test_site_steps_get_a_proof_kind_without_error():
    for item in seed_visit_checklist({"job_type": "site_mapped_capture", "add_ons": ["mapped_setup"]}):
        proof_kind(item["label"])
