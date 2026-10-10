"""Floor plans, drone/reel video, and Visit/3D get their own automatic QC that can hold them."""
from __future__ import annotations

import asyncio
import io
import sys
from types import ModuleType
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from app.services import qc_media_kinds as qmk
from app.services.qc_hold_notify import qc_hold_email_body, qc_hold_sms_body, photos_only

MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 64
WEBM_HEAD = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _cad_plan(mode="RGB"):
    bg = (0, 0, 0, 0) if mode == "RGBA" else "white"
    ink = (0, 0, 0, 255) if mode == "RGBA" else "black"
    img = Image.new(mode, (2400, 1600), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([120, 120, 2280, 1480], outline=ink, width=8)
    d.line([1200, 120, 1200, 1480], fill=ink, width=5)
    d.line([120, 800, 1200, 800], fill=ink, width=5)
    return _png(img)


def test_kind_routing():
    k = qmk.qc_kind_for_upload
    assert k({"filename": "Floor Plan.png"}) == "floorplan"
    assert k({"filename": "level-1-plan.jpg"}) == "floorplan"
    assert k({"filename": "plan.pdf"}) == "floorplan"
    assert k({"filename": "DJI_0042.MP4"}) == "video"
    assert k({"filename": "reel_tour.mov"}) == "video"
    assert k({"filename": "scan.splat"}) == "visit"
    # drone / staging / twilight / reel stills stay on the photo check
    for name in ("aerial_1.jpg", "staging_den.jpg", "twilight_front.jpg", "reel_still.jpg",
                 "ground_floor_living.jpg", "airplane_hangar.jpg"):
        assert k({"filename": name}) == "still", name


def test_clean_floor_plans_pass_including_transparent_export_and_pdf():
    for data, name in ((_cad_plan(), "Floor Plan.png"), (_cad_plan("RGBA"), "plan.png"),
                       (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\nendobj\ntrailer\n%%EOF\n", "floorplan.pdf")):
        v = qmk.check_floorplan_bytes(data, name)
        assert v["qc_status"] == "passed", (name, v)
    # The listing-photo check used to hold every PDF plan as unreadable.
    from app.services.qc_opencv import analyze_image_cv
    assert analyze_image_cv(b"%PDF-1.7\n" + b"0" * 600_000)["status"] == "rejected"


def test_blank_or_broken_floor_plans_are_held():
    cases = [
        (_png(Image.new("RGB", (900, 600), "white")), "plan.png", "blank"),
        (_png(Image.new("RGBA", (900, 600), (0, 0, 0, 0))), "plan.png", "blank"),
        (b"<html><body>AccessDenied</body></html>", "floorplan.pdf", "PDF"),
        (b"\x89PNG\r\n\x1a\nbroken", "Floor Plan.png", "could not be opened"),
        (b"", "plan.pdf", "empty"),
    ]
    for data, name, why in cases:
        v = qmk.check_floorplan_bytes(data, name)
        assert v["qc_status"] == "rejected" and why in v["qc_notes"], (name, v)


def test_video_container_check():
    assert qmk.check_video_head(MP4_HEAD, 9_000_000)["qc_status"] == "passed"
    assert qmk.check_video_head(WEBM_HEAD)["qc_status"] == "passed"
    assert qmk.check_video_head(b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 20)["qc_status"] == "passed"
    held = {
        b"": "empty",
        b"<!DOCTYPE html><html>Error</html>": "error page",
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 40: "still image",
        b"\x00" * 64: "not a recognized",
    }
    for head, why in held.items():
        v = qmk.check_video_head(head)
        assert v["qc_status"] == "rejected" and why in v["qc_notes"], v
    assert qmk.check_video_head(MP4_HEAD, 0)["qc_status"] == "rejected"


PLY_HEAD = b"ply\nformat binary_little_endian 1.0\nelement vertex 1\nend_header\n" + b"\x00" * 32
SPZ_HEAD = b"NGSP" + bytes(28)  # v4 plaintext header start
SPLAT_ONE = b"\x00" * 32  # one antimatter15 record


def test_visit_container_check():
    assert qmk.check_visit_head(PLY_HEAD, len(PLY_HEAD), "room.ply")["qc_status"] == "passed"
    assert qmk.check_visit_head(SPZ_HEAD, 4096, "room.spz")["qc_status"] == "passed"
    assert qmk.check_visit_head(b"\x1f\x8b" + b"\x00" * 40, 9000, "legacy.spz")["qc_status"] == "passed"
    assert qmk.check_visit_head(SPLAT_ONE * 10, 320, "scan.splat")["qc_status"] == "passed"
    held = [
        (b"", None, "scan.splat", "empty"),
        (b"<!DOCTYPE html><html>NoSuchKey</html>", 200, "scan.splat", "error page"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40, 80, "room.ply", "still image"),
        (b"not-a-ply-file-xxxxxx", 80, "room.ply", "PLY"),
        (b"\x00" * 64, 64, "room.spz", "SPZ"),
        (SPLAT_ONE, 33, "scan.splat", "multiple of 32"),
        (b"\x00" * 16, 16, "scan.splat", "truncated"),
    ]
    for head, total, name, why in held:
        v = qmk.check_visit_head(head, total, name)
        assert v["qc_status"] == "rejected" and why.lower() in v["qc_notes"].lower(), (name, why, v)
    assert qmk.check_visit_head(PLY_HEAD, 0, "room.ply")["qc_status"] == "rejected"


def test_visit_fetch_failure_holds(monkeypatch):
    """Fetch miss must hold (image_unavailable), not fail-open as passed."""
    async def boom(url, n=0):
        return None, None
    monkeypatch.setattr(qmk, "fetch_head", boom)
    v = _run(qmk.run_visit_qc("v", "https://x", "scan.splat"))
    assert v["qc_status"] == "rejected"
    assert v["qc_source"] == "unavailable"
    assert "image_unavailable" in (v.get("qc_notes") or "").lower()
    assert "skipped (visit unavailable)" not in (v.get("qc_notes") or "").lower()




class _FakeResp:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self._body = status, headers, body

    def aiter_bytes(self):
        chunks = iter([self._body[i:i + 1000] for i in range(0, len(self._body), 1000)])

        class _It:
            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(chunks)
                except StopIteration:
                    raise StopAsyncIteration

        return _It()


class _FakeStream:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


def test_fetch_head_reads_only_first_bytes_and_total(monkeypatch):
    seen = {}
    big = MP4_HEAD + b"\x00" * 200_000

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None):
            seen["range"] = (headers or {}).get("Range")
            if "empty" in url:
                return _FakeStream(_FakeResp(416, {}, b""))
            if "gone" in url:
                return _FakeStream(_FakeResp(403, {}, b"denied"))
            # server ignoring Range and streaming the whole file
            return _FakeStream(_FakeResp(200, {"content-length": str(len(big))}, big))

    monkeypatch.setattr(qmk.httpx, "AsyncClient", FakeClient)
    head, total = _run(qmk.fetch_head("https://blob/v.mp4"))
    assert len(head) == qmk.VIDEO_HEAD_BYTES and head.startswith(MP4_HEAD)
    assert total == len(big)
    assert seen["range"] == "bytes=0-65535"
    assert _run(qmk.fetch_head("https://blob/empty.mp4")) == (b"", 0)
    assert _run(qmk.run_video_qc("v", "https://blob/empty.mp4"))["qc_status"] == "rejected"
    # fetch failure (403) holds — same honesty as listing-still image_unavailable
    gone = _run(qmk.run_video_qc("v", "https://blob/gone.mp4"))
    assert gone["qc_status"] == "rejected"
    assert gone["qc_source"] == "unavailable"
    assert "image_unavailable" in (gone.get("qc_notes") or "").lower()


def test_total_from_content_range():
    r = _FakeResp(206, {"content-range": "bytes 0-65535/48000000"}, b"")
    assert qmk._total_from_headers(r) == 48_000_000


def test_video_fetch_failure_holds(monkeypatch):
    async def boom(url, n=0):
        return None, None
    monkeypatch.setattr(qmk, "fetch_head", boom)
    v = _run(qmk.run_video_qc("v", "https://x"))
    assert v["qc_status"] == "rejected"
    assert v["qc_source"] == "unavailable"
    assert "image_unavailable" in (v.get("qc_notes") or "").lower()


def test_floorplan_fetch_failure_holds(monkeypatch):
    async def boom(url):
        return None
    monkeypatch.setattr("app.services.qc_agent._fetch_image_bytes", boom)
    v = _run(qmk.run_floorplan_qc("fp", "https://x", "plan.png"))
    assert v["qc_status"] == "rejected"
    assert v["qc_source"] == "unavailable"
    assert "image_unavailable" in (v.get("qc_notes") or "").lower()
    assert "skipped (floor plan unavailable)" not in (v.get("qc_notes") or "").lower()


def _install_job(monkeypatch, rows, fetched):
    from app.services import qc_agent
    import app.services.storage as storage

    writes, photo_reviewed = {}, []

    class Q:
        def __init__(self, table):
            self.table, self._upd = table, None

        def select(self, *_a, **_k):
            return self

        def update(self, data):
            self._upd = data
            return self

        def eq(self, _k, v):
            if self._upd is not None:
                writes[v] = self._upd
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            if self.table == "jobs":
                return MagicMock(data={"package_name": "standard"})
            return MagicMock(data=rows)

    class DB:
        def table(self, name):
            return Q(name)

    async def fake_get_db():
        return DB()

    async def fake_sign(path, expiry=300):
        return f"https://signed/{path}"

    async def fake_photo(upload_id, url, **_k):
        photo_reviewed.append(upload_id)
        return {"qc_status": "passed", "qc_score": 95, "qc_notes": "ok", "qc_source": "opencv"}

    async def fake_fetch(url):
        return fetched[url.rsplit("/", 1)[-1]]

    async def fake_head(url, n=0):
        data = fetched[url.rsplit("/", 1)[-1]]
        return data[:n or 65536], len(data)

    fake_db_mod = ModuleType("app.shared.database")
    fake_db_mod.get_db = fake_get_db
    fake_db_mod.maybe_single_row = lambda res: (
        res.data if isinstance(getattr(res, "data", None), dict) else {}
    )
    monkeypatch.setitem(sys.modules, "app.shared.database", fake_db_mod)
    monkeypatch.setattr(storage, "get_signed_download_url", fake_sign)
    monkeypatch.setattr(qc_agent, "run_qc_on_upload", fake_photo)
    monkeypatch.setattr(qc_agent, "_fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(qmk, "fetch_head", fake_head)
    return qc_agent, writes, photo_reviewed


def _row(uid, filename, path, provider="as_is"):
    return {"id": uid, "file_url": path, "processed_url": path, "qc_variant_url": None,
            "qc_status": None, "processing_status": "done", "filename": filename,
            "provider": provider}


def test_job_qc_holds_broken_video_and_blank_plan_passes_pdf_plan(monkeypatch):
    rows = [
        _row("s1", "kitchen.jpg", "out/kitchen.jpg", "bricks_inhouse"),
        _row("f1", "floorplan.pdf", "raw/floorplan.pdf", "floorplan"),
        _row("f2", "Floor Plan.png", "raw/blank.png", "floorplan"),
        _row("v1", "DJI_0001.MP4", "raw/DJI_0001.MP4"),
        _row("v2", "reel_tour.mp4", "raw/reel_tour.mp4"),
        _row("vv1", "room.ply", "raw/room.ply", "visit"),
        _row("vv2", "scan.splat", "raw/scan.splat", "visit"),
        _row("vv3", "mesh.bin", "raw/mesh.bin", "visit"),  # link-ish — skipped
    ]
    fetched = {
        "floorplan.pdf": b"%PDF-1.6\n...\n%%EOF\n",
        "blank.png": _png(Image.new("RGB", (800, 600), "white")),
        "DJI_0001.MP4": MP4_HEAD,
        "reel_tour.mp4": b"<html><body>NoSuchKey</body></html>",
        "room.ply": PLY_HEAD,
        "scan.splat": b"<html><body>NoSuchKey</body></html>",
        "mesh.bin": b"whatever",
    }
    qc_agent, writes, photo_reviewed = _install_job(monkeypatch, rows, fetched)
    summary = _run(qc_agent.run_qc_for_job("job-k"))

    assert photo_reviewed == ["s1"]  # plans/videos/Visit never hit the listing-photo check
    assert writes["f1"]["qc_status"] == "passed"
    assert writes["f2"]["qc_status"] == "rejected"
    assert writes["v1"]["qc_status"] == "passed"
    assert writes["v2"]["qc_status"] == "rejected"
    assert writes["vv1"]["qc_status"] == "passed"
    assert writes["vv2"]["qc_status"] == "rejected"
    assert writes["vv3"]["qc_status"] == "passed"
    assert "Visit link" in writes["vv3"]["qc_notes"]
    assert summary["total"] == 8 and summary["rejected"] == 3 and summary["passed"] == 5
    assert summary["count_check"]["actual"] == 1  # package photo count is photos only


def test_hold_notice_says_files_when_visit_is_held():
    held = [{"filename": "scan.splat", "qc_notes": "Visit file is empty.", "provider": "visit"}]
    assert not photos_only(held)
    assert "1 listing file " in qc_hold_sms_body("10 King St", 1, photos=False)


def test_hold_notice_says_files_when_a_video_or_plan_is_held():
    held = [{"filename": "reel_tour.mp4", "qc_notes": "Video file is empty."},
            {"filename": "bath.jpg", "qc_notes": "Blurry"}]
    assert not photos_only(held)
    assert "2 listing files" in qc_hold_sms_body("10 King St", 2, photos=False)
    assert "listing files back" in qc_hold_email_body({"address": "10 King St"}, held)
    assert photos_only([{"filename": "bath.jpg"}])
    assert "1 listing photo " in qc_hold_sms_body("", 1)


def test_legacy_unreadable_floorplan_hold_predicate():
    """Only rejected floor plans/PDFs with unreadable_image are eligible to recheck."""
    f = qmk.is_legacy_unreadable_floorplan_hold
    assert f({
        "filename": "floorplan.pdf",
        "qc_status": "rejected",
        "qc_notes": "OpenCV QC (no vision provider): rejected Issues: unreadable_image",
    })
    assert f({
        "filename": "Floor Plan.png",
        "qc_status": "rejected",
        "qc_notes": "OpenCV hold kept after vision error: rejected Issues: unreadable_image",
    })
    # Real photo hold — must not match even if notes somehow mention unreadable_image
    assert not f({
        "filename": "kitchen.jpg",
        "qc_status": "rejected",
        "qc_notes": "OpenCV QC: rejected Issues: soft_focus; unreadable_image",
    })
    assert not f({
        "filename": "kitchen.jpg",
        "qc_status": "rejected",
        "qc_notes": "Issues: soft_focus",
    })
    # Honest floor-plan hold from the new plan check — must not match
    assert not f({
        "filename": "floorplan.pdf",
        "qc_status": "rejected",
        "qc_notes": "Floor plan looks blank (no linework; ink 0.0000).",
    })
    assert not f({
        "filename": "floorplan.pdf",
        "qc_status": "passed",
        "qc_notes": "Issues: unreadable_image",
    })
    assert not f({
        "filename": "floorplan.pdf",
        "qc_status": "rejected",
        "qc_notes": "",
    })


def test_job_qc_rechecks_legacy_unreadable_floorplan_preserves_photo_hold(monkeypatch):
    """Stuck PDF plan held as unreadable_image is re-run; soft_focus photo stay held."""
    rows = [
        {
            "id": "photo-hold",
            "file_url": "out/kitchen.jpg",
            "processed_url": "out/kitchen.jpg",
            "qc_variant_url": None,
            "qc_status": "rejected",
            "qc_notes": "OpenCV QC: rejected Issues: soft_focus",
            "processing_status": "done",
            "filename": "kitchen.jpg",
            "provider": "bricks_inhouse",
        },
        {
            "id": "fp-stuck",
            "file_url": "raw/floorplan.pdf",
            "processed_url": "raw/floorplan.pdf",
            "qc_variant_url": None,
            "qc_status": "rejected",
            "qc_notes": "OpenCV QC (no vision provider): rejected Issues: unreadable_image",
            "processing_status": "done",
            "filename": "floorplan.pdf",
            "provider": "floorplan",
        },
        {
            "id": "fp-blank",
            "file_url": "raw/blank-plan.png",
            "processed_url": "raw/blank-plan.png",
            "qc_variant_url": None,
            "qc_status": "rejected",
            "qc_notes": "Floor plan looks blank (no linework; ink 0.0001).",
            "processing_status": "done",
            "filename": "Floor Plan.png",
            "provider": "floorplan",
        },
        {
            "id": "photo-ok",
            "file_url": "out/living.jpg",
            "processed_url": "out/living.jpg",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "OpenCV pre-pass: passed",
            "processing_status": "done",
            "filename": "living.jpg",
            "provider": "bricks_inhouse",
        },
    ]
    fetched = {
        "floorplan.pdf": b"%PDF-1.6\n...\n%%EOF\n",
        "blank-plan.png": _png(Image.new("RGB", (800, 600), "white")),
    }
    qc_agent, writes, photo_reviewed = _install_job(monkeypatch, rows, fetched)
    summary = _run(qc_agent.run_qc_for_job("job-legacy"))

    assert photo_reviewed == []  # existing photo verdicts preserved — no photo re-score
    assert "photo-hold" not in writes
    assert "photo-ok" not in writes
    assert "fp-blank" not in writes  # honest plan hold preserved
    assert writes["fp-stuck"]["qc_status"] == "passed"
    assert "Floor plan PDF" in writes["fp-stuck"]["qc_notes"]
    assert summary["rejected"] == 2  # photo-hold + fp-blank tallied
    assert summary["passed"] == 2  # photo-ok + fp-stuck

def test_legacy_non_image_asset_pass_predicate():
    """Only Visit/video auto-passed as Non-image asset are eligible to recheck."""
    f = qmk.is_legacy_non_image_asset_pass
    assert f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "Non-image asset — QC skipped",
        "provider": "visit",
    })
    assert f({
        "filename": "DJI_0001.MP4",
        "qc_status": "passed",
        "qc_notes": "Non-image asset — QC skipped",
    })
    assert f({
        "filename": "room.ply",
        "qc_status": "passed",
        "qc_notes": "Non-image asset - QC skipped",  # dash variant in lower()
        "provider": "visit",
    })
    # Honest Visit/video container pass — must not recheck
    assert not f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "Visit container OK (splat).",
        "provider": "visit",
    })
    assert not f({
        "filename": "reel_tour.mp4",
        "qc_status": "passed",
        "qc_notes": "Video container OK (MP4/MOV).",
    })
    # Photo / floor plan — never match
    assert not f({
        "filename": "kitchen.jpg",
        "qc_status": "passed",
        "qc_notes": "Non-image asset — QC skipped",
    })
    assert not f({
        "filename": "floorplan.pdf",
        "qc_status": "passed",
        "qc_notes": "Non-image asset — QC skipped",
    })
    # Rejected Visit — not this legacy path (already held)
    assert not f({
        "filename": "scan.splat",
        "qc_status": "rejected",
        "qc_notes": "Non-image asset — QC skipped",
        "provider": "visit",
    })
    assert not f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "",
        "provider": "visit",
    })


def test_job_qc_rechecks_legacy_non_image_visit_and_video(monkeypatch):
    """Non-image auto-pass Visit/video are re-run; honest container passes stay."""
    rows = [
        {
            "id": "visit-legacy",
            "file_url": "raw/bad.splat",
            "processed_url": "raw/bad.splat",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "Non-image asset — QC skipped",
            "processing_status": "done",
            "filename": "bad.splat",
            "provider": "visit",
        },
        {
            "id": "video-legacy",
            "file_url": "raw/fake.mp4",
            "processed_url": "raw/fake.mp4",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "Non-image asset — QC skipped",
            "processing_status": "done",
            "filename": "fake.mp4",
            "provider": "as_is",
        },
        {
            "id": "visit-ok",
            "file_url": "raw/good.ply",
            "processed_url": "raw/good.ply",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "Visit container OK (PLY).",
            "processing_status": "done",
            "filename": "good.ply",
            "provider": "visit",
        },
        {
            "id": "video-ok",
            "file_url": "raw/good.mp4",
            "processed_url": "raw/good.mp4",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "Video container OK (MP4/MOV).",
            "processing_status": "done",
            "filename": "good.mp4",
            "provider": "as_is",
        },
        {
            "id": "photo-ok",
            "file_url": "out/living.jpg",
            "processed_url": "out/living.jpg",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "OpenCV pre-pass: passed",
            "processing_status": "done",
            "filename": "living.jpg",
            "provider": "bricks_inhouse",
        },
    ]
    fetched = {
        "bad.splat": b"<!DOCTYPE html><html>NoSuchKey</html>",
        "fake.mp4": b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40,
        "good.ply": PLY_HEAD,
        "good.mp4": MP4_HEAD,
    }
    qc_agent, writes, photo_reviewed = _install_job(monkeypatch, rows, fetched)
    summary = _run(qc_agent.run_qc_for_job("job-nonimage"))

    assert photo_reviewed == []
    assert "visit-ok" not in writes
    assert "video-ok" not in writes
    assert "photo-ok" not in writes
    assert writes["visit-legacy"]["qc_status"] == "rejected"
    assert "error page" in writes["visit-legacy"]["qc_notes"].lower()
    assert writes["video-legacy"]["qc_status"] == "rejected"
    assert "still image" in writes["video-legacy"]["qc_notes"].lower()
    assert summary["rejected"] >= 2


def test_legacy_unavailable_skip_pass_predicate():
    """Only fetch-miss skip-passes with the exact unavailable notes are eligible."""
    f = qmk.is_legacy_unavailable_skip_pass
    assert f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "QC skipped (image unavailable)",
    })
    assert f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "QC skipped (Visit unavailable)",
        "provider": "visit",
    })
    assert f({
        "filename": "reel.mp4",
        "qc_status": "passed",
        "qc_notes": "QC skipped (video unavailable)",
    })
    assert f({
        "filename": "floorplan.pdf",
        "qc_status": "passed",
        "qc_notes": "QC skipped (floor plan unavailable)",
    })
    # Honest container / photo passes — must not recheck
    assert not f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "OpenCV pre-pass: passed",
    })
    assert not f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "Visit container OK (splat).",
        "provider": "visit",
    })
    assert not f({
        "filename": "reel.mp4",
        "qc_status": "passed",
        "qc_notes": "Video container OK (MP4/MOV).",
    })
    # New image_unavailable hold is rejected, not this path
    assert not f({
        "filename": "living.jpg",
        "qc_status": "rejected",
        "qc_notes": "image_unavailable — could not fetch listing still for QC review",
    })
    # Provider / error skips — different honesty hole, leave alone
    assert not f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "QC skipped (no provider configured)",
    })
    assert not f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "QC skipped (error: TimeoutError)",
    })
    # Wrong kind for the note
    assert not f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "QC skipped (video unavailable)",
    })
    assert not f({
        "filename": "scan.splat",
        "qc_status": "passed",
        "qc_notes": "QC skipped (image unavailable)",
        "provider": "visit",
    })
    assert not f({
        "filename": "living.jpg",
        "qc_status": "passed",
        "qc_notes": "",
    })


def test_job_qc_rechecks_legacy_unavailable_skip_pass(monkeypatch):
    """Fetch-miss skip-pass still/Visit/video/plan are re-run; honest passes stay."""
    rows = [
        {
            "id": "still-legacy",
            "file_url": "out/dark.jpg",
            "processed_url": "out/dark.jpg",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "QC skipped (image unavailable)",
            "processing_status": "done",
            "filename": "dark.jpg",
            "provider": "bricks_inhouse",
        },
        {
            "id": "visit-legacy",
            "file_url": "raw/bad.splat",
            "processed_url": "raw/bad.splat",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "QC skipped (Visit unavailable)",
            "processing_status": "done",
            "filename": "bad.splat",
            "provider": "visit",
        },
        {
            "id": "video-legacy",
            "file_url": "raw/fake.mp4",
            "processed_url": "raw/fake.mp4",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "QC skipped (video unavailable)",
            "processing_status": "done",
            "filename": "fake.mp4",
            "provider": "as_is",
        },
        {
            "id": "fp-legacy",
            "file_url": "raw/blank-plan.png",
            "processed_url": "raw/blank-plan.png",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "QC skipped (floor plan unavailable)",
            "processing_status": "done",
            "filename": "Floor Plan.png",
            "provider": "floorplan",
        },
        {
            "id": "still-ok",
            "file_url": "out/living.jpg",
            "processed_url": "out/living.jpg",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "OpenCV pre-pass: passed",
            "processing_status": "done",
            "filename": "living.jpg",
            "provider": "bricks_inhouse",
        },
        {
            "id": "visit-ok",
            "file_url": "raw/good.ply",
            "processed_url": "raw/good.ply",
            "qc_variant_url": None,
            "qc_status": "passed",
            "qc_notes": "Visit container OK (PLY).",
            "processing_status": "done",
            "filename": "good.ply",
            "provider": "visit",
        },
    ]
    fetched = {
        "dark.jpg": _png(Image.new("RGB", (800, 600), (40, 40, 40))),
        "bad.splat": b"<!DOCTYPE html><html>NoSuchKey</html>",
        "fake.mp4": b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40,
        "blank-plan.png": _png(Image.new("RGB", (800, 600), "white")),
        "living.jpg": _png(Image.new("RGB", (800, 600), (180, 180, 180))),
        "good.ply": PLY_HEAD,
    }
    qc_agent, writes, photo_reviewed = _install_job(monkeypatch, rows, fetched)
    summary = _run(qc_agent.run_qc_for_job("job-unavail"))

    assert "still-ok" not in writes
    assert "visit-ok" not in writes
    assert "still-legacy" in photo_reviewed
    assert writes["visit-legacy"]["qc_status"] == "rejected"
    assert "error page" in writes["visit-legacy"]["qc_notes"].lower()
    assert writes["video-legacy"]["qc_status"] == "rejected"
    assert "still image" in writes["video-legacy"]["qc_notes"].lower()
    assert writes["fp-legacy"]["qc_status"] == "rejected"
    assert "blank" in writes["fp-legacy"]["qc_notes"].lower()
    assert summary["rejected"] >= 3


def _box(typ: bytes, payload_len: int, *, size_override=None, large=False) -> bytes:
    """Top-level ISO box header (+ zero payload up to 64 bytes, rest implied)."""
    if large:
        hdr = (1).to_bytes(4, "big") + typ + (16 + payload_len).to_bytes(8, "big")
    else:
        size = 8 + payload_len if size_override is None else size_override
        hdr = size.to_bytes(4, "big") + typ
    return hdr + b"\x00" * min(payload_len, 64)


_FTYP = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomavc1"  # 24 bytes


def test_mp4_without_moov_is_held():
    """Camera power loss / cut-off upload: ftyp + mdat to EOF, no moov index."""
    mdat_len = 5_000_000
    head = _FTYP + _box(b"mdat", mdat_len)
    total = len(_FTYP) + 8 + mdat_len
    v = qmk.check_video_head(head, total)
    assert v["qc_status"] == "rejected"
    assert "moov" in v["qc_notes"]
    # size-0 mdat (extends to EOF) with no moov before it — no total needed
    v0 = qmk.check_video_head(_FTYP + _box(b"mdat", 64, size_override=0))
    assert v0["qc_status"] == "rejected"


def test_mp4_mdat_running_past_eof_is_held():
    head = _FTYP + _box(b"moov", 40) + _box(b"mdat", 9_000_000)
    total = 24 + 48 + 8 + 4_000_000  # upload stopped halfway through mdat
    v = qmk.check_video_head(head, total)
    assert v["qc_status"] == "rejected"
    assert "truncated" in v["qc_notes"]
    # 64-bit largesize mdat overrunning too
    head64 = _FTYP + _box(b"mdat", 9_000_000, large=True)
    assert qmk.check_video_head(head64, 24 + 1_000_000)["qc_status"] == "rejected"


def test_complete_mp4_layouts_still_pass():
    # faststart: moov before mdat, mdat ends exactly at EOF
    head = _FTYP + _box(b"moov", 40) + _box(b"mdat", 9_000_000)
    total = 24 + 48 + 8 + 9_000_000
    assert qmk.check_video_head(head, total)["qc_status"] == "passed"
    # camera layout: mdat then moov beyond the fetched head — unknown, pass
    head2 = _FTYP + _box(b"mdat", 9_000_000)
    assert qmk.check_video_head(head2, 24 + 8 + 9_000_000 + 5_000)["qc_status"] == "passed"
    # no total size known — cannot judge EOF, pass
    assert qmk.check_video_head(head2)["qc_status"] == "passed"
    # moov then size-0 mdat to EOF
    head3 = _FTYP + _box(b"moov", 40) + _box(b"mdat", 64, size_override=0)
    assert qmk.check_video_head(head3)["qc_status"] == "passed"
    # QuickTime wide + mdat + moov wholly inside head (small clip)
    small = _FTYP + _box(b"wide", 0) + _box(b"mdat", 32) + _box(b"moov", 40)
    assert qmk.check_video_head(small, len(small))["qc_status"] == "passed"
    # existing fixture (ftyp + zero padding) unchanged
    assert qmk.check_video_head(MP4_HEAD, 9_000_000)["qc_status"] == "passed"
