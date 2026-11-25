"""Stored XSS in the published 3D tour player.

A photographer assigned to a job can store an amenity label, note, or station
room name. Those strings are serialized with json.dumps and pasted into an
inline <script> in tours/{job}/{session}/index.html (text/html). json.dumps
does not escape U+003C, so a closing script tag ends the player script and the
rest of the label is parsed as HTML.

These tests fail while that breakout is present and pass once the published
document stays a single inert player for every field that reaches the script.
"""
from __future__ import annotations

import asyncio
import io
import json
from html.parser import HTMLParser

import pytest
from fastapi import HTTPException
from PIL import Image

from app.routers import capture as cap
from app.services import capture_worker as cw
from app.services.capture_layout import equirect_path, session_tags_path

# Under the 80-character label cap and the 240-character note cap.
BREAK_LABEL = "</script><img src=x onerror=alert(1)>"
BREAK_LABEL_MIXED_CASE = "</ScRiPt><img src=x onerror=alert(1)>"
JOB_ID = "job-tour-1"
SESSION_ID = "sess-tour-1"
ASSIGNED_ID = "photog-assigned-1"
OTHER_ID = "photog-other-1"


class _Chain:
    def __init__(self, table: str, db: "MemDB"):
        self.table = table
        self.db = db
        self.op = "select"
        self.payload = None
        self.filters: list[tuple[str, object]] = []
        self.single = False
        self.limit_n: int | None = None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def maybe_single(self):
        self.single = True
        return self

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "update":
            matched = []
            for row in rows:
                if all(row.get(k) == v for k, v in self.filters):
                    row.update(self.payload)
                    matched.append(row)
            return _Result(matched)
        if self.op == "insert":
            incoming = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(incoming)
            return _Result(incoming)
        matched = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        if self.single:
            return _Result(matched[0] if matched else None)
        return _Result(matched)


class _Result:
    def __init__(self, data):
        self.data = data


class MemDB:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def table(self, name):
        return _Chain(name, self)


class BlobStore:
    """Byte store. upload/download do not rewrite the body."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def upload(self, path, data, content_type="application/octet-stream", upsert=True):
        raw = data if isinstance(data, bytes) else str(data).encode("utf-8")
        self.objects[path] = (raw, content_type or "")
        return True

    async def download(self, path):
        item = self.objects.get(path)
        return item[0] if item else None


class TourDom(HTMLParser):
    """HTMLParser script data ends at a case-insensitive </script end tag.

    That is the same RAWTEXT rule the template's inline script is parsed with
    (html.parser.HTMLParser.CDATA_CONTENT_ELEMENTS / set_cdata_mode).
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.starts: list[tuple[str, list]] = []
        self.scripts: list[dict] = []
        self._open: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.starts.append((tag, list(attrs)))
        self._open.append(tag)
        if tag == "script":
            self.scripts.append({"attrs": dict(attrs), "data": []})

    def handle_endtag(self, tag):
        if self._open:
            self._open.pop()

    def handle_data(self, data):
        if self.scripts and self._open and self._open[-1] == "script":
            self.scripts[-1]["data"].append(data)


def _jpeg(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 80, 120)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _member(actor_id: str) -> dict:
    return {
        "id": actor_id,
        "role": "photographer",
        "title": "Photographer IC",
        "vendor_id": "vendor-1",
        "is_active": True,
        "email": "photog@example.com",
    }


def _job(assignee_id: str = ASSIGNED_ID) -> dict:
    return {
        "id": JOB_ID,
        "address": "10 Main St",
        "job_type": "photos",
        "sqft": 1200,
        "vendor_id": "vendor-1",
        "assignee_id": assignee_id,
        "status": "assigned",
        "checklist": None,
        "booking_id": "book-1",
        "package_id": None,
        "package_name": None,
        "add_ons": None,
        "client_instructions": None,
        "property_details": None,
    }


def _session(**extra) -> dict:
    row = {
        "id": SESSION_ID,
        "job_id": JOB_ID,
        "kind": "splat",
        "status": "delivered",
        "frame_count": 1,
        "output_storage_path": f"tours/{JOB_ID}/{SESSION_ID}/index.html",
    }
    row.update(extra)
    return row


def _hosted_tour(title: str = "Kitchen", room: str = "Kitchen", tags: list | None = None) -> bytes:
    doc = {
        "version": 2,
        "kind": "equirect-walkthrough",
        "scenes": [{
            "id": "station-0",
            "title": title,
            "room": room,
            "panorama": "scenes/0.jpg",
            "heading": 0,
            "next": None,
            "prev": None,
            "station_index": 0,
            "tags": tags or [],
        }],
    }
    return json.dumps(doc).encode("utf-8")


def _install_store(monkeypatch) -> BlobStore:
    store = BlobStore()
    monkeypatch.setattr(cw, "upload_bytes", store.upload)
    monkeypatch.setattr(cw, "download_bytes", store.download)
    monkeypatch.setattr(cap, "upload_bytes", store.upload)
    return store


def _install_db(monkeypatch, db: MemDB) -> None:
    async def get_db():
        return db

    monkeypatch.setattr(cap, "get_db", get_db)


def _install_actor(monkeypatch, actor_id: str) -> None:
    async def resolve(authorization, x_user_id=None, x_internal_api_key=None):
        # Identity only. Job visibility is the real job_visible_to check.
        assert authorization == "Bearer assigned-session"
        assert not x_internal_api_key
        return actor_id

    async def get_member(actor):
        return _member(actor)

    monkeypatch.setattr(cap, "resolve_vendor_actor_id", resolve)
    monkeypatch.setattr(cap, "get_vendor_member", get_member)


def _seed_hosted(store: BlobStore, **tour_kwargs) -> None:
    store.objects[f"tours/{JOB_ID}/{SESSION_ID}/tour.json"] = (
        _hosted_tour(**tour_kwargs),
        "application/json",
    )
    store.objects[f"tours/{JOB_ID}/{SESSION_ID}/scenes/0.jpg"] = (
        _jpeg(2048, 1024),
        "image/jpeg",
    )


def _published(store: BlobStore) -> tuple[str, str]:
    path = f"tours/{JOB_ID}/{SESSION_ID}/index.html"
    assert path in store.objects, f"tour was not published; objects={list(store.objects)}"
    raw, content_type = store.objects[path]
    return raw.decode("utf-8"), content_type


def assert_published_tour_is_inert(html: str, content_type: str) -> None:
    """The published bytes are an HTML document whose script is only the player."""
    assert (content_type or "").split(";")[0].strip().lower() == "text/html", content_type
    dom = TourDom()
    dom.feed(html)
    dom.close()
    handlers = [
        (tag, attrs)
        for tag, attrs in dom.starts
        if any(str(key).lower().startswith("on") for key, _val in attrs)
    ]
    assert not handlers, f"event-handler markup in tour HTML: {handlers}"
    player = [
        script for script in dom.scripts
        if "placeTags" in "".join(script["data"])
        and (script["attrs"].get("type") or "").lower() != "application/json"
    ]
    assert player, (
        "inline player script ended before the tour runtime; "
        f"script lengths={[len(''.join(s['data'])) for s in dom.scripts]}"
    )
    for script in dom.scripts:
        body = "".join(script["data"])
        typ = (script["attrs"].get("type") or "").lower()
        if typ == "application/json":
            json.loads(body)


def test_break_label_fits_the_tag_field_limits():
    assert len(BREAK_LABEL) <= 80
    assert len(BREAK_LABEL_MIXED_CASE) <= 80
    assert len(BREAK_LABEL) <= 240


def test_unassigned_photographer_does_not_publish_a_tour(monkeypatch):
    """The tag route authorizes the job before it stores a label or rebuilds."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job(assignee_id=ASSIGNED_ID)]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, OTHER_ID)
    _seed_hosted(store)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(cap.add_tour_tag(
            SESSION_ID,
            {"label": BREAK_LABEL, "station_index": 0, "yaw": 10, "pitch": 0},
            authorization="Bearer assigned-session",
        ))
    assert caught.value.status_code == 404
    assert f"tours/{JOB_ID}/{SESSION_ID}/index.html" not in store.objects
    tags_path = session_tags_path(SESSION_ID)
    assert tags_path not in store.objects


def test_assigned_photographer_label_does_not_break_out_of_the_player(monkeypatch):
    """POST /api/capture/sessions/{id}/tags → save_session_tags → rebuild → index.html."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    result = asyncio.run(cap.add_tour_tag(
        SESSION_ID,
        {
            "label": BREAK_LABEL,
            "note": "Wolf range",
            "station_index": 0,
            "yaw": 12,
            "pitch": -4,
        },
        authorization="Bearer assigned-session",
    ))
    assert result["tag"]["station_index"] == 0
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)
    # The amenity text is still data in the player, not an element.
    assert "Wolf range" in html


def test_assigned_photographer_note_does_not_break_out_of_the_player(monkeypatch):
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    asyncio.run(cap.add_tour_tag(
        SESSION_ID,
        {
            "label": "Pantry",
            "note": BREAK_LABEL,
            "station_index": 0,
            "yaw": 1,
            "pitch": 2,
        },
        authorization="Bearer assigned-session",
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)
    assert "Pantry" in html


def test_job_scoped_tag_route_does_not_break_out_of_the_player(monkeypatch):
    """POST /api/capture/tags uses the same publish path as the session route."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    asyncio.run(cap.add_tour_tag_for_job(
        {
            "job_id": JOB_ID,
            "session_id": SESSION_ID,
            "label": BREAK_LABEL_MIXED_CASE,
            "station_index": 0,
            "yaw": 0,
            "pitch": 0,
        },
        authorization="Bearer assigned-session",
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)


def test_rebuild_republishes_a_stored_label_without_a_new_breakout(monkeypatch):
    """POST /sessions/{id}/rebuild-tour recompiles tags.json already on disk."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    stored = asyncio.run(cw.save_session_tags(SESSION_ID, [{
        "id": "tag-stored01",
        "label": BREAK_LABEL,
        "note": "kept",
        "yaw": 5,
        "pitch": 0,
        "station_index": 0,
    }]))
    assert stored and stored[0]["label"]
    asyncio.run(cap.rebuild_tour_for_session(
        SESSION_ID,
        {"job_id": JOB_ID},
        authorization="Bearer assigned-session",
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)


def test_station_room_name_does_not_break_out_of_the_player(monkeypatch):
    """ensure_station writes the room into station.json; compile embeds it."""
    store = _install_store(monkeypatch)
    db = MemDB()
    session = _session(status="in_progress", output_storage_path=None)
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [session]
    db.tables["capture_frames"] = [{
        "id": "frame-1",
        "session_id": SESSION_ID,
        "room_id": None,
        "storage_path": equirect_path(SESSION_ID, 0),
        "created_at": "2026-11-01T00:00:00+00:00",
    }]
    db.tables["capture_rooms"] = []
    db.tables["capture_pipeline"] = []
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    store.objects[equirect_path(SESSION_ID, 0)] = (_jpeg(2048, 1024), "image/jpeg")
    asyncio.run(cap.ensure_station(
        {
            "job_id": JOB_ID,
            "kind": "splat",
            "room": BREAK_LABEL,
            "station_index": 0,
            "heading": 90,
        },
        authorization="Bearer assigned-session",
    ))
    out = asyncio.run(cw.process_capture_session(db, _job(), session, "splat"))
    assert out == f"tours/{JOB_ID}/{SESSION_ID}/index.html"
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)


def test_job_scoped_rebuild_republishes_a_stored_label_without_a_breakout(monkeypatch):
    """POST /api/capture/rebuild-tour is the same compiler as the session route."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    asyncio.run(cw.save_session_tags(SESSION_ID, [{
        "id": "tag-jobrebuild",
        "label": BREAK_LABEL,
        "yaw": 0,
        "pitch": 0,
        "station_index": 0,
    }]))
    asyncio.run(cap.rebuild_tour_for_job(
        {"job_id": JOB_ID, "session_id": SESSION_ID, "kind": "splat"},
        authorization="Bearer assigned-session",
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)


def test_delete_keeps_remaining_label_inert(monkeypatch):
    """DELETE /sessions/{id}/tags/{tag_id} recompiles whatever labels remain."""
    store = _install_store(monkeypatch)
    db = MemDB()
    db.tables["jobs"] = [_job()]
    db.tables["capture_sessions"] = [_session()]
    _install_db(monkeypatch, db)
    _install_actor(monkeypatch, ASSIGNED_ID)
    _seed_hosted(store)
    asyncio.run(cw.save_session_tags(SESSION_ID, [
        {
            "id": "tag-keep",
            "label": BREAK_LABEL,
            "yaw": 0,
            "pitch": 0,
            "station_index": 0,
        },
        {
            "id": "tag-drop",
            "label": "Island",
            "yaw": 10,
            "pitch": 0,
            "station_index": 0,
        },
    ]))
    asyncio.run(cap.delete_tour_tag(
        SESSION_ID,
        "tag-drop",
        job_id=JOB_ID,
        authorization="Bearer assigned-session",
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)
    assert "Island" not in html


def test_rebuild_of_hosted_scene_title_does_not_break_out(monkeypatch):
    """A scene title already stored in tour.json is re-embedded on rebuild."""
    store = _install_store(monkeypatch)
    _seed_hosted(store, title=BREAK_LABEL, room=BREAK_LABEL)
    asyncio.run(cw.rebuild_tour_from_equirects(
        None,
        {"id": JOB_ID, "address": "10 Main St"},
        {"id": SESSION_ID, "kind": "splat", "status": "delivered"},
    ))
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)


def test_compile_keeps_station_manifest_tags_inert(monkeypatch):
    """compile_tour overlays tags.json, which save_session_tags also copies onto the station."""
    store = _install_store(monkeypatch)
    db = MemDB()
    session = _session(status="in_progress")
    db.tables["capture_frames"] = [{
        "id": "frame-1",
        "session_id": SESSION_ID,
        "room_id": None,
        "storage_path": equirect_path(SESSION_ID, 0),
        "created_at": "2026-11-01T00:00:00+00:00",
    }]
    db.tables["capture_rooms"] = []
    store.objects[equirect_path(SESSION_ID, 0)] = (_jpeg(2048, 1024), "image/jpeg")
    monkeypatch.setattr(cw, "upload_bytes", store.upload)
    monkeypatch.setattr(cw, "download_bytes", store.download)
    asyncio.run(cw.save_session_tags(SESSION_ID, [{
        "id": "tag-compile1",
        "label": "Nook",
        "note": BREAK_LABEL,
        "yaw": 0,
        "pitch": 0,
        "station_index": 0,
    }]))
    out = asyncio.run(cw.process_capture_session(db, _job(), session, "splat"))
    assert out.endswith("index.html")
    html, content_type = _published(store)
    assert_published_tour_is_inert(html, content_type)
    assert "Nook" in html


def test_header_address_and_title_stay_text(monkeypatch):
    """Address and title are escaped into the document chrome, not the script."""
    del monkeypatch
    from app.services.tour_builder import build_tour_html

    html = build_tour_html(
        [{"id": "station-0", "room": "Kitchen", "file": "scenes/0.jpg", "station_index": 0}],
        address=BREAK_LABEL,
        title=BREAK_LABEL,
    )
    assert_published_tour_is_inert(html, "text/html; charset=utf-8")
