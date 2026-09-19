"""Fair-Use Vercel Blob fallback for GO capture storage."""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import storage as st


@pytest.fixture(autouse=True)
def _hmac_env(monkeypatch):
    monkeypatch.setenv("CAPTURE_PUT_SECRET", "unit-test-capture-secret-32chars!!")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_6ikFstZosAedDlDQ_testrwtoken")
    monkeypatch.setenv("DISPATCH_PUBLIC_URL", "https://dispatch-api.brxs.ca")
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)


def test_mint_and_verify_hmac_roundtrip():
    path = "capture/sess1/stations/0/pan/0.jpg"
    exp = int(time.time()) + 3600
    sig = st.mint_capture_hmac("PUT", path, exp)
    assert len(sig) == 64
    assert st.verify_capture_hmac("PUT", path, exp, sig) is True
    assert st.verify_capture_hmac("GET", path, exp, sig) is False
    assert st.verify_capture_hmac("PUT", path, exp - 10, sig) is False
    assert st.verify_capture_hmac("PUT", "other/path.jpg", exp, sig) is False


def test_verify_rejects_expired():
    path = "tours/job1/tour.html"
    exp = int(time.time()) - 5
    sig = st.mint_capture_hmac("GET", path, exp)
    assert st.verify_capture_hmac("GET", path, exp, sig) is False


def test_build_hmac_proxy_url_put_shape():
    url = st.build_hmac_proxy_url("PUT", "capture/a/b.jpg", 3600)
    assert url.startswith("https://dispatch-api.brxs.ca/api/capture/blob-put/capture/a/b.jpg?")
    assert "exp=" in url and "sig=" in url


def test_store_id_from_token():
    tok = "vercel_blob_rw_6ikFstZosAedDlDQ_abc"
    assert st._store_id_from_token(tok) == "6ikFstZosAedDlDQ"


def test_upload_bytes_falls_back_when_supabase_raises(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    put_calls = []

    async def fake_blob_put(path, data, content_type="application/octet-stream", upsert=True):
        put_calls.append((path, data, content_type, upsert))
        return True

    monkeypatch.setattr(st, "get_db", fake_get_db)
    monkeypatch.setattr(st, "_blob_put", fake_blob_put)

    ok = asyncio.run(
        st.upload_bytes("capture/x/y.jpg", b"hello", "image/jpeg", upsert=True)
    )
    assert ok is True
    assert put_calls == [("capture/x/y.jpg", b"hello", "image/jpeg", True)]


def test_upload_bytes_falls_back_when_supabase_returns_false_path(monkeypatch):
    """Exception path already covered; also ensure no-token returns False."""
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    async def boom(*_a, **_k):
        raise RuntimeError("storage down")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(st, "get_db", fake_get_db)
    ok = asyncio.run(
        st.upload_bytes("capture/x/y.jpg", b"hello")
    )
    assert ok is False


def test_create_signed_upload_url_blob_fallback(monkeypatch):
    async def boom_db():
        raise RuntimeError("auth 402")

    monkeypatch.setattr(st, "get_db", boom_db)
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")

    result = asyncio.run(
        st.create_signed_upload_url("capture/sid/stations/1/equirect.jpg")
    )
    assert result is not None
    assert result["path"] == "capture/sid/stations/1/equirect.jpg"
    assert result["token"] is None
    assert "/api/capture/blob-put/" in result["signed_url"]
    assert "sig=" in result["signed_url"]


def test_get_signed_download_url_blob_fallback(monkeypatch):
    async def boom_db():
        raise RuntimeError("auth 402")

    monkeypatch.setattr(st, "get_db", boom_db)
    url = asyncio.run(
        st.get_signed_download_url("tours/job/tour.html", expiry=600)
    )
    assert url is not None
    assert "/api/capture/blob-get/" in url
