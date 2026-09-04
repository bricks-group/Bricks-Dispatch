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
from app.services.floorplan_tracer import (
    FloorplanError,
    derive_floorplan,
    infer_station_poses,
    stations_poses_inferred,
    stations_to_rooms,
)
from app.services.tour_builder import (
    TourBuildError,
    build_tour_html,
    build_tour_json,
    normalize_tag,
    normalize_tags,
)


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
    # Exact Matterport-class 2:1 canvas from yaw-blend path.
    assert img.size == (2048, 1024)
    assert abs(img.size[0] / img.size[1] - 2.0) < 0.01


def test_stitch_refuses_thin_coverage():
    pans = [{"bytes": _jpeg(80, 60), "yaw": 0} for _ in range(3)]
    try:
        stitch_pan_to_equirect(pans)
        assert False, "should have failed"
    except StitchError:
        pass


def test_stitch_refuses_narrow_yaw_even_with_many_frames():
    # Dense burst but only ~45° of yaw — must not pass via frame-count fallback.
    pans = [{"bytes": _jpeg(80, 60, (40, 40, 40)), "yaw": i * 5} for i in range(12)]
    assert station_is_stitchable(pans) is False
    try:
        stitch_pan_to_equirect(pans)
        assert False, "narrow yaw should refuse"
    except StitchError as e:
        assert "360" in str(e).lower() or "span" in str(e).lower() or "sweep" in str(e).lower()


def test_exposure_normalize_reduces_brightness_jumps():
    from app.services.equirect_stitcher import _exposure_gains, _luma_mean_bgr
    import numpy as np
    dark = np.full((40, 40, 3), 40, dtype=np.uint8)
    bright = np.full((40, 40, 3), 200, dtype=np.uint8)
    mid = np.full((40, 40, 3), 110, dtype=np.uint8)
    lumas = [_luma_mean_bgr(dark), _luma_mean_bgr(mid), _luma_mean_bgr(bright)]
    gains = _exposure_gains(lumas)
    adjusted = [l * g for l, g in zip(lumas, gains)]
    assert max(adjusted) - min(adjusted) < max(lumas) - min(lumas)
    assert gains[0] > 1.0 and gains[2] < 1.0


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


def test_router_rebuild_floorplan_endpoint_is_honest():
    src = (ROOT / "app/routers/capture.py").read_text()
    assert "/sessions/{session_id}/rebuild-floorplan" in src
    assert '@router.post("/rebuild-floorplan")' in src
    assert "rebuild_floorplan_from_stations" in src
    assert "_run_rebuild_floorplan" in src
    process = src.split("async def process_session")[1]
    delivered = process.split('status") == "delivered"')[1].split("now = datetime")[0]
    assert "_run_rebuild_floorplan" in delivered
    worker = (ROOT / "app/services/capture_worker.py").read_text()
    assert "async def rebuild_floorplan_from_stations" in worker
    assert "No recapture, no stitch" in worker or "no stitch" in worker.lower()
    web_review = (ROOT.parent / "web/src/pages/CaptureReviewPage.tsx").read_text()
    web_jobs = (ROOT.parent / "web/src/pages/JobsPage.tsx").read_text()
    web_api = (ROOT.parent / "web/src/lib/capture-api.ts").read_text()
    assert "Rebuild floor plan" in web_review
    assert "Rebuild floor plan" in web_jobs
    assert "rebuild-floorplan" in web_api
    assert "rebuildFloorPlan" in web_api


def test_rebuild_floorplan_from_stations_uses_tracer_no_stitch():
    import asyncio
    import app.services.capture_worker as cw

    stations = [
        {"station_index": 0, "room": "Kitchen", "pose_x": 0.0, "pose_y": 0.0, "heading": 0},
        {"station_index": 1, "room": "Hall", "pose_x": 3.0, "pose_y": 0.0, "heading": 90},
    ]
    uploads = []

    async def fake_load_station_records(db, sid, rooms=None):
        return stations

    async def fake_load_roomplan(sid):
        return None

    async def fake_upload(path, data, content_type, upsert=False):
        uploads.append((path, data[:8], content_type, upsert))
        return True

    async def fake_attach(db, job, storage_path, session_id):
        return None

    async def boom_ensure(*a, **k):
        raise AssertionError("rebuild floor plan must not stitch")

    orig = (
        cw.load_station_records,
        cw.load_roomplan,
        cw.upload_bytes,
        cw._attach_floorplan_media,
        cw.ensure_station_equirect,
    )
    cw.load_station_records = fake_load_station_records
    cw.load_roomplan = fake_load_roomplan
    cw.upload_bytes = fake_upload
    cw._attach_floorplan_media = fake_attach
    cw.ensure_station_equirect = boom_ensure
    try:
        out = asyncio.run(cw.rebuild_floorplan_from_stations(
            None,
            {"id": "job1", "address": "10 Main", "booking_id": None},
            {"id": "sess1", "output_storage_path": "visits/job1/floorplan-sess1.png"},
        ))
        assert out == "visits/job1/floorplan-sess1.png"
        assert uploads and uploads[0][0] == out
        assert uploads[0][1] == b"\x89PNG\r\n\x1a\n"
        assert uploads[0][3] is True
    finally:
        (
            cw.load_station_records,
            cw.load_roomplan,
            cw.upload_bytes,
            cw._attach_floorplan_media,
            cw.ensure_station_equirect,
        ) = orig


def test_rebuild_floorplan_fails_honestly_without_input():
    import asyncio
    import app.services.capture_worker as cw

    async def fake_load_station_records(db, sid, rooms=None):
        return []

    async def fake_load_roomplan(sid):
        return None

    orig = (cw.load_station_records, cw.load_roomplan)
    cw.load_station_records = fake_load_station_records
    cw.load_roomplan = fake_load_roomplan
    try:
        try:
            asyncio.run(cw.rebuild_floorplan_from_stations(
                None, {"id": "job1", "address": ""}, {"id": "sess1"},
            ))
            assert False, "expected CaptureWorkerError"
        except cw.CaptureWorkerError as e:
            assert "station" in str(e).lower() or "RoomPlan" in str(e)
    finally:
        cw.load_station_records, cw.load_roomplan = orig


def test_normalize_tag_refuses_empty_label():
    assert normalize_tag({}) is None
    assert normalize_tag({"label": "  "}) is None
    assert normalize_tags([{"label": ""}, None, "x"]) == []
    tag = normalize_tag({"label": "Chef\u2019s kitchen", "yaw": 400, "pitch": -12, "note": "Wolf range"}, station_index=0)
    assert tag["label"].startswith("Chef")
    assert tag["note"] == "Wolf range"
    assert tag["station_index"] == 0
    assert -180 <= tag["yaw"] <= 180
    assert tag["pitch"] == -12


def test_tour_json_includes_tags_without_inventing():
    stations = [
        {"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0},
        {"id": "station-1", "room": "Living", "file": "scenes/1.jpg", "station_index": 1, "tags": [
            {"id": "tag-abc", "label": "Walk-in closet", "yaw": 40, "pitch": -5}
        ]},
    ]
    meta = build_tour_json(stations)
    assert '"tags": []' in meta
    assert "Walk-in closet" in meta
    assert "Chef" not in meta
    html = build_tour_html(stations, address="10 Main")
    assert 'id="taglayer"' in html
    assert "placeTags" in html
    assert "showCall" in html
    assert "Walk-in closet" in html
    assert "class=\"tg\"" in html or "className=\"tg\"" in html or ".tg{" in html
    empty_html = build_tour_html([
        {"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0},
    ])
    assert "Walk-in closet" not in empty_html
    assert "Chef" not in empty_html


def test_rebuild_payload_keeps_amenity_tags():
    from app.services.capture_worker import rebuild_tour_payload
    html, meta = rebuild_tour_payload([
        {
            "id": "station-0",
            "room": "Kitchen",
            "file": "scenes/0.jpg",
            "station_index": 0,
            "equirect_path": "capture/s/stations/0/equirect.jpg",
            "tags": [{"id": "tag-1", "label": "Island", "yaw": 15, "pitch": 0}],
        }
    ])
    assert "Island" in html and "Island" in meta
    assert "placeTags" in html


def test_attach_tags_overlays_session_tags():
    from app.services.capture_worker import attach_tags_to_stations
    stations = [
        {"station_index": 0, "room": "Kitchen", "file": "scenes/0.jpg", "tags": []},
        {"station_index": 1, "room": "Bed", "file": "scenes/1.jpg"},
    ]
    tagged = attach_tags_to_stations(stations, [
        {"id": "tag-1", "label": "Soaker tub", "yaw": 90, "pitch": -8, "station_index": 1},
    ])
    assert tagged[0]["tags"] == []
    assert tagged[1]["tags"][0]["label"] == "Soaker tub"


def test_router_has_tag_endpoints():
    src = (ROOT / "app/routers/capture.py").read_text()
    assert "/sessions/{session_id}/tags" in src
    assert "save_session_tags" in src
    assert "missing_tag_label" in src
    web_api = (ROOT.parent / "web/src/lib/capture-api.ts").read_text()
    web_editor = ROOT.parent / "web/src/components/TourTagEditor.tsx"
    assert "addTourTag" in web_api
    assert web_editor.exists()
    assert "Add tag" in web_editor.read_text()
    assert "TourTagEditor" in (ROOT.parent / "web/src/pages/CaptureReviewPage.tsx").read_text()
    assert "Feature tags" in web_editor.read_text()


def test_floorplan_empty_stations_raises():
    try:
        derive_floorplan([])
        assert False, "expected FloorplanError"
    except FloorplanError as e:
        assert "station" in str(e).lower() or "RoomPlan" in str(e)


def test_floorplan_inferred_marks_and_honesty_in_source():
    posed = infer_station_poses([
        {"station_index": 0, "room": "Kitchen", "heading": 90},
        {"station_index": 1, "room": "Hall", "heading": 0},
    ])
    assert stations_poses_inferred(posed) is True
    assert posed[0]["pose_inferred"] is True
    png = derive_floorplan([
        {"station_index": 0, "room": "Kitchen", "heading": 90},
        {"station_index": 1, "room": "Hall", "heading": 0},
    ])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (1600, 1100)
    src = (ROOT / "app/services/floorplan_tracer.py").read_text()
    assert "_draw_north_arrow" in src
    assert "_draw_scale_bar" in src
    assert "layout inferred" in src
    assert "not lidar" in src.lower() or "not lidar" in src
    # Must not claim Matterport lidar precision for inferred layouts.
    assert "Matterport lidar" not in src
    assert "surveyed precision" in src or "not lidar" in src


def test_floorplan_provided_poses_not_marked_inferred():
    stations = [
        {"station_index": 0, "room": "Kitchen", "pose_x": 0, "pose_y": 0, "heading": 90},
        {"station_index": 1, "room": "Hall", "pose_x": 4, "pose_y": 0, "heading": 0},
    ]
    posed = infer_station_poses(stations)
    assert stations_poses_inferred(posed) is False
    png = derive_floorplan(stations)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_floorplan_roomplan_path_still_works_without_stations():
    png = derive_floorplan([], roomplan={"rooms": [
        {"name": "Kitchen", "x": 0, "y": 0, "w": 12, "h": 10},
        {"name": "Bath", "x": 12, "y": 0, "w": 6, "h": 8},
    ]})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    src = (ROOT / "app/services/floorplan_tracer.py").read_text()
    assert "from_roomplan" in src


def test_deliver_session_notifies_on_compile_not_rebuild():
    """First compile deliver emails the agent; rebuild never does."""
    src = (ROOT / "app/routers/capture.py").read_text()
    assert "_notify_capture_session_delivered" in src
    assert "notify_capture_delivered" in src
    assert "notify=True" in src
    # Rebuild helpers call _deliver_session without notify=True
    rebuild_tour = src.split("async def _run_rebuild_tour")[1].split("async def _run_rebuild_floorplan")[0]
    rebuild_fp = src.split("async def _run_rebuild_floorplan")[1].split("@router.get")[0]
    assert "_deliver_session" in rebuild_tour
    assert "notify=True" not in rebuild_tour
    assert "_deliver_session" in rebuild_fp
    assert "notify=True" not in rebuild_fp
    # Compile path opts in
    compile = src.split("async def _compile_or_fail")[1].split("async def submit_session")[0]
    assert "notify=True" in compile
    assert "job=job" in compile


def test_notify_capture_delivered_reuses_job_delivered():
    src = (ROOT / "app/services/client_comms.py").read_text()
    assert "async def notify_capture_delivered" in src
    assert "/api/internal/comms/job-delivered" in src
    assert '"products"' in src or "'products'" in src
    # Same BOOKING path as photo delivery
    assert "_call_booking_comms" in src


def test_deliver_session_calls_notify_only_when_flagged():
    """_deliver_session gates email on notify=True (compile); rebuild omits it."""
    src = (ROOT / "app/routers/capture.py").read_text()
    body = src.split("async def _deliver_session")[1].split("async def _compile_or_fail")[0]
    assert "if notify and job is not None:" in body
    assert "_notify_capture_session_delivered" in body
    # Default notify=False in signature
    assert "notify: bool = False" in body


def test_notify_skips_when_not_first_of_kind_in_source():
    src = (ROOT / "app/routers/capture.py").read_text()
    fn = src.split("async def _notify_capture_session_delivered")[1].split("async def _deliver_session")[0]
    assert "_first_delivered_of_kind" in fn
    assert "notify_capture_delivered" in fn
    assert 'products=[product]' in fn
    first = src.split("async def _first_delivered_of_kind")[1].split("async def _notify_capture_session_delivered")[0]
    assert 'eq("status", "delivered")' in first
    assert "len(rows) <= 1" in first


def test_notify_capture_delivered_posts_products_shape():
    """Payload shape for Companion job-delivered (products + address)."""
    src = (ROOT / "app/services/client_comms.py").read_text()
    fn = src.split("async def notify_capture_delivered")[1]
    assert '"products": products' in fn or "'products': products" in fn
    assert '"address": addr' in fn or "'address': addr" in fn
    assert '"booking_id": bid' in fn or "'booking_id': bid" in fn
    assert "delivery_urls" in fn
    assert "if not products:" in fn
    assert "return False" in fn.split("if not products:")[1][:80]


def test_companion_job_delivered_handles_products():
    """Companion email names tour/floor plan when products are set (not photos)."""
    companion = Path("/workspace/Bricks-Companion-repo/backend/server.py")
    if not companion.exists():
        return
    src = companion.read_text()
    assert "products: list = []" in src or "products: list[" in src
    assert "is on the listing file" in src or "on the listing file" in src
    assert "Your photos are ready" in src  # photo path preserved
