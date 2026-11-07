"""Camera RAW as the primary stills upload: decode, pipeline, holds, storage switch."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import os
import struct
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services import post_production as pp
from app.services import raw_decode as rd
from app.services import upload_backend as ub
from app.services.r2_storage import presign_url


def _run(coro):
    return asyncio.run(coro)


def _jpeg(size=(2600, 1950), color=(120, 140, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _fake_dng(preview: bytes, *, bias=(-2, 1), orientation=1) -> bytes:
    """Little-endian TIFF: IFD0 (Make, Model, Orientation, DNGVersion, ExifIFD)
    + Exif IFD (ExposureBiasValue) + an embedded preview JPEG. Not demosaicable,
    so this exercises the no-LibRaw embedded-scan path."""
    make, model = b"Canon\x00", b"Canon EOS R6\x00"
    entries0 = 5
    ifd0 = 8
    ifd0_len = 2 + entries0 * 12 + 4
    exif = ifd0 + ifd0_len
    exif_len = 2 + 1 * 12 + 4
    data_off = exif + exif_len
    make_off = data_off
    model_off = make_off + len(make)
    bias_off = model_off + len(model)
    jpeg_off = bias_off + 8
    out = bytearray(b"II*\x00" + struct.pack("<I", ifd0))
    out += struct.pack("<H", entries0)
    out += struct.pack("<HHII", 0x010F, 2, len(make), make_off)
    out += struct.pack("<HHII", 0x0110, 2, len(model), model_off)
    out += struct.pack("<HHIHH", 0x0112, 3, 1, orientation, 0)
    out += struct.pack("<HHI4B", 0xC612, 1, 4, 1, 4, 0, 0)
    out += struct.pack("<HHII", 0x8769, 4, 1, exif)
    out += struct.pack("<I", 0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x9204, 10, 1, bias_off) + struct.pack("<I", 0)
    out += make + model + struct.pack("<ii", *bias)
    assert len(out) == jpeg_off
    out += preview
    return bytes(out)


# ---------------------------------------------------------------------------
# Detection + decode
# ---------------------------------------------------------------------------

def test_sniff_and_names():
    assert rd.sniff_camera_raw(_fake_dng(_jpeg()))
    assert not rd.sniff_camera_raw(_jpeg())
    assert rd.sniff_camera_raw(b"\x00\x00\x00\x18ftypcrx \x00\x00\x00\x01" + b"\x00" * 32)
    assert rd.sniff_camera_raw(b"FUJIFILMCCD-RAW 0201FF383501" + b"\x00" * 64)
    assert rd.sniff_camera_raw(b"IIRO\x08\x00\x00\x00" + b"\x00" * 32)
    assert rd.sniff_camera_raw(b"IIU\x00\x08\x00\x00\x00" + b"\x00" * 32)
    for ext in ("CR2", "CR3", "NEF", "ARW", "RAF", "ORF", "RW2", "DNG", "PEF", "SRW"):
        assert rd.is_camera_raw_name(f"IMG_1.{ext}")
        assert rd.raw_mime_for(f"IMG_1.{ext}").startswith("image/x-")
    assert not rd.is_camera_raw_name("IMG_1.JPG")
    assert rd.working_jpeg_path("bricks-go/jobs/j/u/IMG_1.CR3") == "bricks-go/jobs/j/u/work_IMG_1.jpg"
    assert rd.preview_jpeg_path("bricks-go/jobs/j/u/IMG_1.CR3") == "bricks-go/jobs/j/u/thumb_IMG_1.jpg"


def test_exif_reader_and_working_jpeg_keep_exposure_bias(monkeypatch):
    raw = _fake_dng(_jpeg(), bias=(-2, 1))
    ex = rd.read_raw_exif(raw)
    assert ex["model"] == "Canon EOS R6"
    assert rd.exposure_bias(ex) == -2.0
    monkeypatch.setattr(rd, "_rawpy", lambda: None)  # no LibRaw → embedded scan
    jpeg, meta = rd.raw_to_working_jpeg(raw)
    assert jpeg[:3] == b"\xff\xd8\xff"
    assert meta["method"] == "embedded_scan"
    assert (meta["width"], meta["height"]) == (2600, 1950)
    from app.services.photo_enhance_service import exposure_bias_from_exif

    assert exposure_bias_from_exif(jpeg) == -2.0


def test_undecodable_raw_raises():
    with pytest.raises(rd.RawDecodeError):
        rd.decode_raw(b"II*\x00\x08\x00\x00\x00" + b"\x00" * 4096)


def test_open_rgb_decodes_raw_bytes(monkeypatch):
    from app.services.image_io import open_rgb

    monkeypatch.setattr(rd, "_rawpy", lambda: None)
    img = open_rgb(_fake_dng(_jpeg((2600, 1950))))
    assert img.size == (2600, 1950)  # not a tiny IFD0 thumbnail


SAMPLES = Path(os.getenv("RAW_SAMPLES_DIR", "/workspace/samples-raw"))


@pytest.mark.skipif(not SAMPLES.is_dir() or not rd.rawpy_available(), reason="sample RAWs / rawpy not on this box")
def test_real_sample_raws_decode():
    seen = set()
    for f in sorted(SAMPLES.iterdir()):
        if not rd.is_camera_raw_name(f.name) or f.stat().st_size > 80 * 1024 * 1024:
            continue
        jpeg, meta = rd.raw_to_working_jpeg(f.read_bytes())
        img = Image.open(io.BytesIO(jpeg))
        assert max(img.size) >= 2400, f.name
        seen.add(f.suffix.lower())
    assert {".cr3", ".nef", ".arw", ".dng"} <= seen


# ---------------------------------------------------------------------------
# Pipeline: RAW edits from the working JPEG; undecodable RAW is held
# ---------------------------------------------------------------------------

def _install(monkeypatch, *, objects):
    updates, stored, enhanced_inputs = [], {}, []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(path):
        return stored.get(path, objects.get(path))

    async def fake_put(path, data, content_type="application/octet-stream", upsert=True):
        stored[path] = data
        return True

    async def fake_up(_storage, _job, uid, _data):
        return f"enhanced/{uid}.jpg"

    async def fake_qc(*_a, **_k):
        return f"qc/{_a[2]}.jpg"

    def enhance_image(b, return_meta=False):
        enhanced_inputs.append(b)
        return (b"\xff\xd8\xffedit", {})

    def enhance_bracket(frames, filenames=None, apply_sky=True, return_meta=False):
        enhanced_inputs.extend(frames)
        return (b"\xff\xd8\xffmerge", {})

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = enhance_bracket
    fake_pes.enhance_image = enhance_image
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else (-2.0 if "-" in n else 2.0)
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (b, {"export": {}})
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    fake_storage.upload_bytes = fake_put
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(pp, "_pano_hold_kind", lambda b: None)
    monkeypatch.setattr(rd, "_rawpy", lambda: None)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    return updates, stored, enhanced_inputs


def test_raw_single_still_edits_working_jpeg_and_keeps_master(monkeypatch):
    raw_path = "bricks-go/jobs/j/u1/IMG_0001.CR3"
    updates, stored, inputs = _install(monkeypatch, objects={raw_path: _fake_dng(_jpeg())})
    asset = {"id": "u1", "file_url": raw_path, "filename": "IMG_0001.CR3", "mime_type": "image/x-canon-cr3"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "j", asset))
    (uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd["processed_url"] == "enhanced/u1.jpg"
    assert inputs and inputs[0][:3] == b"\xff\xd8\xff"  # editor never saw RAW bytes
    assert "bricks-go/jobs/j/u1/work_IMG_0001.jpg" in stored
    assert "bricks-go/jobs/j/u1/thumb_IMG_0001.jpg" in stored
    assert raw_path not in stored  # master untouched


def test_undecodable_raw_is_held_for_review_not_failed(monkeypatch):
    raw_path = "bricks-go/jobs/j/u1/IMG_0001.NEF"
    updates, _, inputs = _install(monkeypatch, objects={raw_path: b"MM\x00*" + b"\x00" * 2048})
    asset = {"id": "u1", "file_url": raw_path, "filename": "IMG_0001.NEF"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "j", asset))
    (uid, upd), = updates
    assert upd["processing_status"] == "done"
    assert upd["provider"] == "original"
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == rd.RAW_REVIEW_QC_NOTE
    assert not inputs


def test_raw_bracket_merges_working_jpegs_and_drops_bad_frame(monkeypatch):
    objs = {
        "p/lounge_-2.CR3": _fake_dng(_jpeg(color=(40, 40, 40)), bias=(-2, 1)),
        "p/lounge_0.CR3": _fake_dng(_jpeg(), bias=(0, 1)),
        "p/lounge_+2.CR3": b"II*\x00" + b"\x00" * 4096,  # corrupt
        "p/lounge_+4.CR3": _fake_dng(_jpeg(color=(230, 230, 230)), bias=(4, 1)),
    }
    updates, _, inputs = _install(monkeypatch, objects=objs)
    group = [
        {"id": f"u{i}", "file_url": p, "filename": p.split("/")[-1]} for i, p in enumerate(objs)
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", group, apply_sky=True))
    by_id = {u: x for u, x in updates}
    assert len(inputs) == 3 and all(b[:3] == b"\xff\xd8\xff" for b in inputs)
    assert by_id["u1"]["provider"] == "bricks_inhouse"  # 0 EV primary
    assert by_id["u2"]["provider"] == "bracket_sibling"  # corrupt frame folds in
    assert by_id["u2"]["processed_url"] == by_id["u1"]["processed_url"]


def test_raw_bracket_all_undecodable_holds_angle(monkeypatch):
    objs = {f"p/hall_{ev}.ARW": b"II*\x00" + b"\x00" * 4096 for ev in ("-2", "0", "+2")}
    updates, _, inputs = _install(monkeypatch, objects=objs)
    group = [{"id": f"u{i}", "file_url": p, "filename": p.split("/")[-1]} for i, p in enumerate(objs)]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", group, apply_sky=True))
    by_id = {u: x for u, x in updates}
    assert by_id["u1"]["qc_notes"] == rd.RAW_REVIEW_QC_NOTE
    assert by_id["u0"]["provider"] == "bracket_sibling"
    assert not inputs


def test_drone_dng_never_delivered_as_raw(monkeypatch):
    raw_path = "bricks-go/jobs/j/u9/DJI_0042.DNG"
    updates, stored, _ = _install(monkeypatch, objects={raw_path: _fake_dng(_jpeg())})
    asset = {"id": "u9", "file_url": raw_path, "filename": "DJI_0042.DNG"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "j", asset))
    (uid, upd), = updates
    assert upd["provider"] == "as_is"
    assert not rd.is_camera_raw_name(upd["processed_url"])


# ---------------------------------------------------------------------------
# Storage switch
# ---------------------------------------------------------------------------

def test_backend_defaults_to_supabase_and_r2_is_config(monkeypatch):
    for k in ("STILLS_UPLOAD_BACKEND", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "UPLOAD_MAX_FILE_MB"):
        monkeypatch.delenv(k, raising=False)
    assert ub.backend_name() == "supabase"
    assert ub.max_file_bytes() == 50 * 1024 * 1024
    monkeypatch.setenv("STILLS_UPLOAD_BACKEND", "r2")
    assert ub.backend_name() == "supabase"  # no keys yet → stays on Supabase
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "AK")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "SK")
    monkeypatch.setenv("R2_BUCKET", "bricks-raw")
    assert ub.backend_name() == "r2"
    assert ub.max_file_bytes() > 1024 * 1024 * 1024
    t = _run(ub.create_direct_upload("bricks-go/jobs/j/u/IMG_1.CR3", "image/x-canon-cr3", 80_000_000))
    assert t["provider"] == "r2" and t["storage_path"] == "r2:bricks-go/jobs/j/u/IMG_1.CR3"
    assert t["signed_url"].startswith("https://acct.r2.cloudflarestorage.com/bricks-raw/bricks-go/jobs/j/u/IMG_1.CR3?")


def test_supabase_tus_endpoint_uses_direct_storage_host(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abcd.supabase.co")
    assert ub._supabase_tus_endpoint() == "https://abcd.storage.supabase.co/storage/v1/upload/resumable/sign"


def test_sigv4_presign_matches_aws_vector():
    url = presign_url(
        "GET", "test.txt", expires=86400, endpoint="https://s3.amazonaws.com", bucket="examplebucket",
        access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1", now=dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc), virtual_host=True,
    )
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
