"""Leftover stills provider: POSTPROD_STILLS_PROVIDER gate; AE default; fail-fast to inhouse."""
from __future__ import annotations

from pathlib import Path

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import post_production as pp
from app.services import autoenhance_service as ae


def _run(coro):
    return asyncio.run(coro)


def test_process_job_assets_never_async_ae_for_vendor_photo():
    """Launch path: vendor_photo must go leftover (sync HDR), not async AE v2."""
    src = Path(pp.__file__).read_text(encoding="utf-8")
    # Guard against regressing to elif AUTOENHANCE_API_KEY: trigger_autoenhance_edit
    assert "trigger_autoenhance_edit(provider_url)" not in src
    assert "leftover.append(asset)" in src
    assert "sync leftover enhance" in src or "Do NOT submit vendor_photo to async" in src



def test_bracket_group_prefers_autoenhance_then_uploads(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(*_a, **_k):
        return "processed/path.jpg"

    async def fake_qc(*_a, **_k):
        return "qc/path.jpg"

    uploaded = []

    async def fake_up_track(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/path.jpg"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("inhouse must not run when AE succeeds")
    )
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else -2.0
    fake_pes.upload_enhanced = fake_up_track
    fake_pes.upload_qc_variant = fake_qc
    # AE full-res must pass PropTx listing export before upload
    fake_pes.apply_listing_export = lambda b: (
        b"\xff\xd8mls",
        {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800, "aspect_ok": True, "long_edge_ok": True},
            "mls_export_profile": "frame_proptx_mls_stills_v1",
        },
    )
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(
        ae,
        "enhance_brackets_sync",
        lambda *a, **k: (b"\xff\xd8ae_raw_oversized", {"order_id": "ord-1", "provider": "autoenhance"}),
    )

    group = [
        {"id": "u1", "file_url": "p1", "filename": "room_-2.jpg"},
        {"id": "u2", "file_url": "p2", "filename": "room_0.jpg"},
        {"id": "u3", "file_url": "p3", "filename": "room_+2.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-x", group, apply_sky=True))
    providers = {uid: u["provider"] for uid, u in updates}
    assert providers["u2"] == "autoenhance"  # primary ~0 EV
    assert providers["u1"] == "bracket_sibling"
    assert providers["u3"] == "bracket_sibling"
    assert uploaded and uploaded[0] == b"\xff\xd8mls"  # listing-export bytes, not raw AE


def test_bracket_group_falls_back_inhouse_on_no_plan(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(*_a, **_k):
        return "processed/path.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(*_a, **_k):
        return b"\xff\xd8inhouse", {"provider": "bricks_inhouse"}

    uploaded = []

    async def fake_up_track(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/path.jpg"

    export_calls = []

    def fake_listing_export(data):
        export_calls.append(data)
        assert data == b"\xff\xd8inhouse"
        return b"\xff\xd8mls_ih", {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800, "aspect_ok": True, "long_edge_ok": True},
        }

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance
    fake_pes.exposure_bias_from_name = lambda n: 0.0
    fake_pes.upload_enhanced = fake_up_track
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = fake_listing_export
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def boom(*_a, **_k):
        raise ae.AutoenhanceNoPlan("No Plan")

    monkeypatch.setattr(ae, "enhance_brackets_sync", boom)

    group = [
        {"id": "a", "file_url": "p1", "filename": "x_-1.jpg"},
        {"id": "b", "file_url": "p2", "filename": "x_0.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-y", group, apply_sky=True))
    assert any(u.get("provider") == "bricks_inhouse" for _, u in updates)
    assert export_calls == [b"\xff\xd8inhouse"]
    assert uploaded and uploaded[0] == b"\xff\xd8mls_ih"


def test_autoenhance_no_plan_marks_worker_blocked(monkeypatch):
    ae._plan_blocked = False
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)

    class FakeResp:
        status_code = 402
        headers = {"Content-Type": "application/json"}
        content = b'{"message":"No Plan"}'
        text = '{"message":"No Plan"}'

        def json(self):
            return {"message": "No Plan", "detail": "The user has no plan"}

    class FakeClient:
        def get(self, *a, **k):
            return FakeResp()

    with pytest.raises(ae.AutoenhanceNoPlan):
        ae._download_enhanced(FakeClient(), "k" * 36, "img-1")
    assert ae._plan_blocked is True
    # reset for other tests
    ae._plan_blocked = False


def test_apply_listing_export_fixes_ae_3x2_oversized():
    """Live Fuze AE leftover was 2800×1870 — must become 4:3 ≤2400 long edge."""
    from io import BytesIO

    from PIL import Image

    from app.services.photo_enhance_service import (
        LISTING_MAX_LONG_EDGE,
        apply_listing_export,
        listing_export_invariants,
    )

    img = Image.new("RGB", (2800, 1870), (120, 130, 140))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    raw = buf.getvalue()
    out, meta = apply_listing_export(raw)
    inv = listing_export_invariants(out)
    assert meta.get("listing_export_applied") is True
    assert inv["aspect_ok"] is True
    assert inv["long_edge_ok"] is True
    assert inv["long_edge"] <= LISTING_MAX_LONG_EDGE
    assert inv["landscape"] is True
    # 2800×1870 center-cropped to 4:3 → 2493×1870 then scaled to 2400×1800
    assert inv["width"] == 2400
    assert inv["height"] == 1800


def test_deliver_bracket_source_calls_listing_export_after_ae():
    src = Path(pp.__file__).read_text(encoding="utf-8")
    assert "apply_listing_export(enhanced)" in src
    assert "listing_export_applied" in Path(
        __file__
    ).read_text(encoding="utf-8") or "apply_listing_export" in src


def test_enhance_brackets_sync_accepts_single_frame(monkeypatch):
    """Single still leftovers: AE accepts 1+ frames (number_of_brackets_per_image=1)."""
    ae._plan_blocked = False
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)

    calls = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            if url.endswith("/process"):
                calls["process_body"] = k.get("json") or {}

            class R:
                status_code = 201
                text = "{}"

                def json(self):
                    if url.rstrip("/").endswith("/orders"):
                        return {"order_id": "ord-single"}
                    if "/brackets" in url:
                        return {"upload_url": "https://upload.example/put"}
                    return {}

            return R()

        def put(self, *a, **k):
            class R:
                status_code = 200
                text = ""

            return R()

        def get(self, url, **k):
            class R:
                status_code = 200
                headers = {"Content-Type": "image/jpeg"}
                content = b"\xff\xd8ae_single"
                text = ""

                def json(self):
                    if "/orders/" in url and "/enhanced" not in url:
                        return {
                            "is_merging": False,
                            "is_processing": False,
                            "images": [{"image_id": "img-1", "status": "processed"}],
                        }
                    return {}

            # download_enhanced path
            if "/enhanced" in url:
                class R2:
                    status_code = 200
                    headers = {"Content-Type": "image/jpeg"}
                    content = b"\xff\xd8ae_single"
                    text = ""

                    def json(self):
                        return {}

                return R2()
            return R()

    import httpx

    monkeypatch.setattr(httpx, "Client", lambda **k: FakeClient())
    monkeypatch.setattr(ae, "POLL_INTERVAL_SEC", 0)
    jpeg, meta = ae.enhance_brackets_sync([b"\xff\xd8frame"], ["room.jpg"], apply_sky=False)
    assert jpeg.startswith(b"\xff\xd8")
    assert meta["n_brackets"] == 1
    assert meta["sky_replacement"] is False
    assert calls["process_body"]["number_of_brackets_per_image"] == 1
    assert calls["process_body"]["sky_replacement"] is False
    ae._plan_blocked = False


def test_enhance_brackets_sync_rejects_empty_and_sky(monkeypatch):
    ae._plan_blocked = False
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)
    with pytest.raises(ae.AutoenhanceError, match="sky_replacement forbidden"):
        ae.enhance_brackets_sync([b"a", b"b"], apply_sky=True)
    with pytest.raises(ae.AutoenhanceError, match="need at least 1"):
        ae.enhance_brackets_sync([], apply_sky=False)


def test_deliver_single_vendor_photo_prefers_autoenhance(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    uploaded = []

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/single.jpg"

    async def fake_qc(*_a, **_k):
        return None

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("inhouse must not run when AE succeeds")
    )
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (
        b"\xff\xd8mls_single",
        {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800, "aspect_ok": True, "long_edge_ok": True},
            "mls_export_profile": "frame_proptx_mls_stills_v1",
        },
    )
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    fake_mi = ModuleType("app.services.media_intake")
    fake_mi.delivery_route = lambda _a: "vendor_photo"
    monkeypatch.setitem(sys.modules, "app.services.media_intake", fake_mi)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(
        ae,
        "enhance_brackets_sync",
        lambda frames, filenames=None, **k: (
            b"\xff\xd8ae_raw",
            {"order_id": "ord-1f", "provider": "autoenhance", "n_brackets": len(frames)},
        ),
    )

    asset = {"id": "u-single", "file_url": "raw/p.jpg", "filename": "kitchen.jpg"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-1f", asset))
    assert updates and updates[0][1]["provider"] == "autoenhance"
    assert uploaded and uploaded[0] == b"\xff\xd8mls_single"


def test_deliver_single_vendor_photo_falls_back_inhouse(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(*_a, **_k):
        return "processed/ih.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(original, return_meta=False):
        meta = {"provider": "bricks_inhouse", "mls_quarantine": False}
        return (b"\xff\xd8inhouse", meta) if return_meta else b"\xff\xd8inhouse"

    uploaded = []

    async def fake_up_track(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/ih.jpg"

    export_calls = []

    def fake_listing_export(data):
        export_calls.append(data)
        assert data == b"\xff\xd8inhouse"
        return b"\xff\xd8mls_ih", {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800, "aspect_ok": True, "long_edge_ok": True},
        }

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = fake_enhance
    fake_pes.upload_enhanced = fake_up_track
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = fake_listing_export
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    fake_mi = ModuleType("app.services.media_intake")
    fake_mi.delivery_route = lambda _a: "vendor_photo"
    monkeypatch.setitem(sys.modules, "app.services.media_intake", fake_mi)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def boom(*_a, **_k):
        raise ae.AutoenhanceNoPlan("No Plan")

    monkeypatch.setattr(ae, "enhance_brackets_sync", boom)

    asset = {"id": "u-fb", "file_url": "raw/p.jpg", "filename": "room.jpg"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-fb", asset))
    assert any(u.get("provider") == "bricks_inhouse" for _, u in updates)
    assert export_calls == [b"\xff\xd8inhouse"]
    assert uploaded and uploaded[0] == b"\xff\xd8mls_ih"


def test_as_is_heic_converts_to_jpeg(monkeypatch):
    """as_is HEIC originals become sRGB JPEG for listing; provider stays as_is."""
    updates = []
    fixture = Path(__file__).resolve().parent / "fixtures" / "heic-smoke.heic"
    if not fixture.is_file():
        pytest.skip(f"missing HEIC fixture: {fixture}")
    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        pytest.skip("pillow-heif not installed")

    heic_bytes = fixture.read_bytes()

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return heic_bytes

    uploaded = []

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "enhanced/job/u-heic.jpg"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.upload_enhanced = fake_up
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    # Use real media_intake for as_is routing (drone/aerial or explicit)
    from app.services import media_intake as mi

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)

    asset = {
        "id": "u-heic",
        "file_url": "raw/staging_room.heic",
        "filename": "staging_room.heic",
        "mime_type": "image/heic",
        "media_type": "staging",
    }
    # Force staging route
    monkeypatch.setattr(mi, "delivery_route", lambda _a: "staging")
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-heic", asset))
    assert updates
    assert updates[0][1]["provider"] == "as_is"
    assert updates[0][1]["processed_url"] == "enhanced/job/u-heic.jpg"
    assert uploaded and uploaded[0][:2] == b"\xff\xd8"


def test_as_is_heic_decode_failure_keeps_original(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"not-a-real-heic"

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    from app.services import media_intake as mi

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(mi, "delivery_route", lambda _a: "as_is")
    # infer helpers used inside as_is — keep real media_intake functions
    asset = {
        "id": "u-bad",
        "file_url": "raw/drone.heic",
        "filename": "aerial_yard.heic",
        "mime_type": "image/heic",
    }
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-bad", asset))
    assert updates
    assert updates[0][1]["provider"] == "as_is"
    assert updates[0][1]["processed_url"] == "raw/drone.heic"


def test_as_is_pdf_untouched_no_heic_convert(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        raise AssertionError("PDF must not download for HEIC convert")

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    from app.services import media_intake as mi

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(mi, "delivery_route", lambda _a: "as_is")
    asset = {
        "id": "u-pdf",
        "file_url": "raw/plan.pdf",
        "filename": "floorplan_cubicasa.pdf",
        "mime_type": "application/pdf",
    }
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-pdf", asset))
    assert updates[0][1]["processed_url"] == "raw/plan.pdf"
    assert updates[0][1]["provider"] == "as_is"


def test_deliver_single_vendor_photo_inhouse_listing_export_soft_fail(monkeypatch):
    """Inhouse fallback: listing_export throw keeps enhanced bytes (not original)."""
    updates = []
    uploaded = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/ih.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(original, return_meta=False):
        meta = {"provider": "bricks_inhouse", "mls_quarantine": False}
        return (b"\xff\xd8inhouse", meta) if return_meta else b"\xff\xd8inhouse"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = fake_enhance
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (_ for _ in ()).throw(RuntimeError("export boom"))
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    fake_mi = ModuleType("app.services.media_intake")
    fake_mi.delivery_route = lambda _a: "vendor_photo"
    monkeypatch.setitem(sys.modules, "app.services.media_intake", fake_mi)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(ae, "is_available", lambda: False)

    asset = {"id": "u-sf", "file_url": "raw/p.jpg", "filename": "room.jpg"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-sf", asset))
    assert updates and updates[0][1]["provider"] == "bricks_inhouse"
    assert uploaded == [b"\xff\xd8inhouse"]


def test_stills_provider_preference_defaults_inhouse(monkeypatch):
    # Launch review 2026-11-07: paid Autoenhance only when explicitly configured.
    monkeypatch.delenv("POSTPROD_STILLS_PROVIDER", raising=False)
    assert pp.stills_provider_preference() == "inhouse"
    assert pp.prefer_autoenhance_stills() is False
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "")
    assert pp.stills_provider_preference() == "inhouse"
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    assert pp.stills_provider_preference() == "autoenhance"
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "ae")
    assert pp.stills_provider_preference() == "autoenhance"
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    assert pp.stills_provider_preference() == "inhouse"
    assert pp.prefer_autoenhance_stills() is False
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "bricks_inhouse")
    assert pp.stills_provider_preference() == "inhouse"
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "nope")
    assert pp.stills_provider_preference() == "inhouse"


def test_bracket_group_inhouse_env_skips_autoenhance(monkeypatch):
    """POSTPROD_STILLS_PROVIDER=inhouse (kill switch) must not call AE even if available."""
    updates = []
    ae_calls = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_qc(*_a, **_k):
        return None

    uploaded = []

    async def fake_up_track(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/path.jpg"

    def fake_enhance(*_a, **_k):
        return b"\xff\xd8inhouse", {"provider": "bricks_inhouse"}

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else -2.0
    fake_pes.upload_enhanced = fake_up_track
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (
        b"\xff\xd8mls_ih",
        {"listing_export_applied": True, "export": {"width": 2400, "height": 1800}},
    )
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def track_ae(*a, **k):
        ae_calls.append(1)
        raise AssertionError("AE must not run when provider defaults to inhouse")

    monkeypatch.setattr(ae, "enhance_brackets_sync", track_ae)

    group = [
        {"id": "u1", "file_url": "p1", "filename": "room_-2.jpg"},
        {"id": "u2", "file_url": "p2", "filename": "room_0.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-ih", group, apply_sky=True))
    assert ae_calls == []
    assert any(u.get("provider") == "bricks_inhouse" for _, u in updates)
    assert uploaded and uploaded[0] == b"\xff\xd8mls_ih"


def test_deliver_single_inhouse_env_skips_autoenhance(monkeypatch):
    updates = []
    ae_calls = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    uploaded = []

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/ih.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(original, return_meta=False):
        meta = {"provider": "bricks_inhouse", "mls_quarantine": False}
        return (b"\xff\xd8inhouse", meta) if return_meta else b"\xff\xd8inhouse"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = fake_enhance
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (
        b"\xff\xd8mls_ih",
        {"listing_export_applied": True, "export": {"width": 2400, "height": 1800}},
    )
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    fake_mi = ModuleType("app.services.media_intake")
    fake_mi.delivery_route = lambda _a: "vendor_photo"
    monkeypatch.setitem(sys.modules, "app.services.media_intake", fake_mi)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def track_ae(*a, **k):
        ae_calls.append(1)
        raise AssertionError("AE must not run on default inhouse")

    monkeypatch.setattr(ae, "enhance_brackets_sync", track_ae)

    asset = {"id": "u-def", "file_url": "raw/p.jpg", "filename": "kitchen.jpg"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-def", asset))
    assert ae_calls == []
    assert updates and updates[0][1]["provider"] == "bricks_inhouse"
    assert uploaded and uploaded[0] == b"\xff\xd8mls_ih"


def test_deliver_single_vendor_photo_ae_listing_export_soft_fail_keeps_ae(monkeypatch):
    """AE single-frame: listing_export throw keeps AE bytes (not inhouse fallback)."""
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    updates = []
    uploaded = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/ae.jpg"

    async def fake_qc(*_a, **_k):
        return None

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("inhouse must not run when AE succeeded — soft-fail keeps AE")
    )
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (_ for _ in ()).throw(RuntimeError("export boom"))
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    fake_mi = ModuleType("app.services.media_intake")
    fake_mi.delivery_route = lambda _a: "vendor_photo"
    monkeypatch.setitem(sys.modules, "app.services.media_intake", fake_mi)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(
        ae,
        "enhance_brackets_sync",
        lambda *a, **k: (b"\xff\xd8ae_raw", {"order_id": "ord-sf", "provider": "autoenhance"}),
    )

    # Exterior-tagged single leftover (filename cue) — still vendor_photo route
    asset = {"id": "u-ae-sf", "file_url": "raw/exterior_front.jpg", "filename": "exterior_front.jpg"}
    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-ae-sf", asset))
    assert updates and updates[0][1]["provider"] == "autoenhance"
    assert uploaded == [b"\xff\xd8ae_raw"]


def test_bracket_group_ae_listing_export_soft_fail_keeps_ae(monkeypatch):
    """AE HDR bracket: listing_export throw keeps AE bytes (not inhouse fallback)."""
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "autoenhance")
    updates = []
    uploaded = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/ae_hdr.jpg"

    async def fake_qc(*_a, **_k):
        return None

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("inhouse must not run when AE succeeded — soft-fail keeps AE")
    )
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else -2.0
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (_ for _ in ()).throw(RuntimeError("export boom"))
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(
        ae,
        "enhance_brackets_sync",
        lambda *a, **k: (b"\xff\xd8ae_hdr", {"order_id": "ord-hdr", "provider": "autoenhance"}),
    )

    group = [
        {"id": "u1", "file_url": "p1", "filename": "ext_-2.jpg"},
        {"id": "u2", "file_url": "p2", "filename": "ext_0.jpg"},
        {"id": "u3", "file_url": "p3", "filename": "ext_+2.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-ae-hdr", group, apply_sky=True))
    providers = {uid: u["provider"] for uid, u in updates}
    assert providers["u2"] == "autoenhance"
    assert providers["u1"] == "bracket_sibling"
    assert uploaded == [b"\xff\xd8ae_hdr"]


def test_apply_listing_export_exterior_portrait_and_3x2_aspect_ok():
    """Exterior portrait + 3:2 singles must leave aspect_ok after PropTx export."""
    from io import BytesIO

    from PIL import Image

    from app.services.photo_enhance_service import (
        LISTING_MAX_LONG_EDGE,
        apply_listing_export,
        listing_export_invariants,
    )

    for w, h, label in ((1800, 2700, "portrait"), (2800, 1870, "3:2"), (3000, 2000, "3:2-big")):
        img = Image.new("RGB", (w, h), (90, 140, 200))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        out, meta = apply_listing_export(buf.getvalue())
        inv = listing_export_invariants(out)
        assert inv["aspect_ok"] is True, f"{label} aspect_ok"
        assert inv["long_edge_ok"] is True, f"{label} long_ok"
        assert inv["landscape"] is True, f"{label} landscape"
        assert inv["long_edge"] <= LISTING_MAX_LONG_EDGE
        assert meta.get("listing_export_applied") is True
