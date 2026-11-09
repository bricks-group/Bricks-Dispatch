"""R2 routing: STORAGE_READ_BACKEND / r2: prefix send storage calls to Cloudflare R2.

Covers bucket mapping, presigned GET/PUT, uploads, deletes, named evidence buckets
and the nightly backup, without touching the network or Supabase.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest

from app.services import ops_autopilot, r2_storage, storage, upload_backend

R2_ENV = {
    "R2_ACCOUNT_ID": "acct123",
    "R2_ACCESS_KEY_ID": "AKIDTEST",
    "R2_SECRET_ACCESS_KEY": "secrettest",
    "R2_BUCKET": "bricks-deliverables",
    "R2_BUCKET_EVIDENCE": "bricks-evidence",
    "R2_BUCKET_BACKUPS": "bricks-backups",
}


@pytest.fixture
def r2_env(monkeypatch):
    for k, v in R2_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STORAGE_READ_BACKEND", "r2")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    async def no_db():
        raise AssertionError("Supabase must not be called in R2 mode")

    monkeypatch.setattr(storage, "get_db", no_db)
    return monkeypatch


@pytest.fixture
def fake_http(monkeypatch):
    calls = []

    class _Resp:
        def __init__(self, code=200, content=b"bytes"):
            self.status_code, self.content = code, content

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            calls.append(("GET", url))
            return _Resp()

        async def put(self, url, content=None, headers=None, **k):
            calls.append(("PUT", url, headers))
            return _Resp()

        async def delete(self, url, **k):
            calls.append(("DELETE", url))
            return _Resp(404)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


def _bucket_and_key(url):
    p = urlparse(url)
    assert p.netloc == "acct123.r2.cloudflarestorage.com"
    _, bucket, key = p.path.split("/", 2)
    return bucket, key


def test_read_backend_off_without_config(monkeypatch):
    monkeypatch.setenv("STORAGE_READ_BACKEND", "r2")
    for k in R2_ENV:
        monkeypatch.delenv(k, raising=False)
    assert r2_storage.read_backend_is_r2() is False


def test_bucket_map(r2_env):
    assert r2_storage.r2_target("brxs-deliverables", "bricks-go/jobs/a.jpg") == ("bricks-deliverables", "bricks-go/jobs/a.jpg")
    assert r2_storage.r2_target("noshow-evidence", "r2:x/y.jpg") == ("bricks-evidence", "x/y.jpg")
    assert r2_storage.r2_target("job-checklist-proof", "j/1.jpg") == ("bricks-evidence", "job-checklist-proof/j/1.jpg")
    assert r2_storage.r2_target("db-backups", "2026-11-09.jsonl.gz") == ("bricks-backups", "2026-11-09.jsonl.gz")


def test_legacy_path_signed_url_goes_to_r2(r2_env):
    url = asyncio.run(storage.get_signed_download_url("bricks-go/jobs/j/u/a.jpg", 3600))
    assert _bucket_and_key(url.split("?")[0]) == ("bricks-deliverables", "bricks-go/jobs/j/u/a.jpg")
    assert "X-Amz-Signature=" in url and "secrettest" not in url


def test_signed_upload_is_plain_put(r2_env):
    signed = asyncio.run(storage.create_signed_upload_url("bricks-go/jobs/j/u/a.jpg"))
    assert signed["token"] is None and signed["path"] == "bricks-go/jobs/j/u/a.jpg"
    assert "X-Amz-Signature=" in signed["signed_url"]


def test_download_and_upload_bytes(r2_env, fake_http):
    assert asyncio.run(storage.download_bytes("r2:k/a.jpg")) == b"bytes"
    assert asyncio.run(storage.upload_bytes("k/b.jpg", b"x", "image/jpeg")) is True
    assert [c[0] for c in fake_http] == ["GET", "PUT"]


def test_upload_file_writes_r2_prefixed_paths(r2_env, fake_http):
    r2_env.setenv("STILLS_UPLOAD_BACKEND", "r2")
    assert upload_backend.backend_name() == "r2"
    from io import BytesIO
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, "JPEG")
    fp, tp, b64, qp = asyncio.run(storage.upload_file(buf.getvalue(), "job1", "up1", "../a.jpg", "image/jpeg"))
    assert fp == "r2:bricks-go/jobs/job1/up1/a.jpg"
    assert tp == "r2:bricks-go/jobs/job1/up1/thumb_a.jpg" and qp == "r2:bricks-go/jobs/job1/up1/qc_a.jpg"
    assert b64 and len([c for c in fake_http if c[0] == "PUT"]) == 3


def test_delete_objects_on_r2(r2_env, fake_http):
    assert asyncio.run(storage.delete_objects(["capture/s/a", "capture/../etc", "r2:capture/s/b"])) is True
    keys = sorted(_bucket_and_key(c[1].split("?")[0])[1] for c in fake_http)
    assert keys == ["capture/s/a", "capture/s/b"]


def test_named_evidence_bucket(r2_env, fake_http):
    assert asyncio.run(storage.upload_to_bucket("job-checklist-proof", "j/1.jpg", b"x", "image/jpeg")) is True
    url = asyncio.run(storage.signed_url_in_bucket("noshow-evidence", "t/1.jpg", 600))
    assert _bucket_and_key(url.split("?")[0]) == ("bricks-evidence", "t/1.jpg")
    assert asyncio.run(storage.remove_from_bucket("job-checklist-proof", ["j/1.jpg"])) is True
    put_bucket, put_key = _bucket_and_key(fake_http[0][1].split("?")[0])
    assert (put_bucket, put_key) == ("bricks-evidence", "job-checklist-proof/j/1.jpg")


def test_backup_writes_r2(r2_env, fake_http, monkeypatch):
    monkeypatch.setattr(ops_autopilot, "dump_database", lambda: (b"gz", {"jobs": 3}))
    res = asyncio.run(ops_autopilot.backup(datetime(2026, 11, 9, tzinfo=timezone.utc)))
    assert res["object"] == "r2:db-backups/2026-11-09.jsonl.gz" and res["rows"] == 3
    put = [c for c in fake_http if c[0] == "PUT"][0]
    assert _bucket_and_key(put[1].split("?")[0]) == ("bricks-backups", "2026-11-09.jsonl.gz")
    assert len([c for c in fake_http if c[0] == "DELETE"]) == 7


def test_supabase_mode_unchanged(monkeypatch):
    monkeypatch.delenv("STORAGE_READ_BACKEND", raising=False)
    for k, v in R2_ENV.items():
        monkeypatch.setenv(k, v)
    assert storage._on_r2("bricks-go/jobs/a.jpg") is False
    assert storage._on_r2("r2:bricks-go/jobs/a.jpg") is True
