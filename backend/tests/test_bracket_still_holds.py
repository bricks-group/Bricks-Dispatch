"""HDR brackets get the same still holds as single frames.

AEB shooters fire 3-5 frames per press, so a lens-cap / wide-pano / thumbnail
accident arrives as a bracket. It used to be HDR-merged, 4:3-cropped and
delivered; a truncated sibling exposure sank the whole merge to a no-HDR edit.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from app.services import post_production as pp


def _jpeg(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype("uint8"), "RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _scene(w=1600, h=1200, seed=1) -> bytes:
    rng = np.random.default_rng(seed)
    return _jpeg(rng.integers(0, 256, size=(h, w, 3)))


def _flat(level: int, w=1600, h=1200) -> bytes:
    return _jpeg(np.full((h, w, 3), level))


def _truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def _install(monkeypatch, by_path: dict):
    updates, calls = [], {"bracket": 0, "single": 0, "frames": []}

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(path):
        return by_path.get(path)

    async def fake_up(_storage, _job, uid, _data):
        return f"processed/{uid}.jpg"

    async def fake_qc(*_a, **_k):
        return f"qc/x.jpg"

    def bracket(frames, filenames=None, **_k):
        calls["bracket"] += 1
        calls["frames"] = list(filenames or [])
        return b"\xff\xd8merged", {}

    def single(*_a, **_k):
        calls["single"] += 1
        return b"\xff\xd8single", {}

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = bracket
    fake_pes.enhance_image = single
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else (-2.0 if "-" in n else 2.0)
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (b, {"export": {}})
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    return updates, calls


GROUP = [
    {"id": "u1", "file_url": "p1", "filename": "lounge_-2.jpg"},
    {"id": "u2", "file_url": "p2", "filename": "lounge_0.jpg"},
    {"id": "u3", "file_url": "p3", "filename": "lounge_+2.jpg"},
]


def _run(by_path, monkeypatch):
    updates, calls = _install(monkeypatch, by_path)
    asyncio.run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-bh", [dict(a) for a in GROUP], apply_sky=True
    ))
    by_id = {}
    for uid, upd in updates:
        by_id.setdefault(uid, {}).update(upd)
    return by_id, calls


def _assert_held(by_id, calls, note):
    assert calls["bracket"] == 0 and calls["single"] == 0
    assert by_id["u2"]["qc_status"] == "rejected"
    assert by_id["u2"]["qc_notes"] == note
    assert by_id["u2"]["processed_url"] == "p2"
    assert by_id["u2"]["provider"] == "as_is"
    for sib in ("u1", "u3"):
        assert by_id[sib]["provider"] == "bracket_sibling"
        assert by_id[sib]["processed_url"] == "p2"


def test_lens_cap_bracket_held_not_merged(monkeypatch):
    by_id, calls = _run({"p1": _flat(0), "p2": _flat(1), "p3": _flat(3)}, monkeypatch)
    _assert_held(by_id, calls, pp.BLANK_QC_NOTE)


def test_wide_pano_bracket_held_uncropped(monkeypatch):
    pano = {p: _scene(3000, 1000, seed=i) for i, p in enumerate(("p1", "p2", "p3"))}
    by_id, calls = _run(pano, monkeypatch)
    _assert_held(by_id, calls, pp.WIDE_PANO_QC_NOTE)


def test_thumbnail_bracket_held(monkeypatch):
    thumbs = {p: _scene(800, 600, seed=i) for i, p in enumerate(("p1", "p2", "p3"))}
    by_id, calls = _run(thumbs, monkeypatch)
    _assert_held(by_id, calls, pp.LOW_RES_QC_NOTE)


def test_truncated_sibling_dropped_rest_still_merged(monkeypatch):
    by_id, calls = _run(
        {"p1": _scene(seed=1), "p2": _scene(seed=2), "p3": _truncated(_scene(seed=3))},
        monkeypatch,
    )
    assert calls["bracket"] == 1
    assert calls["frames"] == ["lounge_-2.jpg", "lounge_0.jpg"]
    assert by_id["u2"]["provider"] == "bricks_inhouse"
    assert by_id["u2"]["processed_url"] == "processed/u2.jpg"
    assert by_id["u3"]["provider"] == "bracket_sibling"
    assert by_id["u3"]["processed_url"] == "processed/u2.jpg"


def test_normal_bracket_still_merged(monkeypatch):
    by_id, calls = _run(
        {"p1": _scene(seed=1), "p2": _scene(seed=2), "p3": _scene(seed=3)}, monkeypatch
    )
    assert calls["bracket"] == 1 and len(calls["frames"]) == 3
    assert by_id["u2"]["provider"] == "bricks_inhouse"
    assert "qc_status" not in by_id["u2"]
