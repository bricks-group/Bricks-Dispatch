"""Floor plans and drone/reel video get their own automatic QC that can hold them."""
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
                       (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj", "floorplan.pdf")):
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
    # fetch failure (403) fails open, never holds on our own outage
    assert _run(qmk.run_video_qc("v", "https://blob/gone.mp4"))["qc_source"] == "skipped"


def test_total_from_content_range():
    r = _FakeResp(206, {"content-range": "bytes 0-65535/48000000"}, b"")
    assert qmk._total_from_headers(r) == 48_000_000


def test_video_fetch_failure_fails_open(monkeypatch):
    async def boom(url, n=0):
        return None, None
    monkeypatch.setattr(qmk, "fetch_head", boom)
    v = _run(qmk.run_video_qc("v", "https://x"))
    assert v["qc_status"] == "passed" and v["qc_source"] == "skipped"


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
    ]
    fetched = {
        "floorplan.pdf": b"%PDF-1.6\n...",
        "blank.png": _png(Image.new("RGB", (800, 600), "white")),
        "DJI_0001.MP4": MP4_HEAD,
        "reel_tour.mp4": b"<html><body>NoSuchKey</body></html>",
    }
    qc_agent, writes, photo_reviewed = _install_job(monkeypatch, rows, fetched)
    summary = _run(qc_agent.run_qc_for_job("job-k"))

    assert photo_reviewed == ["s1"]  # plans/videos never hit the listing-photo check
    assert writes["f1"]["qc_status"] == "passed"
    assert writes["f2"]["qc_status"] == "rejected"
    assert writes["v1"]["qc_status"] == "passed"
    assert writes["v2"]["qc_status"] == "rejected"
    assert summary["total"] == 5 and summary["rejected"] == 2 and summary["passed"] == 3
    assert summary["count_check"]["actual"] == 1  # package photo count is photos only


def test_hold_notice_says_files_when_a_video_or_plan_is_held():
    held = [{"filename": "reel_tour.mp4", "qc_notes": "Video file is empty."},
            {"filename": "bath.jpg", "qc_notes": "Blurry"}]
    assert not photos_only(held)
    assert "2 listing files" in qc_hold_sms_body("10 King St", 2, photos=False)
    assert "listing files back" in qc_hold_email_body({"address": "10 King St"}, held)
    assert photos_only([{"filename": "bath.jpg"}])
    assert "1 listing photo " in qc_hold_sms_body("", 1)
