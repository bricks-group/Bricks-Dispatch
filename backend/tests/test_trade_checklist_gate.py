"""
Trade job completion gate (Kam, 2026-10-31).

Vendors tick trade checklist items in go / Expo; ticks and proof are saved on
jobs.checklist. A trade job can't be marked complete until every item is
ticked with its proof: photo items need a photo, inventory items need item +
room lines, report items need the uploaded report. Media jobs don't change.
Storage, webhooks and post-production are mocked; nothing touches Stripe.
"""
import asyncio
import copy
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.routers import vendor_os as vos
from app.routers import dispatch as dispatch_router
from app.services import job_checklist as jc
from app.services import storage
from app.services.capture_entitlements import stamp_capture
from app.services.vendor_profile import job_required_skills, job_required_vendor_type
from app.services.visit_checklist import trade_checklist_key
from app.shared.schemas import JobStatusUpdate

ACTOR = "member-trade"
JOB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.single = "select", None, [], False

    def select(self, *a, **k):
        return self

    def update(self, fields):
        self.op, self.payload = "update", fields
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, *a):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def __getattr__(self, name):
        return lambda *a, **k: self

    def _rows(self):
        return [r for r in self.db.tables.setdefault(self.name, [])
                if all(str(r.get(c)) == str(v) for c, v in self.filters)]

    async def execute(self):
        if self.op == "update":
            matched = self._rows()
            for r in matched:
                r.update(copy.deepcopy(self.payload))
            self.db.updates.append((self.name, copy.deepcopy(self.payload)))
            return SimpleNamespace(data=[copy.deepcopy(r) for r in matched])
        rows = [copy.deepcopy(r) for r in self._rows()]
        if self.single:
            return None if not rows else SimpleNamespace(data=rows[0])
        return SimpleNamespace(data=rows, count=len(rows))


class _Db:
    def __init__(self, job):
        self.tables = {"jobs": [job], "uploads": [], "vendor_members": []}
        self.updates = []

    def table(self, name):
        return _Q(self, name)


def _job(**extra) -> dict:
    job = {
        "id": JOB_ID,
        "assignee_id": ACTOR,
        "vendor_id": "default_vendor",
        "booking_id": None,
        "status": "in_progress",
        "job_type": "staging",
        "package_name": "Home staging",
        "checklist": [],
        "deliverables_count": 0,
        "uploaded_count": 0,
    }
    job.update(extra)
    return job


@pytest.fixture
def env(monkeypatch):
    state = {"uploaded": [], "removed": []}

    def install(job):
        db = _Db(job)
        state["db"] = db

        async def fake_get_db():
            return db

        async def fake_upload(bucket, path, data, ct):
            state["uploaded"].append((bucket, path, ct, len(data)))
            return True

        async def fake_remove(bucket, paths):
            state["removed"].extend(paths)
            return True

        async def fake_sign(bucket, path, expiry=3600):
            return f"https://signed.example/{bucket}/{path}"

        monkeypatch.setattr(vos, "get_db", fake_get_db)
        monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
        monkeypatch.setattr(vos, "get_vendor_member", AsyncMock(return_value={"id": ACTOR, "vendor_id": "default_vendor", "role": "Stager"}))
        monkeypatch.setattr(vos, "send_dispatch_status_update_webhook", AsyncMock())
        monkeypatch.setattr(storage, "upload_to_bucket", fake_upload)
        monkeypatch.setattr(storage, "remove_from_bucket", fake_remove)
        monkeypatch.setattr(storage, "signed_url_in_bucket", fake_sign)
        post = AsyncMock()
        import app.services.post_production as pp
        monkeypatch.setattr(pp, "process_job_assets", post)
        state["post"] = post
        return db

    state["install"] = install
    return state


def _db_job(db):
    return db.tables["jobs"][0]


def _detail(exc):
    d = exc.value.detail
    return d.get("message") if isinstance(d, dict) else str(d)


def _photo(name="room.jpg", ct="image/jpeg", data=b"\xff\xd8jpegbytes"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": ct}))


def _complete():
    return _run(vos.update_vendor_job_status(JOB_ID, JobStatusUpdate(status="completed"), "Bearer t", None))


def _items(view):
    return {i["label"].split(". ", 1)[1]: i for i in view["items"]}


# ── Proof kinds from labels ──────────────────────────────────────────────────

def test_proof_kinds_follow_the_labels():
    assert jc.proof_kind("1. After photos of each room") == jc.PROOF_PHOTO
    assert jc.proof_kind("3. Inventory of pieces placed: item and room") == jc.PROOF_INVENTORY
    assert jc.proof_kind("1. Upload your report") == jc.PROOF_REPORT
    assert jc.proof_kind("1. Upload your report: measurements and plan") == jc.PROOF_REPORT
    assert jc.proof_kind("2. Confirm access and lock-up notes") == jc.PROOF_TICK
    assert jc.proof_kind("3. Lights off") == jc.PROOF_TICK
    assert jc.proof_kind("6. Note any damage or issues, with a photo") == jc.PROOF_FINDINGS
    assert jc.proof_kind("3. Note anything else you found") == jc.PROOF_FINDINGS
    assert jc.proof_kind("2. Inventory check against the install list, with any damage noted") == jc.PROOF_INVENTORY


def test_effective_checklist_ids_are_stable_before_anything_is_saved():
    a = jc.effective_checklist(_job())
    b = jc.effective_checklist(_job())
    assert [i["id"] for i in a] == [i["id"] for i in b]
    # Reads (stamp_capture) hand out the same ids the first tick will use.
    stamped = stamp_capture(_job())
    assert [i["id"] for i in stamped["checklist"]] == [i["id"] for i in a]
    assert stamped["checklist_title"] == "Job checklist"


def test_media_job_checklist_and_gate_unchanged():
    media = _job(job_type="photo", package_name="Essentials")
    assert jc.completion_block_message(media) is None
    stamped = stamp_capture(dict(media))
    assert stamped["checklist_title"] == "Shoot checklist"
    assert "proof" not in stamped["checklist"][0]


# ── Completion gate ──────────────────────────────────────────────────────────

def test_trade_job_cannot_complete_without_ticks_and_proof(env):
    db = env["install"](_job())
    with pytest.raises(HTTPException) as e:
        _complete()
    assert e.value.status_code == 400
    msg = _detail(e)
    assert msg.startswith("Finish the job checklist before you mark this job complete.")
    assert "Before photos of each room you stage (add at least one photo)" in msg
    assert "Inventory of pieces placed: item and room (add each piece with its item and room)" in msg
    assert _db_job(db)["status"] == "in_progress"
    env["post"].assert_not_awaited()


def test_tick_without_proof_is_refused(env):
    env["install"](_job())
    view = _run(vos.get_trade_checklist(JOB_ID, "Bearer t", None))
    before = _items(view)["Before photos of each room you stage"]
    with pytest.raises(HTTPException) as e:
        _run(vos.patch_trade_checklist_item(JOB_ID, before["id"], vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    assert _detail(e) == "Before you tick this, add at least one photo."


def test_stager_job_completes_after_ticks_and_proof(env):
    db = env["install"](_job())
    items = _items(_run(vos.get_trade_checklist(JOB_ID, "Bearer t", None)))
    for label in ("Before photos of each room you stage", "After photos of each staged room"):
        iid = items[label]["id"]
        _run(vos.add_trade_checklist_photo(JOB_ID, iid, _photo(), None, "Bearer t", None))
        _run(vos.patch_trade_checklist_item(JOB_ID, iid, vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    inv = items["Inventory of pieces placed: item and room"]["id"]
    with pytest.raises(HTTPException) as e:
        _run(vos.patch_trade_checklist_item(
            JOB_ID, inv, vos.ChecklistItemPatch(inventory=[{"item": "Sofa", "room": ""}]), "Bearer t", None))
    assert "both the item and the room" in _detail(e)
    view = _run(vos.patch_trade_checklist_item(
        JOB_ID, inv,
        vos.ChecklistItemPatch(inventory=[{"item": "Grey sofa", "room": "Living room"}], completed=True),
        "Bearer t", None))
    assert view["complete"] is True and view["missing"] == []

    # Saved for real on jobs.checklist (not read-time only), with storage paths.
    saved = _db_job(db)["checklist"]
    assert all(i["completed"] for i in saved)
    assert saved[0]["photos"][0]["path"].startswith(f"{JOB_ID}/")
    assert saved[2]["inventory"] == [{"item": "Grey sofa", "room": "Living room"}]
    assert all(b == jc.PROOF_BUCKET for b, *_ in env["uploaded"])
    # The vendor view signs URLs and never shows storage paths.
    assert view["items"][0]["photos"][0]["url"].startswith("https://signed.example/")
    assert "path" not in view["items"][0]["photos"][0]

    out = _complete()
    assert _db_job(db)["status"] == "completed"
    env["post"].assert_awaited_once()
    assert out


def test_removing_the_last_photo_unticks_the_item(env):
    db = env["install"](_job())
    items = _items(_run(vos.get_trade_checklist(JOB_ID, "Bearer t", None)))
    iid = items["Before photos of each room you stage"]["id"]
    view = _run(vos.add_trade_checklist_photo(JOB_ID, iid, _photo(), None, "Bearer t", None))
    _run(vos.patch_trade_checklist_item(JOB_ID, iid, vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    pid = _items(view)["Before photos of each room you stage"]["photos"][0]["id"]
    view = _run(vos.delete_trade_checklist_photo(JOB_ID, iid, pid, "Bearer t", None))
    item = _items(view)["Before photos of each room you stage"]
    assert item["completed"] is False and item["photos"] == []
    assert env["removed"] and env["removed"][0].startswith(f"{JOB_ID}/")
    assert _db_job(db)["checklist"][0]["completed"] is False


def test_inspector_needs_the_uploaded_report(env):
    db = env["install"](_job(job_type="inspection", package_name="Home inspection"))
    items = _items(_run(vos.get_trade_checklist(JOB_ID, "Bearer t", None)))
    rep = items["Upload your report"]["id"]
    access = items["Confirm access and lock-up notes"]["id"]
    _run(vos.patch_trade_checklist_item(JOB_ID, access, vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    with pytest.raises(HTTPException) as e:
        _complete()
    assert "Upload your report (upload the report)" in _detail(e)
    with pytest.raises(HTTPException) as e:
        _run(vos.upload_trade_checklist_report(JOB_ID, rep, _photo("r.txt", "text/plain", b"hi"), "Bearer t", None))
    assert _detail(e) == "Upload the report as a PDF or a photo."
    _run(vos.upload_trade_checklist_report(JOB_ID, rep, _photo("report.pdf", "application/pdf", b"%PDF-1.4"), "Bearer t", None))
    with pytest.raises(HTTPException) as e:
        _complete()
    assert "Upload your report (tick it)" in _detail(e)
    _run(vos.patch_trade_checklist_item(JOB_ID, rep, vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    _complete()
    assert _db_job(db)["status"] == "completed"


def test_cleaner_damage_item_takes_a_note_and_photo_or_nothing_found(env):
    env["install"](_job(job_type="cleaning", package_name="Turnover clean"))
    items = _items(_run(vos.get_trade_checklist(JOB_ID, "Bearer t", None)))
    dmg = items["Note any damage or issues, with a photo"]
    assert dmg["proof"] == jc.PROOF_FINDINGS and dmg["photo_required"] is True
    with pytest.raises(HTTPException) as e:
        _run(vos.patch_trade_checklist_item(JOB_ID, dmg["id"], vos.ChecklistItemPatch(note="Scuff in hall", completed=True), "Bearer t", None))
    assert "add a photo" in _detail(e)
    view = _run(vos.patch_trade_checklist_item(JOB_ID, dmg["id"], vos.ChecklistItemPatch(none_found=True, completed=True), "Bearer t", None))
    assert _items(view)["Note any damage or issues, with a photo"]["completed"] is True


def test_media_job_refuses_checklist_edits_and_completes_as_before(env):
    db = env["install"](_job(job_type="photo", package_name="Essentials", checklist=[]))
    with pytest.raises(HTTPException) as e:
        _run(vos.get_trade_checklist(JOB_ID, "Bearer t", None))
    assert e.value.status_code == 409
    _complete()
    assert _db_job(db)["status"] == "completed"


def test_closed_job_checklist_is_locked(env):
    env["install"](_job(status="completed"))
    items = _items(_run(vos.get_trade_checklist(JOB_ID, "Bearer t", None)))
    iid = items["Before photos of each room you stage"]["id"]
    with pytest.raises(HTTPException) as e:
        _run(vos.patch_trade_checklist_item(JOB_ID, iid, vos.ChecklistItemPatch(completed=True), "Bearer t", None))
    assert e.value.status_code == 409


def test_dispatch_status_route_gates_vendors_on_trade_jobs(monkeypatch):
    db = _Db(_job())

    async def fake_get_db():
        return db

    import app.shared.auth as auth
    monkeypatch.setattr(dispatch_router, "get_db", fake_get_db)
    monkeypatch.setattr(auth, "resolve_vendor_actor_id", AsyncMock(return_value=ACTOR))
    monkeypatch.setattr(auth, "get_vendor_member", AsyncMock(return_value={"id": ACTOR, "vendor_id": "default_vendor"}))
    # Hotfix 2026-11-09: this route is internal-only now, so a vendor is
    # refused outright (403) before any checklist or status logic runs.
    with pytest.raises(HTTPException) as e:
        _run(dispatch_router.update_job_status(JOB_ID, JobStatusUpdate(status="completed"), "Bearer t", None, None))
    assert e.value.status_code == 403
    assert db.tables["jobs"][0]["status"] == "in_progress"


# ── De-staging goes to stagers ───────────────────────────────────────────────

@pytest.mark.parametrize("job", [
    {"job_type": "destage"},
    {"job_type": "staging_pickup"},
    {"job_type": "staging_removal"},
    {"job_type": "de-staging"},
    {"job_type": "", "package_name": "Destage and pickup"},
    {"job_type": "photo", "package_id": "staging_removal"},
    {"package_name": "Staging pickup"},
])
def test_destaging_jobs_need_a_stager(job):
    assert job_required_vendor_type(job) == "stager"
    assert job_required_skills(job) == {"staging"}
    assert trade_checklist_key(job) == "destage"


def test_media_and_explicit_types_unchanged():
    assert job_required_vendor_type({"job_type": "photo", "package_name": "Essentials"}) == "photographer"
    assert job_required_vendor_type({"job_type": "photo", "package_name": "Virtual staging"}) == "photographer"
    assert job_required_vendor_type({"job_type": "destage", "required_vendor_type": "cleaner"}) == "cleaner"
