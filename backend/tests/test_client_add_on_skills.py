"""Client-service add-ons gate auto-assign on the skills they need."""
from app.services.vendor_profile import job_required_skills


def test_acquisition_needs_drone_photo_and_tour():
    job = {"job_type": "drone", "add_ons": ["interior", "virtual_visit"], "package_id": "inv_acquisition"}
    assert job_required_skills(job) == {"drone", "photo", "tour"}


def test_mapped_acquisition_keeps_mapping_and_adds_interior_and_visit():
    job = {"job_type": "site_mapped_capture", "add_ons": ["interior", "virtual_visit"], "package_id": "inv_acquisition_mapped"}
    assert job_required_skills(job) == {"drone", "mapping", "photo", "tour"}


def test_plain_builder_mapped_job_unchanged():
    job = {"job_type": "site_mapped_capture", "add_ons": []}
    assert job_required_skills(job) == {"drone", "mapping"}


def test_aerial_only_and_listing_sets_unchanged():
    assert job_required_skills({"job_type": "drone", "add_ons": []}) == {"drone"}
    assert job_required_skills({"job_type": "photo", "add_ons": ["floor_plan"]}) == {"photo"}
    assert job_required_skills({"job_type": "photo", "add_ons": ["floor_plan", "virtual_visit"]}) == {"photo", "tour"}


def test_dict_add_ons_work_too():
    job = {"job_type": "drone", "add_ons": [{"key": "interior"}, {"id": "virtual_visit"}]}
    assert job_required_skills(job) == {"drone", "photo", "tour"}
