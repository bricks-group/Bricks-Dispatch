"""Backup destinations (Sentinel C1/C2, 2026-11-15).

C1: R2 backups are pinned to bricks-backups/db/YYYY-MM-DD.jsonl.gz; no env var can
redirect them to a public bucket, and writes/deletes elsewhere are refused.
C2: the disk fallback uses its own bricks-owned 0700 directory and file names and
never prunes the root stopgap's files.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.services import ops_autopilot as oa

ACCT = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def r2_env(monkeypatch):
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT", "R2_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BACKUP_R2_ACCOUNT_ID", ACCT)
    monkeypatch.setenv("BACKUP_R2_ACCESS_KEY_ID", "kid")
    monkeypatch.setenv("BACKUP_R2_SECRET_ACCESS_KEY", "sek")


def test_bucket_env_overrides_are_ignored(r2_env, monkeypatch):
    monkeypatch.setenv("BACKUP_R2_BUCKET", "bricks-images")
    monkeypatch.setenv("R2_BUCKET_BACKUPS", "brxs-deliverables")
    monkeypatch.setenv("R2_BUCKET", "bricks-images")
    cfg = oa._r2_backup_config()
    assert cfg["bucket"] == "bricks-backups"
    url = oa._r2_url(cfg, "PUT", "db/2026-11-15.jsonl.gz")
    assert "/bricks-backups/db/2026-11-15.jsonl.gz?" in url
    assert "bricks-images" not in url and "brxs-deliverables" not in url


@pytest.mark.parametrize("key", ["other/2026-11-15.jsonl.gz", "db/sub/2026-11-15.jsonl.gz",
                                 "db/2026-11-15.jsonl.gz.bak", "2026-11-15.jsonl.gz", "db/../x"])
def test_refuses_keys_outside_db_prefix(r2_env, key):
    cfg = oa._r2_backup_config()
    for method in ("PUT", "DELETE", "HEAD"):
        with pytest.raises(RuntimeError):
            oa._r2_url(cfg, method, key)


def test_refuses_other_bucket_even_if_cfg_tampered(r2_env):
    cfg = {**oa._r2_backup_config(), "bucket": "bricks-images"}
    with pytest.raises(RuntimeError):
        oa._r2_url(cfg, "PUT", "db/2026-11-15.jsonl.gz")
    with pytest.raises(RuntimeError):
        oa._r2_url(cfg, "GET", "", {"list-type": "2", "prefix": "db/"})


def test_list_only_on_db_prefix(r2_env):
    cfg = oa._r2_backup_config()
    assert "prefix=db%2F" in oa._r2_url(cfg, "GET", "", {"list-type": "2", "prefix": "db/"})
    with pytest.raises(RuntimeError):
        oa._r2_url(cfg, "GET", "", {"list-type": "2", "prefix": ""})


class _Resp:
    def __init__(self, status=200, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


class _FakeClient:
    """Records calls; HEAD returns the stored size and MD5 ETag."""
    calls: list = []
    stored: dict = {}
    listing = ""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, url, content=None, headers=None):
        _FakeClient.calls.append(("PUT", url, headers))
        _FakeClient.stored["md5"] = hashlib.md5(content).hexdigest()
        _FakeClient.stored["len"] = len(content)
        return _Resp()

    async def head(self, url):
        _FakeClient.calls.append(("HEAD", url, None))
        return _Resp(headers={"content-length": str(_FakeClient.stored["len"]),
                              "etag": f'"{_FakeClient.stored["md5"]}"'})

    async def get(self, url):
        _FakeClient.calls.append(("GET", url, None))
        return _Resp(text=_FakeClient.listing)

    async def delete(self, url):
        _FakeClient.calls.append(("DELETE", url, None))
        return _Resp(204)


def test_r2_upload_md5_and_prune_scope(r2_env, monkeypatch):
    _FakeClient.calls, _FakeClient.stored = [], {}
    _FakeClient.listing = "".join(f"<Key>{k}</Key>" for k in (
        "db/2026-10-01.jsonl.gz", "db/2026-11-10.jsonl.gz", "db/sub/2026-10-01.jsonl.gz",
        "db/2026-10-01.jsonl.gz.bak", "db/notes.txt"))
    monkeypatch.setattr(oa, "httpx", SimpleNamespace(AsyncClient=_FakeClient))
    res = asyncio.run(oa._backup_to_r2(oa._r2_backup_config(), "2026-11-15.jsonl.gz", b"x" * 100, NOW))
    put = [c for c in _FakeClient.calls if c[0] == "PUT"][0]
    assert "Content-MD5" in put[2]
    assert res["md5"] == hashlib.md5(b"x" * 100).hexdigest()
    assert res["pruned"] == ["db/2026-10-01.jsonl.gz"]
    assert all("/bricks-backups/" in c[1] for c in _FakeClient.calls)


def test_r2_upload_md5_mismatch_raises(r2_env, monkeypatch):
    class Bad(_FakeClient):
        async def head(self, url):
            return _Resp(headers={"content-length": str(_FakeClient.stored["len"]), "etag": '"deadbeef"'})
    _FakeClient.calls, _FakeClient.stored, _FakeClient.listing = [], {}, ""
    monkeypatch.setattr(oa, "httpx", SimpleNamespace(AsyncClient=Bad))
    with pytest.raises(RuntimeError, match="md5 differs"):
        asyncio.run(oa._backup_to_r2(oa._r2_backup_config(), "2026-11-15.jsonl.gz", b"y" * 10, NOW))


def test_disk_fallback_own_dir_names_and_prune(tmp_path, monkeypatch):
    d = tmp_path / "db-backups"
    monkeypatch.setattr(oa, "BACKUP_LOCAL_DIR", str(d))
    d.mkdir(mode=0o700)
    for n in ("dispatch-2026-10-01.jsonl.gz", "2026-10-01.jsonl.gz", "dispatch-2026-10-01.jsonl.gz.bak"):
        (d / n).write_bytes(b"old")
    res = oa._backup_to_disk("2026-11-15.jsonl.gz", b"data", NOW)
    f = d / "dispatch-2026-11-15.jsonl.gz"
    assert res["object"] == f"file://{f}" and f.read_bytes() == b"data"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert res["pruned"] == ["dispatch-2026-10-01.jsonl.gz"]
    assert (d / "2026-10-01.jsonl.gz").exists()  # stopgap-style name untouched
    assert (d / "dispatch-2026-10-01.jsonl.gz.bak").exists()


def test_disk_fallback_creates_0700_dir(tmp_path, monkeypatch):
    d = tmp_path / "new" / "db-backups"
    monkeypatch.setattr(oa, "BACKUP_LOCAL_DIR", str(d))
    oa._backup_to_disk("2026-11-15.jsonl.gz", b"d", NOW)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_disk_fallback_refuses_stopgap_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "BACKUP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(oa, "BACKUP_LOCAL_STOPGAP_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        oa._backup_to_disk("2026-11-15.jsonl.gz", b"d", NOW)


def test_defaults_point_at_separate_dirs():
    assert oa.BACKUP_BUCKET == "bricks-backups" and oa.BACKUP_PREFIX == "db/"
    assert oa.BACKUP_LOCAL_DIR == "/var/lib/bricks/db-backups"
    assert os.path.realpath(oa.BACKUP_LOCAL_DIR) != os.path.realpath(oa.BACKUP_LOCAL_STOPGAP_DIR)


def test_l1_web_push_and_nonce_columns_redacted():
    assert {"endpoint", "p256dh", "auth"} <= oa.backup_redacted_columns(
        "public.pwa_web_push_devices", ["id", "endpoint", "p256dh", "auth", "member_id"])
    assert "nonce_sha256" in oa.backup_redacted_columns("public.calendar_oauth_nonces", ["id", "nonce_sha256"])


@pytest.mark.parametrize("var", ["BACKUP_R2_ENDPOINT", "R2_ENDPOINT"])
def test_endpoint_env_ignored(r2_env, monkeypatch, var):
    monkeypatch.setenv(var, f"https://bricks-images.{ACCT}.r2.cloudflarestorage.com")
    cfg = oa._r2_backup_config()
    assert cfg["endpoint"] == f"https://{ACCT}.r2.cloudflarestorage.com"
    assert oa._r2_url(cfg, "PUT", "db/2026-11-15.jsonl.gz").startswith(
        f"https://{ACCT}.r2.cloudflarestorage.com/bricks-backups/db/")


@pytest.mark.parametrize("ep", [
    f"https://bricks-images.{ACCT}.r2.cloudflarestorage.com",
    f"https://{ACCT}.r2.cloudflarestorage.com/bricks-images",
    f"https://{ACCT}.r2.cloudflarestorage.com/",
    f"http://{ACCT}.r2.cloudflarestorage.com",
    f"https://{ACCT.upper()}.r2.cloudflarestorage.com",
    "https://images.brxs.ca",
])
def test_tampered_endpoint_refused(r2_env, ep):
    cfg = {**oa._r2_backup_config(), "endpoint": ep}
    with pytest.raises(RuntimeError):
        oa._r2_url(cfg, "PUT", "db/2026-11-15.jsonl.gz")


@pytest.mark.parametrize("acct", ["acct123", ACCT.upper(), ACCT + "0", "bricks-images." + ACCT])
def test_bad_account_id_disables_r2(r2_env, monkeypatch, acct):
    monkeypatch.setenv("BACKUP_R2_ACCOUNT_ID", acct)
    assert oa._r2_backup_config() is None


@pytest.mark.parametrize("method", ["PUT", "DELETE", "HEAD"])
def test_empty_key_refused_for_writes(r2_env, method):
    with pytest.raises(RuntimeError):
        oa._r2_url(oa._r2_backup_config(), method, "", {"prefix": "db/"})


def test_unicode_digits_refused(r2_env):
    with pytest.raises(RuntimeError):
        oa._r2_url(oa._r2_backup_config(), "PUT", "db/\u0662\u0660\u0662\u0666-11-15.jsonl.gz")


def test_existing_fallback_dir_is_tightened_to_0700(tmp_path, monkeypatch):
    d = tmp_path / "db-backups"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    monkeypatch.setattr(oa, "BACKUP_LOCAL_DIR", str(d))
    oa._backup_to_disk("2026-11-15.jsonl.gz", b"d", NOW)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_fallback_dir_owned_by_someone_else_refused(tmp_path, monkeypatch):
    d = tmp_path / "db-backups"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(oa, "BACKUP_LOCAL_DIR", str(d))
    monkeypatch.setattr(oa.os, "geteuid", lambda: os.stat(d).st_uid + 1)
    with pytest.raises(RuntimeError, match="not owned"):
        oa._backup_to_disk("2026-11-15.jsonl.gz", b"d", NOW)
