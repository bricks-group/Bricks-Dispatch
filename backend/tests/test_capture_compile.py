"""360 station compile: stitch gate, plan from stations, equirect tour HTML."""
from pathlib import Path
import io

from PIL import Image

from app.services.capture_layout import (
    equirect_path,
    pan_path,
    parse_station_index_from_path,
    role_from_path,
    select_rebuild_stations,
    stations_have_compile_input,
    stations_have_stored_equirects,
)
from app.services.equirect_stitcher import (
    StitchError,
    is_equirect_image,
    station_is_stitchable,
    stitch_pan_to_equirect,
    yaw_span_deg,
)
from app.services.floorplan_tracer import derive_floorplan, infer_station_poses, stations_to_rooms
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
        {"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0, "heading": 90},
        {"id": "station-1", "room": "Living", "file": "scenes/1.jpg", "station_index": 1},
    ]
    html = build_tour_html(stations, address="10 Main", title="3D tour")
    low = html.lower()
    compact = "".join(low.split())
    assert "scenes/0.jpg" in html
    assert "scenes/1.jpg" in html
    assert "<canvas" in low
    assert "webgl" in low
    assert "getcontext" in low
    assert "object-fit:cover" not in compact
    assert "object-fit: cover" not in low
    assert "drag" in low
    assert "previous" in low
    assert "next station" in low
    assert 'id="hs"' in html or "id='hs'" in html
    assert "aria-label" in low and "next station" in low
    assert "placeHs" in html or "hotspot" in low or 'class="hs' in low
    assert "ArrowRight" in html
    assert "kitchen" in low
    assert "living" in low
    assert "bricks" in low and "3d tour" in low
    assert "border-radius:2px" in compact
    meta = build_tour_json(stations)
    assert "equirect-walkthrough" in meta
    assert '"panorama": "scenes/0.jpg"' in meta
    assert '"next": "station-1"' in meta
    assert '"prev": "station-0"' in meta
    assert '"heading": 90' in meta
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

def test_public_tour_embed_defaults_os_api_url(monkeypatch):
    from app.services.capture_worker import public_tour_embed_url

    monkeypatch.delenv("BRICKS_OS_API_URL", raising=False)
    url = public_tour_embed_url("job-1", "sess-1")
    assert url == "https://bricks-brokerage-api.vercel.app/api/public/tours/job-1/sess-1"

    monkeypatch.setenv("BRICKS_OS_API_URL", "https://custom.example")
    assert public_tour_embed_url("job-1", "sess-1") == (
        "https://custom.example/api/public/tours/job-1/sess-1"
    )

    monkeypatch.setenv("BRICKS_OS_API_URL", "  ")
    assert public_tour_embed_url("job-1", "sess-1").startswith(
        "https://bricks-brokerage-api.vercel.app/"
    )
    assert public_tour_embed_url("", "sess-1") is None

def test_floorplan_gate_accepts_four_corner_station():
    pans = [{"storage_path": f"p{i}"} for i in range(4)]
    ok, reason = stations_have_compile_input(
        [{"station_index": 0, "pans": pans, "equirect_path": None}],
        kind="floorplan",
        has_roomplan=False,
    )
    assert ok is True and reason == "stations"


def test_tour_gate_still_refuses_four_frames():
    pans = [{"storage_path": f"p{i}"} for i in range(4)]
    ok, reason = stations_have_compile_input(
        [{"station_index": 0, "pans": pans, "equirect_path": None}],
        kind="splat",
        has_roomplan=False,
    )
    assert ok is False and reason == "no_equirects"


def test_infer_station_poses_walks_heading():
    posed = infer_station_poses([
        {"station_index": 0, "room": "Kitchen", "heading": 90},
        {"station_index": 1, "room": "Hall", "heading": 90},
        {"station_index": 2, "room": "Bed"},
    ])
    assert posed[0]["pose_x"] == 0.0
    assert posed[1]["pose_x"] > 2.0
    assert posed[2]["pose_x"] > posed[1]["pose_x"]
    rooms = stations_to_rooms(posed)
    assert [r["name"] for r in rooms] == ["Kitchen", "Hall", "Bed"]



def test_rebuild_gate_requires_stored_equirects():
    ok, reason = stations_have_stored_equirects([])
    assert ok is False and reason == "no_stations"
    pans_only = [{"station_index": 0, "pans": [{"storage_path": "p"}] * 12, "equirect_path": None}]
    ok, reason = stations_have_stored_equirects(pans_only)
    assert ok is False and reason == "no_equirects"
    # Compile gate still accepts pans; rebuild must not.
    cok, creason = stations_have_compile_input(pans_only, kind="splat", has_roomplan=False)
    assert cok is True and creason == "pan_burst"


def test_rebuild_gate_ok_when_equirect_present():
    stations = [
        {"station_index": 0, "pans": [{"storage_path": "p"}], "equirect_path": None},
        {"station_index": 1, "equirect_path": "capture/s/stations/1/equirect.jpg"},
    ]
    ok, reason = stations_have_stored_equirects(stations)
    assert ok is True and reason == "equirect"
    ready = select_rebuild_stations(stations)
    assert [s["station_index"] for s in ready] == [1]


def test_rebuild_payload_writes_hotspot_player_without_stitch():
    from app.services.capture_worker import rebuild_tour_payload, CaptureWorkerError
    from app.services import equirect_stitcher as stitcher

    def boom(*_a, **_k):
        raise AssertionError("rebuild must not stitch pans")
    orig = stitcher.stitch_pan_to_equirect
    stitcher.stitch_pan_to_equirect = boom
    try:
        html, meta = rebuild_tour_payload([
            {"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0, "heading": 90, "equirect_path": "capture/s/stations/0/equirect.jpg"},
            {"id": "station-1", "room": "Living", "file": "scenes/1.jpg", "station_index": 1, "equirect_path": "capture/s/stations/1/equirect.jpg"},
        ], address="10 Main")
    finally:
        stitcher.stitch_pan_to_equirect = orig
    low = html.lower()
    assert "scenes/0.jpg" in html
    assert "placeHs" in html or "hotspot" in low
    assert "next station" in low
    assert "equirect-walkthrough" in meta
    assert '"next": "station-1"' in meta
    try:
        rebuild_tour_payload([{"station_index": 0, "pans": [{"x": 1}]}])
        assert False, "pans-only rebuild should fail"
    except CaptureWorkerError:
        pass


def test_ensure_station_equirect_skips_stitch_when_stored():
    import asyncio
    from app.services import capture_worker as cw

    jpeg = _jpeg(2048, 1024)
    stitched = {"n": 0}

    def boom(*_a, **_k):
        stitched["n"] += 1
        raise AssertionError("should not stitch when equirect exists")

    orig_stitch = cw.stitch_pan_to_equirect
    orig_dl = cw.download_bytes
    cw.stitch_pan_to_equirect = boom

    async def fake_dl(_path):
        return jpeg

    cw.download_bytes = fake_dl
    try:
        station = {
            "station_index": 0,
            "equirect_path": "capture/s/stations/0/equirect.jpg",
            "pans": [{"bytes": b"pan", "yaw": i * 15} for i in range(24)],
        }
        out = asyncio.run(cw.ensure_station_equirect("s", station))
        assert out == jpeg
        assert stitched["n"] == 0
    finally:
        cw.stitch_pan_to_equirect = orig_stitch
        cw.download_bytes = orig_dl


def test_rebuild_from_equirects_rewrites_html_not_scenes_when_hosted():
    import asyncio
    from app.services import capture_worker as cw

    jpeg = _jpeg(2048, 1024)
    uploaded = []
    stitched = {"n": 0}

    def boom(*_a, **_k):
        stitched["n"] += 1
        raise AssertionError("rebuild from hosted tour must not stitch")

    tour_json = (
        '{"version": 1, "kind": "equirect-walkthrough", "scenes": ['
        '{"id": "station-0", "title": "Kitchen", "room": "Kitchen", "panorama": "scenes/0.jpg", "heading": 90, "next": "station-1", "prev": null, "station_index": 0},'
        '{"id": "station-1", "title": "Living", "room": "Living", "panorama": "scenes/1.jpg", "heading": null, "next": null, "prev": "station-0", "station_index": 1}'
        ']}'
    ).encode("utf-8")

    orig_stitch = cw.stitch_pan_to_equirect
    orig_dl = cw.download_bytes
    orig_up = cw.upload_bytes
    cw.stitch_pan_to_equirect = boom

    async def fake_dl(path):
        if str(path).endswith("tour.json"):
            return tour_json
        if "scenes/" in str(path):
            return jpeg
        return None

    async def fake_up(path, data, _ctype, upsert=False):
        uploaded.append((path, data))
        return True

    async def fake_visit(*_a, **_k):
        return True

    cw.download_bytes = fake_dl
    cw.upload_bytes = fake_up
    orig_visit = None
    try:
        import app.services.client_comms as cc
        orig_visit = cc.register_property_visit
        cc.register_property_visit = fake_visit
    except Exception:
        orig_visit = None

    try:
        out = asyncio.run(cw.rebuild_tour_from_equirects(
            None,
            {"id": "job-1", "address": "10 Main"},
            {"id": "sess-1"},
        ))
        assert out == "tours/job-1/sess-1/index.html"
        assert stitched["n"] == 0
        paths = [p for p, _ in uploaded]
        assert "tours/job-1/sess-1/index.html" in paths
        assert "tours/job-1/sess-1/tour.json" in paths
        assert not any("/scenes/" in p for p in paths)
        html = next(d for p, d in uploaded if p.endswith("index.html")).decode("utf-8")
        assert "placeHs" in html
        assert "Next station" in html
        assert "scenes/0.jpg" in html
    finally:
        cw.stitch_pan_to_equirect = orig_stitch
        cw.download_bytes = orig_dl
        cw.upload_bytes = orig_up
        if orig_visit is not None:
            import app.services.client_comms as cc
            cc.register_property_visit = orig_visit


def test_router_rebuild_tour_endpoint_is_honest():
    src = (ROOT / "app/routers/capture.py").read_text()
    assert "/sessions/{session_id}/rebuild-tour" in src
    assert 'prefix="/rebuild-tour"' in src or '@router.post("/rebuild-tour")' in src
    assert "rebuild_tour_from_equirects" in src
    process = src.split("async def process_session")[1]
    assert "_run_rebuild_tour" in process
    delivered = process.split('status") == "delivered"')[1].split("now = datetime")[0]
    assert "_run_rebuild_tour" in delivered
    assert "rebuilt" in delivered
    web_review = ROOT.parent / "web/src/pages/CaptureReviewPage.tsx"
    web_jobs = ROOT.parent / "web/src/pages/JobsPage.tsx"
    web_api = ROOT.parent / "web/src/lib/capture-api.ts"
    assert "Rebuild tour player" in web_review.read_text()
    assert "Rebuild tour player" in web_jobs.read_text()
    assert "rebuild-tour" in web_api.read_text()
