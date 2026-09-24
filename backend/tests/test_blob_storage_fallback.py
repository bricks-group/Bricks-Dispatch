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


def test_download_bytes_falls_back_when_supabase_raises(monkeypatch):
    async def boom_db():
        raise RuntimeError("exceed_storage_size_quota")

    got = []

    async def fake_blob_get(path):
        got.append(path)
        return b"from-blob"

    monkeypatch.setattr(st, "get_db", boom_db)
    monkeypatch.setattr(st, "_blob_get", fake_blob_get)

    data = asyncio.run(st.download_bytes("capture/sid/stations/0/equirect.jpg"))
    assert data == b"from-blob"
    assert got == ["capture/sid/stations/0/equirect.jpg"]


def _tiny_jpeg_bytes():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(40, 80, 120)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_upload_file_falls_back_to_blob_when_supabase_raises(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    put_calls = []

    async def fake_blob_put(path, data, content_type="application/octet-stream", upsert=True):
        put_calls.append((path, len(data), content_type, upsert))
        return True

    monkeypatch.setattr(st, "get_db", fake_get_db)
    monkeypatch.setattr(st, "_blob_put", fake_blob_put)

    jpeg = _tiny_jpeg_bytes()
    job_id, upload_id = "job-abc", "up-xyz"
    file_path, thumb_path, thumb_b64, qc_path = asyncio.run(
        st.upload_file(jpeg, job_id, upload_id, "still.jpg", "image/jpeg")
    )

    expected_prefix = f"bricks-go/jobs/{job_id}/{upload_id}/"
    assert file_path is not None
    assert file_path.startswith(expected_prefix)
    assert file_path.endswith("still.jpg")
    assert thumb_b64
    assert put_calls, "expected at least one _blob_put for main file"
    assert put_calls[0][0] == file_path
    assert put_calls[0][2] == "image/jpeg"
    # Prefer thumb + qc also landed on Blob for images
    put_paths = [c[0] for c in put_calls]
    assert file_path in put_paths
    assert thumb_path in put_paths
    assert qc_path in put_paths


def test_upload_file_returns_none_without_blob_token(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(st, "get_db", fake_get_db)

    jpeg = _tiny_jpeg_bytes()
    file_path, thumb_path, thumb_b64, qc_path = asyncio.run(
        st.upload_file(jpeg, "job-1", "up-1", "still.jpg", "image/jpeg")
    )
    assert file_path is None
    assert thumb_path is None
    assert qc_path is None
    assert thumb_b64  # best-effort preview still produced

def test_upload_enhanced_falls_back_to_blob_when_supabase_raises(monkeypatch):
    """MLS desk write must land on Blob under Storage 402 — not hollow to original only."""
    from app.services import photo_enhance_service as pe

    put_calls = []

    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    async def fake_blob_put(path, data, content_type="application/octet-stream", upsert=True):
        put_calls.append((path, data, content_type, upsert))
        return True

    monkeypatch.setattr(st, "get_db", fake_get_db)
    monkeypatch.setattr(st, "_blob_put", fake_blob_put)

    jpeg = _tiny_jpeg_bytes()
    path = asyncio.run(pe.upload_enhanced(None, "job-enh-1", "asset-9", jpeg))
    assert path == "enhanced/job-enh-1/asset-9.jpg"
    assert put_calls and put_calls[0][0] == path
    assert put_calls[0][2] == "image/jpeg"


def test_upload_enhanced_raises_when_supabase_and_blob_fail(monkeypatch):
    from app.services import photo_enhance_service as pe

    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    monkeypatch.setattr(st, "get_db", fake_get_db)

    with pytest.raises(RuntimeError, match="Blob fallback both unavailable"):
        asyncio.run(pe.upload_enhanced(None, "job-x", "a1", _tiny_jpeg_bytes()))


def test_upload_qc_variant_falls_back_to_blob(monkeypatch):
    from app.services import photo_enhance_service as pe

    put_calls = []

    async def boom(*_a, **_k):
        raise RuntimeError("exceed_storage_size_quota")

    fake_db = MagicMock()
    fake_db.storage.from_.return_value.upload = boom

    async def fake_get_db():
        return fake_db

    async def fake_blob_put(path, data, content_type="application/octet-stream", upsert=True):
        put_calls.append((path, data, content_type, upsert))
        return True

    monkeypatch.setattr(st, "get_db", fake_get_db)
    monkeypatch.setattr(st, "_blob_put", fake_blob_put)

    path = asyncio.run(pe.upload_qc_variant(None, "job-enh-1", "asset-9", _tiny_jpeg_bytes()))
    assert path == "enhanced/job-enh-1/qc_asset-9.jpg"
    assert put_calls and put_calls[0][0] == path
