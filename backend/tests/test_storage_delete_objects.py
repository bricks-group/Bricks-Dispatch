"""storage.delete_objects: batched Supabase remove, invalid paths skipped, failures reported not raised."""
import asyncio

from app.services import storage


class _Bucket:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    async def remove(self, paths):
        if self.fail:
            raise RuntimeError("down")
        self.log.append(list(paths))
        return [{"name": p} for p in paths]


class _DB:
    def __init__(self, log, fail=False):
        self.storage = self
        self.log, self.fail, self.bucket_name = log, fail, None

    def from_(self, name):
        self.bucket_name = name
        return _Bucket(self.log, self.fail)


def _patch(monkeypatch, db):
    async def get_db():
        return db
    monkeypatch.setattr(storage, "get_db", get_db)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


def test_delete_objects_removes_normalized_unique_paths(monkeypatch):
    log = []
    db = _DB(log)
    _patch(monkeypatch, db)
    ok = asyncio.run(storage.delete_objects(["/capture/s/visit/r/manifest.json", "capture/s/visit/r/manifest.json",
                                             "capture/s/visit/r/part-00001.bin", "capture/../etc", ""]))
    assert ok is True and db.bucket_name == storage.STORAGE_BUCKET
    assert log == [["capture/s/visit/r/manifest.json", "capture/s/visit/r/part-00001.bin"]]


def test_delete_objects_batches_and_reports_failure(monkeypatch):
    log = []
    _patch(monkeypatch, _DB(log))
    assert asyncio.run(storage.delete_objects([f"capture/s/p-{i:05d}" for i in range(2500)])) is True
    assert [len(b) for b in log] == [1000, 1000, 500]
    _patch(monkeypatch, _DB([], fail=True))
    assert asyncio.run(storage.delete_objects(["capture/s/x"])) is False
    assert asyncio.run(storage.delete_objects([])) is True
