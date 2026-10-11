"""Guided Visit video capture: resumable parts into capture_sessions(kind=visit)/rooms/frames."""
from __future__ import annotations

import asyncio
import itertools
import json

import pytest
from fastapi import HTTPException

from app.routers import capture_visit_video as vv

MB = 1024 * 1024


class FakeRepo:
    def __init__(self):
        self.sessions: list[dict] = []
        self.rooms: list[dict] = []
        self.frames: list[dict] = []
        self.pipe: list[dict] = []
        self._ids = itertools.count(1)

    def _id(self, p):
        return f"{p}-{next(self._ids)}"

    async def in_progress_session(self, job_id):
        rows = [s for s in self.sessions if s["job_id"] == job_id and s["status"] == "in_progress"]
        return rows[-1] if rows else None

    async def latest_session(self, job_id):
        rows = [s for s in self.sessions if s["job_id"] == job_id]
        return rows[-1] if rows else None

    async def session_by_id(self, sid, job_id):
        return next((s for s in self.sessions if s["id"] == sid and s["job_id"] == job_id), None)

    async def create_session(self, job_id):
        s = {"id": self._id("s"), "job_id": job_id, "status": "in_progress", "kind": "visit"}
        self.sessions.append(s)
        for i, (t, d, st) in enumerate(vv.PIPELINE):
            self.pipe.append({"session_id": s["id"], "title": t, "detail": d, "status": st, "position": i})
        return s

    async def recordings(self, sid):
        return sorted([r for r in self.rooms if r["session_id"] == sid], key=lambda r: r["position"])

    async def insert_recording(self, sid, job_id, name, position, seconds):
        r = {"id": self._id("r"), "session_id": sid, "job_id": job_id, "name": name, "position": position,
             "frames": seconds, "tone": "bad"}
        self.rooms.append(r)
        return r

    async def update_recording(self, rid, fields):
        for r in self.rooms:
            if r["id"] == rid:
                r.update(fields)

    async def delete_recording(self, sid, rid):
        self.frames = [f for f in self.frames if f["room_id"] != rid]
        self.rooms = [r for r in self.rooms if r["id"] != rid]

    async def parts(self, sid, rid):
        return [f for f in self.frames if f["session_id"] == sid and f["room_id"] == rid and f["role"] == vv.PART_ROLE]

    async def insert_part(self, row):
        self.frames.append(dict(row))

    async def update_session(self, sid, fields):
        for s in self.sessions:
            if s["id"] == sid:
                s.update(fields)

    async def pipeline(self, sid):
        return [p for p in self.pipe if p["session_id"] == sid]

    async def update_pipeline(self, sid, title, status, detail=None):
        for p in self.pipe:
            if p["session_id"] == sid and p["title"] == title:
                p["status"] = status
                if detail is not None:
                    p["detail"] = detail


class FakeBlobs:
    def __init__(self, proxy=False):
        self.files: dict[str, bytes] = {}
        self.proxy = proxy
        self.signed_puts: list[str] = []

    async def read_json(self, path):
        raw = self.files.get(path)
        return json.loads(raw) if raw else None

    async def write_json(self, path, data):
        self.files[path] = json.dumps(data).encode()
        return True

    async def sign_put(self, path):
        self.signed_puts.append(path)
        if self.proxy:
            return {"path": path, "token": None, "signed_url": f"https://api.test/blob-put/{path}?sig=x"}
        return {"path": path, "token": "tok", "signed_url": f"https://sb.test/upload/sign/{path}?token=tok"}

    async def sign_get(self, path):
        return f"https://sb.test/get/{path}?sig=y"

    async def remove(self, paths):
        self.removed = list(paths)
        for p in paths:
            self.files.pop(p, None)
        return True


class StaleBlobs(FakeBlobs):
    """Storage behind a CDN: overwriting an object keeps serving the first copy for a while."""

    def __init__(self):
        super().__init__()
        self.first: dict[str, bytes] = {}

    async def write_json(self, path, data):
        await super().write_json(path, data)
        self.first.setdefault(path, self.files[path])
        return True

    async def read_json(self, path):
        raw = self.first.get(path) if path in self.files else None
        return json.loads(raw) if raw else None


JOB = {"id": "job-1", "address": "12 Example Crescent"}


@pytest.fixture
def env(monkeypatch):
    repo, blobs = FakeRepo(), FakeBlobs()
    monkeypatch.setattr(vv, "repo", repo)
    monkeypatch.setattr(vv, "blobs", blobs)

    async def fake_job(*_a, **_k):
        return JOB

    monkeypatch.setattr(vv, "_job", fake_job)
    monkeypatch.setattr(vv, "verify_internal_api_key", lambda k: k == "internal")
    return repo, blobs


def run(coro):
    return asyncio.run(coro)


def _rec(size=20 * MB + 123, duration=95.0, **extra):
    return {"job_id": "job-1", "name": "Main floor", "scope": "floor", "floor_index": 0, "size_bytes": size,
            "duration_s": duration, "rooms": [{"name": "Kitchen", "t0": 0, "t1": 50}, {"name": "Living", "t0": 50, "t1": 95}],
            **extra}


def _upload_all(rid, total, part_size, size):
    for n in range(1, total + 1):
        expected = part_size if n < total else size - part_size * (total - 1)
        run(vv.commit_part(rid, {"job_id": "job-1", "part": n, "size": expected}))


def test_session_uses_visit_kind_and_pipeline(env):
    repo, _ = env
    b = run(vv.ensure_visit_session({"job_id": "job-1"}))
    assert b["session_id"] and b["status"] == "in_progress"
    assert repo.sessions[0]["kind"] == "visit"
    assert [p["title"] for p in b["pipeline"]] == ["Recorded", "Reconstructing", "QC", "Delivered"]
    assert b["limits"]["max_bytes"] == 4_000_000_000 and b["limits"]["max_seconds"] == 900
    # Idempotent: one in-progress session per job.
    b2 = run(vv.ensure_visit_session({"job_id": "job-1"}))
    assert b2["session_id"] == b["session_id"] and len(repo.sessions) == 1
    # No default "Room 1" row (that's the 360 flow).
    assert b["recordings"] == []


def test_create_recording_splits_into_parts_and_writes_manifest(env):
    repo, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    assert out["part_size"] == 8 * MB
    assert out["parts_total"] == 3  # 20 MB + 123 B in 8 MB parts
    sid, rid = out["session_id"], out["recording_id"]
    man = json.loads(blobs.files[vv.manifest_path(sid, rid)])
    assert man["rooms"][1] == {"name": "Living", "t0": 50.0, "t1": 95.0}
    assert man["complete"] is False and man["scope"] == "floor"
    assert repo.rooms[0]["frames"] == 95 and repo.rooms[0]["tone"] == "bad"


def test_blob_proxy_fallback_uses_smaller_parts(env, monkeypatch):
    repo, _ = env
    proxy = FakeBlobs(proxy=True)
    monkeypatch.setattr(vv, "blobs", proxy)
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec(size=10 * MB)))
    assert out["part_size"] == 4 * MB and out["parts_total"] == 3


@pytest.mark.parametrize("payload,code", [
    ({"size_bytes": 4_000_000_001}, "visit_recording_too_large"),
    ({"size_bytes": 500_000}, "visit_recording_too_small"),
    ({"duration_s": 16 * 60}, "visit_recording_too_long"),
    ({"scope": "house"}, "bad_visit_recording"),
    ({"rooms": [{"name": "A", "t0": 10, "t1": 5}]}, "bad_visit_rooms"),
    ({"rooms": [{"name": "A", "t0": 0, "t1": 50}, {"name": "B", "t0": 20, "t1": 60}]}, "bad_visit_rooms"),
])
def test_limits_and_validation(env, payload, code):
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    with pytest.raises(HTTPException) as e:
        run(vv.create_recording(_rec(**payload)))
    assert code in str(e.value.detail)


def test_duplicate_name_rejected(env):
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    run(vv.create_recording(_rec()))
    with pytest.raises(HTTPException) as e:
        run(vv.create_recording(_rec()))
    assert "visit_duplicate_name" in str(e.value.detail)


def test_resume_reports_committed_parts_and_commit_is_idempotent(env):
    repo, _ = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    rid = out["recording_id"]
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 2, "size": 8 * MB}))
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 2, "size": 8 * MB}))  # retried after a dropped response
    st = run(vv.recording_status(rid, job_id="job-1"))
    assert st["committed"] == [2] and st["parts_total"] == 3 and st["complete"] is False
    assert len(repo.frames) == 1 and repo.frames[0]["station_index"] == 2
    assert repo.frames[0]["storage_path"].endswith("/part-00002.bin")


def test_commit_checks_part_size(env):
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    rid = run(vv.create_recording(_rec()))["recording_id"]
    with pytest.raises(HTTPException) as e:
        run(vv.commit_part(rid, {"job_id": "job-1", "part": 1, "size": 1000}))
    assert "bad_part_size" in str(e.value.detail)
    # Last part carries the remainder.
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 3, "size": 4 * MB + 123}))


def test_sign_batches_and_bounds(env):
    _, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    rid = run(vv.create_recording(_rec()))["recording_id"]
    out = run(vv.sign_parts(rid, {"job_id": "job-1", "parts": [1, 2, 3]}))
    assert [p["part"] for p in out["parts"]] == [1, 2, 3]
    assert out["parts"][0]["token"] == "tok"
    with pytest.raises(HTTPException):
        run(vv.sign_parts(rid, {"job_id": "job-1", "parts": [4]}))
    with pytest.raises(HTTPException):
        run(vv.sign_parts(rid, {"job_id": "job-1", "parts": list(range(1, 30))}))


def test_complete_requires_every_part_then_submit_writes_worker_spec(env):
    repo, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    rid, sid, total = out["recording_id"], out["session_id"], out["parts_total"]
    with pytest.raises(HTTPException) as e:
        run(vv.submit_visit({"job_id": "job-1"}))
    assert "visit_parts_missing" in str(e.value.detail)
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 1, "size": 8 * MB}))
    with pytest.raises(HTTPException) as e:
        run(vv.complete_recording(rid, {"job_id": "job-1"}))
    assert "visit_parts_missing" in str(e.value.detail)
    _upload_all(rid, total, out["part_size"], 20 * MB + 123)
    run(vv.complete_recording(rid, {"job_id": "job-1"}))
    assert repo.rooms[0]["tone"] == "ok"
    b = run(vv.submit_visit({"job_id": "job-1"}))
    assert b["status"] == "submitted" and b["queued"] == vv.job_spec_path(sid)
    spec = json.loads(blobs.files[vv.job_spec_path(sid)])
    assert spec["kind"] == "visit" and spec["recordings"][0]["parts"][-1].endswith("part-00003.bin")
    assert spec["recordings"][0]["rooms"][0]["name"] == "Kitchen"
    assert repo.sessions[0]["status"] == "submitted" and repo.sessions[0]["frame_count"] == 3
    pipe = {p["title"]: p["status"] for p in repo.pipe}
    assert pipe["Recorded"] == "done" and pipe["Reconstructing"] == "active"
    # Session closed: no more recordings into it.
    with pytest.raises(HTTPException):
        run(vv.create_recording({**_rec(), "name": "Upstairs"}))


def test_room_scope_recording_gets_a_whole_clip_room_mark(env):
    _, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording({**_rec(size=2 * MB, duration=70), "scope": "room", "name": "Primary bedroom", "rooms": []}))
    _upload_all(out["recording_id"], out["parts_total"], out["part_size"], 2 * MB)
    run(vv.complete_recording(out["recording_id"], {"job_id": "job-1"}))
    run(vv.submit_visit({"job_id": "job-1"}))
    spec = json.loads(blobs.files[vv.job_spec_path(out["session_id"])])
    assert spec["recordings"][0]["rooms"] == [{"name": "Primary bedroom", "t0": 0.0, "t1": 70.0}]


def test_discard_recording_for_retake(env):
    repo, _ = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    rid = run(vv.create_recording(_rec()))["recording_id"]
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 1, "size": 8 * MB}))
    run(vv.discard_recording(rid, job_id="job-1"))
    assert repo.rooms == [] and repo.frames == []


def test_submit_right_after_complete_trusts_db_over_stale_manifest(env, monkeypatch):
    repo, _ = env
    stale = StaleBlobs()
    monkeypatch.setattr(vv, "blobs", stale)
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    rid, sid = out["recording_id"], out["session_id"]
    _upload_all(rid, out["parts_total"], out["part_size"], 20 * MB + 123)
    run(vv.complete_recording(rid, {"job_id": "job-1"}))
    # The CDN still serves the pre-complete manifest...
    assert run(stale.read_json(vv.manifest_path(sid, rid)))["complete"] is False
    # ...but status, the session bundle and submit all go by capture_rooms.tone.
    assert run(vv.recording_status(rid, job_id="job-1"))["complete"] is True
    assert run(vv.get_visit_session("job-1"))["recordings"][0]["complete"] is True
    b = run(vv.submit_visit({"job_id": "job-1"}))
    assert b["status"] == "submitted"
    spec = json.loads(stale.files[vv.job_spec_path(sid)])
    assert len(spec["recordings"][0]["parts"]) == 3


def test_submit_still_blocks_a_recording_the_db_says_is_unfinished(env):
    repo, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    run(vv.commit_part(out["recording_id"], {"job_id": "job-1", "part": 1, "size": 8 * MB}))
    assert repo.rooms[0]["tone"] == "bad"
    with pytest.raises(HTTPException) as e:
        run(vv.submit_visit({"job_id": "job-1"}))
    assert "visit_parts_missing" in str(e.value.detail)
    assert run(vv.recording_status(out["recording_id"], job_id="job-1"))["complete"] is False


def test_discard_removes_manifest_and_every_part_object(env):
    repo, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    keep = run(vv.create_recording({**_rec(), "name": "Upstairs"}))
    out = run(vv.create_recording(_rec()))
    rid, sid = out["recording_id"], out["session_id"]
    run(vv.commit_part(rid, {"job_id": "job-1", "part": 1, "size": 8 * MB}))
    res = run(vv.discard_recording(rid, job_id="job-1"))
    assert res["deleted"] is True and res["files_removed"] is True
    assert set(blobs.removed) == {vv.manifest_path(sid, rid)} | {vv.part_path(sid, rid, n) for n in (1, 2, 3)}
    assert vv.manifest_path(sid, rid) not in blobs.files
    # The other recording's manifest is untouched.
    assert vv.manifest_path(sid, keep["recording_id"]) in blobs.files
    assert all(p.startswith(f"capture/{sid}/visit/{rid}/") for p in blobs.removed)


def test_discard_still_succeeds_when_storage_delete_fails(env, monkeypatch):
    repo, blobs = env
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    rid = run(vv.create_recording(_rec()))["recording_id"]

    async def boom(_paths):
        raise RuntimeError("storage down")

    monkeypatch.setattr(blobs, "remove", boom)
    res = run(vv.discard_recording(rid, job_id="job-1"))
    assert res["deleted"] is True and res["files_removed"] is False and repo.rooms == []


def test_worker_source_is_internal_only_and_ordered(env):
    run(vv.ensure_visit_session({"job_id": "job-1"}))
    out = run(vv.create_recording(_rec()))
    _upload_all(out["recording_id"], out["parts_total"], out["part_size"], 20 * MB + 123)
    run(vv.complete_recording(out["recording_id"], {"job_id": "job-1"}))
    run(vv.submit_visit({"job_id": "job-1"}))
    with pytest.raises(HTTPException):
        run(vv.worker_source(out["session_id"], job_id="job-1", x_internal_api_key="nope"))
    src = run(vv.worker_source(out["session_id"], job_id="job-1", x_internal_api_key="internal"))
    urls = src["recordings"][0]["part_urls"]
    assert len(urls) == 3 and "part-00001" in urls[0] and "part-00003" in urls[2]


def test_kind_constraint_missing_is_a_clear_503(env, monkeypatch):
    repo, _ = env

    async def boom(_job_id):
        raise Exception('new row violates check constraint "capture_sessions_kind_check" (23514)')

    monkeypatch.setattr(repo, "create_session", boom)
    with pytest.raises(HTTPException) as e:
        run(vv.ensure_visit_session({"job_id": "job-1"}))
    assert e.value.status_code == 503 and "capture_visit_kind_missing" in str(e.value.detail)


def test_routes_registered_and_separate_from_360_tour():
    paths = {r.path for r in vv.router.routes}
    assert "/api/capture/visit-video/recordings/{recording_id}/sign" in paths
    assert vv.KIND == "visit"
    from app.routers import capture
    assert "visit" not in capture._VALID_KINDS  # kind=splat stays the 360 tour
