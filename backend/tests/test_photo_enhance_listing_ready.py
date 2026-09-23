"""Unit tests for MLS listing-ready stills in photo_enhance_service.

No network — synthetic JPEGs via Pillow only. Sky HTTP is mocked when tested.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from app.services.photo_enhance_service import (
    LISTING_ASPECT,
    LISTING_JPEG_QUALITY,
    LISTING_MAX_LONG_EDGE,
    REASON_CORNER_OVERLAY,
    WATERMARK_STRIP_ENABLED,
    bracket_stem,
    capabilities,
    enhance_bracket,
    enhance_image,
    exposure_bias_from_name,
    format_listing_ready,
    group_assets_by_bracket,
    merge_brackets_mertens,
    replace_sky_opencv,
    sky_region_likely,
    straighten_verticals,
    window_pull_from_brackets,
    _detect_corner_overlays,
    _glass_only_matte,
    _mid_biased_body,
    _sky_mid_bias_restore,
    _sky_region_weight,
    _center_crop_aspect,
    _exif_auto_orient,
    _landscape_content_crop,
    _resize_long_edge,
)


def _jpeg(width: int, height: int, color=(40, 120, 200), orient: int | None = None) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    if orient is not None:
        exif = img.getexif()
        exif[274] = orient
        img.save(buf, format="JPEG", quality=85, exif=exif)
    else:
        img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_constants_mls_band():
    assert LISTING_MAX_LONG_EDGE == 2400
    assert LISTING_ASPECT == (4, 3)
    assert 85 <= LISTING_JPEG_QUALITY <= 90


def test_capabilities_honest_flags():
    caps = capabilities()
    assert caps["virtual_staging"] is False
    assert caps["object_removal"] is False
    assert caps["day_to_dusk"] is False
    assert caps["exif_orient"] is True
    assert caps["sky_replace"] in ("http_sky_service", "opencv_heuristic", "unavailable")
    assert caps["window_pull"] in ("glass_only_bracket", "single_frame_soft")
    assert caps["listing_crop"] == "4:3_landscape_content"


def test_bracket_stem_and_bias():
    assert bracket_stem("living_-2.jpg") == bracket_stem("living_+1.jpg") == bracket_stem("living_0.jpg")
    assert exposure_bias_from_name("living_-2.jpg") == -2.0
    assert exposure_bias_from_name("living_+1.5.jpg") == 1.5
    assert exposure_bias_from_name("kitchen_under.jpg") == -2.0
    groups = group_assets_by_bracket(
        [
            {"filename": "a_-2.jpg", "id": "1"},
            {"filename": "a_0.jpg", "id": "2"},
            {"filename": "a_+2.jpg", "id": "3"},
            {"filename": "b.jpg", "id": "4"},
        ]
    )
    assert len(groups) == 2
    assert len(groups[0]) == 3
    assert len(groups[1]) == 1


def test_resize_and_center_crop():
    wide = Image.new("RGB", (4800, 2400), (1, 2, 3))
    out = _resize_long_edge(wide, 2400)
    assert max(out.size) == 2400
    cropped = _center_crop_aspect(Image.new("RGB", (1600, 900), (1, 2, 3)), (4, 3))
    assert abs((cropped.size[0] / cropped.size[1]) - (4 / 3)) < 0.02


def test_portrait_content_crop_prefers_dense_band():
    # Dark portrait with a bright content band in the upper-middle third.
    img = Image.new("RGB", (900, 1800), (10, 10, 10))
    arr = np.asarray(img).copy()
    arr[500:1100, :, :] = (200, 180, 160)
    img = Image.fromarray(arr, "RGB")
    cropped = _landscape_content_crop(img, (4, 3))
    w, h = cropped.size
    assert w >= h  # landscape
    assert abs((w / h) - (4 / 3)) < 0.05
    # Crop window should overlap the bright band (not the empty floor).
    cy = (cropped.size[1] // 2)  # relative — check mean brightness of result
    assert float(np.asarray(cropped).mean()) > 80


def test_format_listing_ready_caps_and_aspect():
    img = Image.new("RGB", (4000, 3000), (50, 60, 70))
    out = format_listing_ready(img)
    assert max(out.size) <= LISTING_MAX_LONG_EDGE
    assert abs((out.size[0] / out.size[1]) - (4 / 3)) < 0.02


def test_exif_auto_orient_rotates():
    raw = _jpeg(100, 200, orient=6)
    img = Image.open(io.BytesIO(raw))
    oriented = _exif_auto_orient(img)
    assert oriented.size == (200, 100)


def test_enhance_image_listing_ready_jpeg():
    out = enhance_image(_jpeg(3200, 1800))
    assert out[:2] == b"\xff\xd8"
    result = Image.open(io.BytesIO(out))
    assert result.format == "JPEG"
    assert max(result.size) <= LISTING_MAX_LONG_EDGE
    assert abs((result.size[0] / result.size[1]) - (4 / 3)) < 0.02
    assert result.size[0] >= result.size[1]  # landscape


def test_merge_brackets_and_window_pull():
    under = Image.new("RGB", (400, 300), (20, 20, 20))
    mid = Image.new("RGB", (400, 300), (120, 120, 120))
    # Blow out a window region on mid; under holds foliage (chroma + DR)
    m = np.asarray(mid).copy()
    u = np.asarray(under).copy()
    m[50:150, 250:380, :] = 255
    u[50:150, 250:380, :] = (30, 90, 40)
    mid = Image.fromarray(m, "RGB")
    under = Image.fromarray(u, "RGB")
    over = Image.new("RGB", (400, 300), (220, 220, 220))
    fused = merge_brackets_mertens([under, mid, over])
    assert fused.size == (400, 300)
    pulled = window_pull_from_brackets(mid, under, over)
    # Glass should take −EV foliage, not stay blown white
    window_mean = float(np.asarray(pulled)[50:150, 250:380, :].mean())
    assert window_mean < 250


def test_enhance_bracket_multi_frame():
    frames = [_jpeg(800, 600, c) for c in ((20, 20, 20), (100, 100, 100), (220, 220, 220))]
    names = ["room_-2.jpg", "room_0.jpg", "room_+2.jpg"]
    out = enhance_bracket(frames, names, apply_sky=False)
    result = Image.open(io.BytesIO(out))
    assert result.format == "JPEG"
    assert abs((result.size[0] / result.size[1]) - (4 / 3)) < 0.02


def _synthetic_blue_sky_jpeg(width: int = 800, height: int = 600) -> bytes:
    """Exterior-like still: blue upper sky + green/brown ground (no network)."""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    sky_h = height // 2
    # Vertical blue gradient in upper half
    for y in range(sky_h):
        t = y / max(1, sky_h - 1)
        arr[y, :, 0] = int(90 + 40 * t)
        arr[y, :, 1] = int(145 + 30 * t)
        arr[y, :, 2] = int(225 - 15 * t)
    arr[sky_h:, :, :] = (70, 95, 55)  # ground / foliage
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_sky_region_heuristic_and_service_optional():
    blue_sky = Image.new("RGB", (800, 600), (90, 140, 220))
    # Make top band clearly sky-like
    arr = np.asarray(blue_sky).copy()
    arr[:120, :, :] = (100, 160, 230)
    arr[120:, :, :] = (80, 90, 70)
    blue_sky = Image.fromarray(arr, "RGB")
    assert bool(sky_region_likely(blue_sky))

    indoor = Image.new("RGB", (800, 600), (90, 80, 70))
    assert not bool(sky_region_likely(indoor))

    # When SKY_SERVICE_URL is unset, OpenCV fallback still yields listing JPEG.
    out = enhance_image(_jpeg(1200, 800, (100, 150, 220)))
    assert out[:2] == b"\xff\xd8"


def test_opencv_sky_fallback_on_synthetic_blue_sky():
    """In-process sky replace when URL unset — no network, synthetic JPEG."""
    raw = _synthetic_blue_sky_jpeg()
    src = Image.open(io.BytesIO(raw)).convert("RGB")
    assert bool(sky_region_likely(src))

    replaced = replace_sky_opencv(src)
    assert replaced is not None
    assert replaced.size == src.size
    # Upper band should still be blue-ish after replace (procedural sky)
    top = np.asarray(replaced)[: src.size[1] // 5, :, :].astype(np.float32)
    r, g, b = top[:, :, 0].mean(), top[:, :, 1].mean(), top[:, :, 2].mean()
    assert b > r + 5 and b > g
    # Ground band should stay mostly green (not washed into neon sky)
    ground = np.asarray(replaced)[src.size[1] * 2 // 3 :, :, :].astype(np.float32)
    assert ground[:, :, 1].mean() > ground[:, :, 2].mean() - 10

    # Full enhance path (no SKY_SERVICE_URL) still listing-ready
    with patch.dict("os.environ", {"SKY_SERVICE_URL": ""}, clear=False):
        # Force module-level URL empty for this call path via patch on the binding
        with patch("app.services.photo_enhance_service.SKY_SERVICE_URL", ""):
            out = enhance_image(raw)
    result = Image.open(io.BytesIO(out))
    assert result.format == "JPEG"
    assert max(result.size) <= LISTING_MAX_LONG_EDGE
    assert abs((result.size[0] / result.size[1]) - (4 / 3)) < 0.02

    caps = capabilities()
    assert caps["sky_replace"] in ("opencv_heuristic", "http_sky_service")


def test_opencv_sky_skips_indoor():
    indoor = Image.new("RGB", (800, 600), (90, 80, 70))
    assert replace_sky_opencv(indoor) is None


def test_straighten_noop_on_blank():
    blank = Image.new("RGB", (400, 300), (128, 128, 128))
    out = straighten_verticals(blank)
    assert out.size == blank.size


def test_glass_only_matte_rejects_wall_bleed():
    """Blown glass pulls −EV; painted wall next to it stays mid (no grey slab)."""
    mid = np.full((200, 300, 3), 110, dtype=np.uint8)
    under = np.full((200, 300, 3), 40, dtype=np.uint8)
    # Glass pane: blown flat white on mid, foliage-ish under
    mid[40:140, 160:270, :] = 255
    under[40:140, 160:270, :] = (35, 95, 45)
    matte = _glass_only_matte(mid, under)
    glass = float(matte[60:120, 180:250].mean())
    wall = float(matte[60:120, 20:80].mean())
    assert glass > 0.35
    assert wall < 0.05


def test_glass_only_matte_does_not_expand_into_walls_or_mullions():
    """Synthetic bay: glass panes high; wall + white mullion bars stay ~0."""
    h, w = 240, 360
    mid = np.full((h, w, 3), 130, dtype=np.uint8)  # painted wall
    under = np.full((h, w, 3), 45, dtype=np.uint8)
    # Two blown panes with a vertical mullion gap between them
    mid[50:180, 40:150, :] = 255
    mid[50:180, 170:290, :] = 255
    under[50:180, 40:150, :] = (40, 110, 50)
    under[50:180, 170:290, :] = (35, 100, 45)
    # Bright white mullion + wall pillar (must not receive −EV)
    mid[50:180, 150:170, :] = 250  # mullion
    mid[40:190, 20:35, :] = 200  # wall trim / pillar (not blown flat)
    under[50:180, 150:170, :] = (80, 80, 80)
    matte = _glass_only_matte(mid, under)
    pane = float(matte[80:150, 70:130].mean())
    wall = float(matte[80:150, 0:18].mean())
    mullion = float(matte[80:150, 152:168].mean())
    assert pane > 0.30
    assert wall < 0.05, f"wall matte leaked: {wall}"
    assert mullion < 0.12, f"mullion matte leaked: {mullion}"


def test_multi_pane_left_bank_does_not_paint_wall_between_panes():
    """Left-bank multi-pane: −EV on glass only; wall between bank and bay stays 0."""
    h, w = 280, 480
    # Beige interior wall (same chromaticity mid/under — classic wall ghost bait)
    mid = np.zeros((h, w, 3), dtype=np.uint8)
    mid[:, :] = (168, 158, 142)
    under = np.zeros((h, w, 3), dtype=np.uint8)
    under[:, :] = (95, 88, 78)
    # Left bank: 2x2 panes with mullions
    panes = [(50, 120, 40, 120), (50, 120, 140, 220), (140, 210, 40, 120), (140, 210, 140, 220)]
    for y0, y1, x0, x1 in panes:
        mid[y0:y1, x0:x1, :] = 255
        under[y0:y1, x0:x1, :] = (42, 118, 48)
    # Mullions / pillar between panes (white frame)
    mid[50:210, 120:140, :] = 248
    under[50:210, 120:140, :] = (90, 90, 88)
    mid[120:140, 40:220, :] = 248
    under[120:140, 40:220, :] = (90, 90, 88)
    # Wall gap between left bank and bay (must NEVER get −EV)
    mid[40:220, 240:300, :] = (175, 165, 148)
    under[40:220, 240:300, :] = (100, 92, 82)
    # Bay panes (right) — should also pull
    mid[55:200, 320:450, :] = 255
    under[55:200, 320:450, :] = (38, 105, 50)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    left_glass = float(matte[70:110, 60:100].mean())
    bay_glass = float(matte[90:160, 350:420].mean())
    between_wall = float(matte[70:180, 250:290].mean())
    mullion = float(matte[70:110, 124:136].mean())
    assert left_glass > 0.25, f"left bank glass too weak: {left_glass}"
    assert bay_glass > 0.25, f"bay glass too weak: {bay_glass}"
    assert between_wall < 0.02, f"WALL GHOST between bank and bay: {between_wall}"
    assert mullion < 0.12, f"mullion painted: {mullion}"


def test_glass_only_low_conf_leaves_mid_pane():
    """Still-blown −EV (no recoverable detail) → matte ~0, leave mid."""
    mid = np.full((180, 240, 3), 120, dtype=np.uint8)
    under = np.full((180, 240, 3), 50, dtype=np.uint8)
    mid[40:140, 60:180, :] = 255
    under[40:140, 60:180, :] = 250  # still blown, useless
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    assert float(matte[60:120, 80:160].mean()) < 0.05


def test_glass_only_protects_pillar_and_curtain():
    """Pillar + curtain between panes stay mid; only glass receives −EV."""
    h, w = 220, 340
    mid = np.full((h, w, 3), (160, 150, 135), dtype=np.uint8)
    under = np.full((h, w, 3), (90, 82, 72), dtype=np.uint8)
    # Two panes
    mid[40:180, 30:130, :] = 255
    mid[40:180, 200:300, :] = 255
    under[40:180, 30:130, :] = (40, 110, 55)
    under[40:180, 200:300, :] = (35, 100, 50)
    # Pillar (medium bright, painted)
    mid[30:190, 140:175, :] = (190, 180, 165)
    under[30:190, 140:175, :] = (110, 100, 90)
    # Curtain (warm chroma)
    mid[30:190, 175:198, :] = (170, 140, 100)
    under[30:190, 175:198, :] = (100, 80, 55)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    pane = float(matte[70:140, 50:110].mean())
    pillar = float(matte[70:140, 145:170].mean())
    curtain = float(matte[70:140, 178:195].mean())
    assert pane > 0.25, f"pane weak: {pane}"
    assert pillar < 0.05, f"pillar leaked: {pillar}"
    assert curtain < 0.05, f"curtain leaked: {curtain}"




def test_glass_only_rejects_bright_wall_panel_ghost():
    """Bright white wall lit by window must not get −EV (wall ghost hard fail)."""
    h, w = 200, 300
    mid = np.full((h, w, 3), (170, 160, 145), dtype=np.uint8)
    under = np.full((h, w, 3), (95, 88, 78), dtype=np.uint8)
    # Glass pane with novel under foliage edges
    mid[40:160, 40:130, :] = 255
    under[40:160, 40:130, :] = (40, 120, 50)
    # Draw branch-like structure only on under glass
    for i in range(50, 150, 8):
        under[i:i+2, 50:120, :] = (30, 80, 35)
    # Bright white wall panel (same edges mid+under — no novel structure)
    mid[40:160, 160:260, :] = 245
    under[40:160, 160:260, :] = (140, 130, 118)
    # Shared paneling lines
    mid[70:74, 170:250, :] = 220
    under[70:74, 170:250, :] = (120, 110, 100)
    mid[110:114, 170:250, :] = 220
    under[110:114, 170:250, :] = (120, 110, 100)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    glass = float(matte[60:140, 55:115].mean())
    wall = float(matte[60:140, 175:245].mean())
    assert glass > 0.20, f"glass weak: {glass}"
    assert wall < 0.02, f"WALL GHOST on bright panel: {wall}"

def test_mid_biased_body_keeps_bright_sky_on_mid():
    """Upper bright flat sky must not absorb fused mud."""
    mid = Image.new("RGB", (200, 160), (100, 150, 220))
    fused = Image.new("RGB", (200, 160), (180, 160, 90))  # muddy yellow
    m = np.asarray(mid).copy()
    m[:70, :, :] = (240, 245, 250)  # blown sky band
    mid = Image.fromarray(m, "RGB")
    out = _mid_biased_body(mid, fused, fused_mix=0.35)
    top = np.asarray(out)[:50, :, :].astype(np.float32)
    # Stay near mid plate (cool), not fused yellow
    assert top[:, :, 2].mean() > top[:, :, 0].mean()
    assert abs(float(top.mean()) - 245) < 25


def test_sky_region_prefers_mid_on_bright_low_chroma():
    """Bright low-chroma sky: restore toward mid, never keep green-yellow mud."""
    mid = Image.new("RGB", (300, 200), (80, 90, 70))
    m = np.asarray(mid).copy()
    m[:90, :, :] = (235, 240, 248)  # bright cool sky
    mid = Image.fromarray(m, "RGB")
    # Working plate dirtied with green-yellow reconstruct in sky
    dirty = m.copy()
    dirty[:90, :, :] = (200, 210, 120)
    working = Image.fromarray(dirty, "RGB")
    out = _sky_mid_bias_restore(working, mid, under=None)
    top = np.asarray(out)[:70, :, :].astype(np.float32)
    # Prefer mid cool sky (B high) over green-yellow mud
    assert top[:, :, 2].mean() > top[:, :, 1].mean() - 5
    assert float(top.mean()) > 220
    # Weight helper marks bright low-chroma
    w = _sky_region_weight(m.astype(np.float32))
    assert float(w[:70, :].mean()) > 0.2


def test_watermark_strip_default_off_soft_fail_path():
    """Aggressive corner inpaint OFF by default; clean frames stay untouched."""
    assert WATERMARK_STRIP_ENABLED is False
    clean = Image.new("RGB", (800, 600), (120, 120, 120))
    from app.services.photo_enhance_service import _strip_corner_watermarks
    out, stripped, reasons = _strip_corner_watermarks(clean)
    assert stripped is False
    assert reasons == []
    assert out.size == clean.size
    # Fake high-contrast corner glyph on flat margin → detect soft-fail, no inpaint
    arr = np.asarray(clean).copy()
    arr[10:50, 10:70, :] = 20
    arr[18:28, 18:60, :] = 240  # bright bar (logo-like)
    marked = Image.fromarray(arr, "RGB")
    out2, stripped2, reasons2 = _strip_corner_watermarks(marked, apply=False)
    assert stripped2 is False
    # Detection may or may not fire on synthetic; pixels must be unchanged when apply=False
    assert np.array_equal(np.asarray(out2), np.asarray(marked))
    # Opt-in apply still returns tuple of 3
    out3, stripped3, _ = _strip_corner_watermarks(marked, apply=True)
    assert out3.size == marked.size
    assert isinstance(stripped3, bool)



# ---------------------------------------------------------------------------
# Frame PropTx MLS export profile (P1) + people soft-fail (P2)
# ---------------------------------------------------------------------------

from app.services.photo_enhance_service import (
    LISTING_TARGET_MIN_LONG_EDGE,
    LISTING_JPEG_TARGET_MAX_BYTES,
    MLS_EXPORT_PROFILE,
    listing_export_invariants,
    _strip_corner_watermarks,
    _to_srgb_rgb,
)
from app.services.mls_people_detect import (
    FIELD_FACE_COUNT,
    FIELD_MLS_QUARANTINE,
    FIELD_PEOPLE_DETECTED,
    FIELD_SOFT_FAIL,
    FIELD_SOFT_FAIL_REASONS,
    REASON_PERSONS,
    people_soft_fail_metadata,
    merge_people_into_qc,
)
from app.services.qc_opencv import analyze_image_cv


def test_capabilities_proptx_export_profile():
    caps = capabilities()
    assert caps["mls_export_profile"] == MLS_EXPORT_PROFILE
    assert "PropTx" in caps["mls_brief"]
    assert caps["color_space"] == "srgb"
    assert caps["long_edge_band_px"] == [LISTING_TARGET_MIN_LONG_EDGE, LISTING_MAX_LONG_EDGE]
    assert caps["people_soft_fail"] == "yunet_proptx_5_01d"
    assert caps["watermark_strip"] in ("detect_soft_fail", "corner_inpaint_opt_in")
    assert caps["jpeg_target_bytes"][1] == LISTING_JPEG_TARGET_MAX_BYTES


def test_export_invariants_4_3_srgb_band():
    out = enhance_image(_jpeg(4000, 3000, (80, 90, 100)))
    inv = listing_export_invariants(out)
    assert inv["format"] == "JPEG"
    assert inv["landscape"] is True
    assert inv["aspect_ok"] is True
    assert inv["long_edge_ok"] is True
    assert inv["long_edge"] <= LISTING_MAX_LONG_EDGE
    assert inv["mls_export_profile"] == MLS_EXPORT_PROFILE
    # Soft size band: synthetic flats may be small — only enforce upper soft cap
    assert inv["bytes"] <= LISTING_JPEG_TARGET_MAX_BYTES
    assert out[:2] == b"\xff\xd8"


def test_no_upscale_below_target_min():
    """Sources already under 2048 stay as-is on long edge (no upscale mandate)."""
    out = enhance_image(_jpeg(1600, 1200))
    result = Image.open(io.BytesIO(out))
    assert max(result.size) <= 1600 + 2  # crop may trim slightly; never upscale


def test_srgb_flatten_strips_info():
    img = Image.new("RGB", (64, 48), (10, 20, 30))
    img.info["icc_profile"] = b"not-a-real-profile"
    flat = _to_srgb_rgb(img)
    assert flat.mode == "RGB"
    assert not flat.info.get("icc_profile")


def test_watermark_strip_noop_on_clean():
    clean = Image.new("RGB", (800, 600), (120, 120, 120))
    out, stripped, reasons = _strip_corner_watermarks(clean)
    assert stripped is False
    assert reasons == []
    assert out.size == clean.size


def test_enhance_return_meta_soft_fail_fields():
    out, meta = enhance_image(_jpeg(1200, 900, (90, 100, 110)), return_meta=True)
    assert out[:2] == b"\xff\xd8"
    assert meta["mls_export_profile"] == MLS_EXPORT_PROFILE
    assert FIELD_PEOPLE_DETECTED in meta
    assert FIELD_MLS_QUARANTINE in meta
    assert FIELD_SOFT_FAIL in meta
    assert FIELD_SOFT_FAIL_REASONS in meta
    assert FIELD_FACE_COUNT in meta
    # Clean synthetic flat — no faces
    assert meta[FIELD_PEOPLE_DETECTED] is False
    assert meta[FIELD_MLS_QUARANTINE] is False
    assert meta[FIELD_SOFT_FAIL] is False
    assert meta["export"]["aspect_ok"] is True


def test_people_soft_fail_metadata_empty_scene():
    meta = people_soft_fail_metadata(_jpeg(640, 480, (40, 50, 60)))
    assert meta[FIELD_PEOPLE_DETECTED] is False
    assert meta[FIELD_FACE_COUNT] == 0
    assert meta[FIELD_MLS_QUARANTINE] is False
    assert meta[FIELD_SOFT_FAIL] is False
    assert meta[FIELD_SOFT_FAIL_REASONS] == []


def test_people_soft_fail_does_not_hard_reject():
    """merge_people_into_qc flags / quarantines — never status=rejected for people alone."""
    base = {
        "score": 95,
        "status": "passed",
        "source": "opencv",
        "issues": [],
        "metrics": {},
    }
    # Force detected faces via patch
    fake = {
        FIELD_PEOPLE_DETECTED: True,
        FIELD_FACE_COUNT: 2,
        FIELD_MLS_QUARANTINE: True,
        FIELD_SOFT_FAIL: True,
        FIELD_SOFT_FAIL_REASONS: [REASON_PERSONS],
        "people_detector": "test",
        "people_faces": [{"x": 1, "y": 1, "w": 10, "h": 10, "score": 0.9}],
    }
    with patch("app.services.mls_people_detect.people_soft_fail_metadata", return_value=fake):
        from app.services.mls_people_detect import merge_people_into_qc as merge
        # Call merge with patched metadata by patching detect path inside merge
        with patch("app.services.mls_people_detect.people_soft_fail_metadata", return_value=fake):
            out = merge(base, b"fake")
    assert out["status"] == "flagged"
    assert out["status"] != "rejected"
    assert out[FIELD_MLS_QUARANTINE] is True
    assert out[FIELD_PEOPLE_DETECTED] is True
    assert "persons_detected" in out["issues"]
    assert REASON_PERSONS in out[FIELD_SOFT_FAIL_REASONS]
    assert out["score"] <= 85


def test_analyze_image_cv_includes_people_fields():
    result = analyze_image_cv(_jpeg(800, 600, (70, 80, 90)))
    assert FIELD_PEOPLE_DETECTED in result
    assert FIELD_MLS_QUARANTINE in result
    assert FIELD_SOFT_FAIL in result
    # Empty scene must not quarantine
    assert result[FIELD_PEOPLE_DETECTED] is False
    assert result["status"] in ("passed", "flagged", "rejected")


def test_soft_fail_field_names_contract_for_frame_go():
    """Exact field names Frame/GO should read — do not rename without brief update."""
    assert FIELD_PEOPLE_DETECTED == "qc_people_detected"
    assert FIELD_FACE_COUNT == "qc_face_count"
    assert FIELD_MLS_QUARANTINE == "mls_quarantine"
    assert FIELD_SOFT_FAIL == "qc_soft_fail"
    assert FIELD_SOFT_FAIL_REASONS == "qc_soft_fail_reasons"
    assert REASON_PERSONS == "proptx_5_01d_persons"


def test_enhance_meta_wm_strip_false_by_default():
    """Default path never sets used_watermark_strip; may soft-fail on overlays."""
    out, meta = enhance_image(_jpeg(1200, 900, (100, 110, 120)), return_meta=True)
    assert out[:2] == b"\xff\xd8"
    assert meta["used_watermark_strip"] is False
    assert REASON_CORNER_OVERLAY == "possible_corner_overlay"

