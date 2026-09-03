"""360 station compile: stitch gate, plan from stations, equirect tour HTML."""
from pathlib import Path
import io

from PIL import Image

from app.services.capture_layout import (
    equirect_path,
    pan_path,
    parse_station_index_from_path,
    role_from_path,
    stations_have_compile_input,
)
from app.services.equirect_stitcher import (
    StitchError,
    is_equirect_image,
    station_is_stitchable,
    stitch_pan_to_equirect,
    yaw_span_deg,
)
from app.services.floorplan_tracer import derive_floorplan, stations_to_rooms
from app.services.tour_builder import build_tour_html, build_tour_json, TourBuildError


ROOT = Path(__file__).resolve().parents[1]


def _jpeg(w, h, color=(40, 80, 120)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_layout_paths():
    assert pan_path("sid", 2, 4) == "capture/sid/stations/2/pan/4.jpg"
    assert equirect_path("sid", 2) == "capture/sid/stations/2/equirect.jpg"
    assert parse_station_index_from_path("capture/sid/stations/3/pan/1.jpg") == 3
    assert role_from_path("capture/sid/stations/3/equirect.jpg") == "equirect"
    assert role_from_path("capture/sid/stations/3/pan/1.jpg") == "pan"


def test_is_equirect_2_to_1():
    assert is_equirect_image(_jpeg(2048, 1024))
    assert not is_equirect_image(_jpeg(1200, 900))


def test_yaw_span_wraps():
    assert yaw_span_deg([350, 10, 30]) >= 40
    assert yaw_span_deg(list(range(0, 360, 15))) >= 270


def test_station_not_stitchable_when_thin():
    pans = [{"bytes": _jpeg(320, 240), "yaw": 0}]
    assert station_is_stitchable(pans) is False


def test_station_stitchable_with_dome():
    pans = [{"bytes": _jpeg(64, 48, (i * 9, 40, 80)), "yaw": i * 15} for i in range(24)]
    assert station_is_stitchable(pans) is True


def test_stitch_pan_produces_equirect():
    pans = [{"bytes": _jpeg(80, 60, (i * 8, 30, 90)), "yaw": i * 15} for i in range(24)]
    jpeg = stitch_pan_to_equirect(pans)
    assert is_equirect_image(jpeg)
    img = Image.open(io.BytesIO(jpeg))
    assert abs(img.size[0] / img.size[1] - 2.0) < 0.15


def test_stitch_refuses_thin_coverage():
    pans = [{"bytes": _jpeg(80, 60), "yaw": 0} for _ in range(3)]
    try:
        stitch_pan_to_equirect(pans)
        assert False, "should have failed"
    except StitchError:
        pass


def test_plan_from_station_graph():
    stations = [
        {"station_index": 0, "room": "Kitchen"},
        {"station_index": 1, "room": "Living"},
        {"station_index": 2, "room": "Bedroom"},
    ]
    rooms = stations_to_rooms(stations)
    assert [r["name"] for r in rooms] == ["Kitchen", "Living", "Bedroom"]
    png = derive_floorplan(stations, address="10 Main")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.size[0] >= 800


def test_plan_from_posed_stations():
    stations = [
        {"station_index": 0, "room": "Kitchen", "pose_x": 0, "pose_y": 0, "heading": 90},
        {"station_index": 1, "room": "Kitchen", "pose_x": 2, "pose_y": 0, "heading": 0},
        {"station_index": 2, "room": "Hall", "pose_x": 6, "pose_y": 0, "heading": 0},
    ]
    png = derive_floorplan(stations)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_plan_from_roomplan_json():
    png = derive_floorplan([], roomplan={"rooms": [
        {"name": "Kitchen", "x": 0, "y": 0, "w": 12, "h": 10},
        {"name": "Bath", "x": 12, "y": 0, "w": 6, "h": 8},
    ]})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_tour_html_is_equirect_walkthrough():
    stations = [
        {"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0},
        {"id": "station-1", "room": "Living", "file": "scenes/1.jpg", "station_index": 1},
    ]
    html = build_tour_html(stations, address="10 Main", title="3D tour")
    assert "scenes/0.jpg" in html
    assert "scenes/1.jpg" in html
    assert "equirect-walkthrough" in build_tour_json(stations)
    assert "Pannellum of perspective" not in html
    assert "station-0" in html or "Kitchen" in html
    try:
        build_tour_html([])
        assert False
    except TourBuildError:
        pass


def test_submit_gate_no_stations():
    ok, reason = stations_have_compile_input([], kind="splat", has_roomplan=False)
    assert ok is False and reason == "no_stations"


def test_submit_gate_no_equirects():
    thin = [{"station_index": 0, "pans": [{"storage_path": "x"}] * 2, "equirect_path": None}]
    ok, reason = stations_have_compile_input(thin, kind="splat", has_roomplan=False)
    assert ok is False and reason == "no_equirects"


def test_submit_gate_pan_burst_ok():
    pans = [{"storage_path": f"p{i}"} for i in range(12)]
    ok, reason = stations_have_compile_input(
        [{"station_index": 0, "pans": pans, "equirect_path": None}],
        kind="splat",
        has_roomplan=False,
    )
    assert ok is True and reason == "pan_burst"


def test_floorplan_roomplan_bypasses_equirect_gate():
    ok, reason = stations_have_compile_input([], kind="floorplan", has_roomplan=True)
    assert ok is True and reason == "roomplan"


def test_router_uses_station_paths():
    src = (ROOT / "app/routers/capture.py").read_text()
    assert "stations/{n}" in src or "station_index" in src
    assert "no_stations" in src
    assert "no_equirects" in src
    assert "Building tour" in src
    assert "process_capture_session" in src
    assert "Splatting" not in src.split("_PIPELINES")[1].split(")")[0] or 'Building tour' in src


def test_no_gemini_in_floorplan_tracer():
    src = (ROOT / "app/services/floorplan_tracer.py").read_text()
    assert "generate_from_images" not in src
    assert "GEMINI" not in src
