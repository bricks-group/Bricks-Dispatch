"""Package/service gate for GO in-app 3D tour + floor plan capture."""
from app.services.capture_entitlements import (
    job_capture_entitlements,
    kind_purchased,
)


def flags(**job):
    return job_capture_entitlements(job)


def test_essentials_photo_drone_is_floorplan_only():
    f = flags(
        package_id="essentials",
        package_name="Essentials — HDR photo + Drone",
        job_type="drone",
        add_ons=[{"key": "drone", "included": True}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is False
    assert kind_purchased({"package_id": "essentials", "job_type": "drone",
                           "package_name": "Essentials — HDR photo + Drone",
                           "add_ons": [{"key": "drone", "included": True}]}, "floorplan")
    assert not kind_purchased({"package_id": "essentials", "job_type": "drone",
                               "package_name": "Essentials — HDR photo + Drone",
                               "add_ons": [{"key": "drone", "included": True}]}, "splat")


def test_photos_only_has_neither():
    f = flags(
        package_id="photos",
        package_name="Photos only — HDR photo",
        job_type="photo",
        add_ons=[],
    )
    assert f["floorplan"] is False
    assert f["tour"] is False


def test_small_xl_photo_package_includes_floorplan():
    f = flags(package_name="Small photo", job_type="photo", add_ons=[])
    assert f["floorplan"] is True
    assert f["tour"] is False
    f = flags(package_name="XL photo", job_type="photo")
    assert f["floorplan"] is True


def test_job_type_photo_includes_floorplan():
    f = flags(package_name="Listing media", job_type="photo")
    assert f["floorplan"] is True
    assert f["tour"] is False


def test_floor_plan_only_99():
    f = flags(
        package_id="floor_plan_only",
        package_name="Floor plan only $99",
        job_type="floorplan",
        add_ons=[{"id": "floor_plan_only", "name": "Floor Plan Only"}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is False


def test_explicit_floorplan_addon_on_drone_job():
    f = flags(
        package_name="Drone Photos",
        job_type="drone",
        add_ons=[{"key": "drone", "included": True}, {"key": "floorplan_2d", "included": True}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is False


def test_drone_only_without_floorplan_line():
    f = flags(
        package_name="Drone Photos",
        job_type="drone",
        add_ons=[{"key": "drone", "included": True}],
    )
    assert f["floorplan"] is False
    assert f["tour"] is False


def test_reel_only_without_floorplan_line():
    f = flags(
        package_name="Cinematic reel",
        job_type="video",
        add_ons=[{"key": "reel", "included": True}],
    )
    assert f["floorplan"] is False
    assert f["tour"] is False


def test_showcase_photo_plus_tour_shows_both():
    f = flags(
        package_id="showcase",
        package_name="Showcase — HDR photo + Drone + Cinematic reel + Virtual tour",
        job_type="drone",
        add_ons=[
            {"key": "drone", "included": True},
            {"key": "reel", "included": True},
            {"key": "tour", "included": True},
        ],
    )
    assert f["floorplan"] is True
    assert f["tour"] is True
    assert f["visit"] is False


def test_suite_includes_tour_and_floorplan():
    f = flags(
        package_id="suite",
        package_name="Full suite — HDR photo + Drone + Cinematic reel + Virtual tour + Twilight",
        job_type="drone",
        add_ons=[{"key": "tour", "included": True}, {"key": "twilight", "included": True}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is True


def test_tour_addon_on_essentials_is_picker():
    f = flags(
        package_id="essentials",
        package_name="Essentials — HDR photo + Drone + Virtual tour",
        job_type="drone",
        add_ons=[{"key": "drone", "included": True}, {"key": "tour", "included": True}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is True


def test_tour_only_3d_tour_job_type():
    f = flags(package_name="Virtual tour", job_type="3d_tour", add_ons=[])
    assert f["tour"] is True
    assert f["floorplan"] is False


def test_tour_only_addon_without_photo_package():
    f = flags(
        package_name="Virtual tour",
        job_type="virtual_tour",
        add_ons=[{"key": "tour", "included": True}],
    )
    assert f["tour"] is True
    assert f["floorplan"] is False


def test_visit_only_is_not_phone_tour():
    f = flags(
        package_name="Virtual visit (quoted by sqft)",
        job_type="photo",
        package_id="photos",
        add_ons=[],
        client_instructions="Virtual visit (Gaussian splat, free-roam) requested — quoted by sqft.",
    )
    # photos only + visit: neither phone mode
    assert f["tour"] is False
    assert f["floorplan"] is False
    assert f["visit"] is True


def test_visit_with_essentials_opens_floorplan_not_tour():
    f = flags(
        package_id="essentials",
        package_name="Essentials — HDR photo + Drone + Virtual visit (quoted by sqft)",
        job_type="drone",
        add_ons=[{"key": "drone", "included": True}],
        client_instructions="Virtual visit (Gaussian splat, SuperSplat). Never packaged.",
    )
    assert f["floorplan"] is True
    assert f["tour"] is False
    assert f["visit"] is True


def test_live_premium_luxury_estate_include_path_tour():
    """Live booking-api ladder: Premium+ include path-confined 3D tour + floor plan."""
    for pid, name in (
        ("premium", "Premium"),
        ("luxury", "Luxury"),
        ("estate", "Estate"),
    ):
        f = flags(package_id=pid, package_name=name, job_type="photo", add_ons=[])
        assert f["floorplan"] is True, pid
        assert f["tour"] is True, pid
        assert f["visit"] is False, pid
    f = flags(package_id="standard", package_name="Standard", job_type="photo", add_ons=[])
    assert f["floorplan"] is True
    assert f["tour"] is False


def test_legacy_premium_gaussian_copy_is_visit_not_phone_tour():
    """Old Premium rows that still say Gaussian Visit must not open tour shutter."""
    f = flags(
        package_id="premium",
        package_name="Premium — Gaussian splat 3D virtual tour",
        job_type="video",
    )
    assert f["tour"] is False
    assert f["visit"] is True
    assert f["floorplan"] is True


def test_disabled_tour_addon_does_not_grant():
    f = flags(
        package_id="essentials",
        package_name="Essentials — HDR photo + Drone",
        job_type="drone",
        add_ons=[{"key": "tour", "included": False}, {"key": "drone", "included": True}],
    )
    assert f["tour"] is False
    assert f["floorplan"] is True


def test_addon_strings_and_sku_objects():
    f = flags(
        job_type="drone",
        package_name="Drone",
        add_ons=["floor_plan_only", {"sku": "tour", "name": "Virtual tour"}],
    )
    assert f["floorplan"] is True
    assert f["tour"] is True


def test_checklist_virtual_tour_counts_visit_does_not():
    f = flags(
        job_type="photo",
        package_name="Photos only — HDR photo",
        package_id="photos",
        checklist=[{"label": "Virtual visit"}, {"label": "Shoot 3D tour"}],
    )
    # photos-only drops floorplan; 3D tour checklist still grants tour
    assert f["floorplan"] is False
    assert f["tour"] is True
    assert f["visit"] is True


def test_floorplan_capture_submitted_note_is_not_a_purchase_line():
    f = flags(
        job_type="drone",
        package_name="Drone Photos",
        add_ons=[{"key": "drone", "included": True}],
        checklist=[{"label": "Floor plan capture submitted", "completed": True}],
    )
    assert f["floorplan"] is False


def test_visit_kind_purchased():
    job = {
        "package_id": "essentials",
        "package_name": "Essentials",
        "job_type": "drone",
        "add_ons": [{"key": "drone", "included": True}, {"key": "visit", "included": True}],
    }
    assert flags(**job)["visit"] is True
    assert kind_purchased(job, "visit")
    assert not kind_purchased(job, "splat")
    assert kind_purchased(job, "floorplan")


def test_visit_only_job_grants_visit_kind():
    job = {
        "package_name": "Virtual visit (quoted by sqft)",
        "job_type": "visit",
        "add_ons": [{"key": "virtual_visit", "included": True}],
    }
    assert flags(**job)["visit"] is True
    assert kind_purchased(job, "visit")
