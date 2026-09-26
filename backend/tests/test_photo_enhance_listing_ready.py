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
    merge_exposure_fusion,
    _body_plates_for_fuse,
    _interior_body_lift,
    _body_luma_mean,
    replace_sky_opencv,
    sky_region_likely,
    straighten_verticals,
    window_pull_from_brackets,
    _pick_under_mid_over,
    _detect_corner_overlays,
    _glass_only_matte,
    _frame_edge_lock_mask,
    _mid_biased_body,
    _sky_mid_bias_restore,
    _highlight_cap_vs_mid,
    _neutralize_magenta_highlights,
    _body_beat_mid,
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




def test_white_frame_pixels_near_panes_stay_mid_after_pull():
    """Frame v6: mid-white mullions/pillars near panes must not dirty ~70 luma."""
    from PIL import Image

    h, w = 220, 360
    mid = np.full((h, w, 3), 140, dtype=np.uint8)
    under = np.full((h, w, 3), 50, dtype=np.uint8)
    # Two blown panes with recoverable foliage under
    mid[40:180, 40:150, :] = 255
    mid[40:180, 190:300, :] = 255
    under[40:180, 40:150, :] = (40, 115, 50)
    under[40:180, 190:300, :] = (35, 105, 45)
    for i in range(50, 170, 10):
        under[i : i + 2, 55:140, :] = (30, 80, 35)
        under[i : i + 2, 205:290, :] = (28, 75, 32)
    # Bright white mullion between panes (must stay mid after pull)
    mid[40:180, 150:190, :] = 250
    under[40:180, 150:190, :] = (95, 94, 92)

    mid_img = Image.fromarray(mid, "RGB")
    und_img = Image.fromarray(under, "RGB")
    pulled = window_pull_from_brackets(mid_img, und_img, None, reference_mid=mid_img)
    p = np.asarray(pulled, dtype=np.float32)
    m = mid.astype(np.float32)
    mullion = (slice(60, 160), slice(155, 185))
    mid_luma = m[mullion].mean(axis=2)
    desk_luma = p[mullion].mean(axis=2)
    drop = mid_luma - desk_luma
    # Almost no mullion pixels should darken by ~70
    dirty = drop >= 70.0
    dirty_ratio = float(dirty.mean())
    assert dirty_ratio < 0.02, f"dirty_white mullion ratio {dirty_ratio}"
    assert float(desk_luma.mean()) > float(mid_luma.mean()) - 12.0, (
        f"mullion mean darkened too much: mid={mid_luma.mean():.1f} desk={desk_luma.mean():.1f}"
    )
    # Glass still pulls
    pane = p[70:140, 60:120].mean(axis=2)
    assert float(pane.mean()) < 180.0, f"pane did not pull: {pane.mean()}"


def test_low_conf_glass_leaves_mid_not_half_dirty():
    """Weak / still-blown −EV panes leave mid (blown white ok), no half-dirty."""
    from PIL import Image

    h, w = 180, 240
    mid = np.full((h, w, 3), 120, dtype=np.uint8)
    under = np.full((h, w, 3), 50, dtype=np.uint8)
    mid[40:140, 50:190, :] = 255
    # Mostly useless −EV: still very bright, tiny chroma, almost no structure
    under[40:140, 50:190, :] = 235
    under[80:84, 90:150, :] = (220, 225, 218)  # feeble cue — must not half-pull
    mid_img = Image.fromarray(mid, "RGB")
    und_img = Image.fromarray(under, "RGB")
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    assert float(matte[60:120, 70:170].mean()) < 0.05
    pulled = window_pull_from_brackets(mid_img, und_img, None, reference_mid=mid_img)
    p = np.asarray(pulled, dtype=np.float32)
    # Stay near mid white, not muddy grey mid-pull
    assert float(p[60:120, 70:170].mean()) > 240.0



def test_dark_ev_low_conf_pane_leaves_mid():
    """v7: dark −EV + low/patchy conf → leave mid (blown white), never half-dirty."""
    from PIL import Image

    h, w = 200, 280
    mid = np.full((h, w, 3), 120, dtype=np.uint8)
    under = np.full((h, w, 3), 30, dtype=np.uint8)
    mid[40:160, 50:230, :] = 255
    # Very dark −EV with only sparse speckles of structure (patchy/low-conf)
    under[40:160, 50:230, :] = (8, 12, 6)
    for i in range(55, 150, 28):  # sparse — must not half-pull
        under[i : i + 1, 80:120, :] = (20, 50, 18)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    assert float(matte[70:130, 80:200].mean()) < 0.05, "dark low-conf must leave mid"
    mid_img = Image.fromarray(mid, "RGB")
    und_img = Image.fromarray(under, "RGB")
    pulled = window_pull_from_brackets(mid_img, und_img, None, reference_mid=mid_img)
    p = np.asarray(pulled, dtype=np.float32)
    assert float(p[70:130, 80:200].mean()) > 240.0, "pane must stay blown mid-white"


def test_mullion_pixels_stay_mid_after_pull_v7():
    """v7: after pull, mullion luma stays ~mid (no dark fringe / matte bleed)."""
    from PIL import Image

    h, w = 220, 360
    mid = np.full((h, w, 3), 140, dtype=np.uint8)
    under = np.full((h, w, 3), 50, dtype=np.uint8)
    mid[40:180, 40:150, :] = 255
    mid[40:180, 190:300, :] = 255
    under[40:180, 40:150, :] = (40, 115, 50)
    under[40:180, 190:300, :] = (35, 105, 45)
    for i in range(50, 170, 10):
        under[i : i + 2, 55:140, :] = (30, 80, 35)
        under[i : i + 2, 205:290, :] = (28, 75, 32)
    mid[40:180, 150:190, :] = 250
    under[40:180, 150:190, :] = (95, 94, 92)

    mid_img = Image.fromarray(mid, "RGB")
    und_img = Image.fromarray(under, "RGB")
    pulled = window_pull_from_brackets(mid_img, und_img, None, reference_mid=mid_img)
    p = np.asarray(pulled, dtype=np.float32)
    m = mid.astype(np.float32)
    mullion = (slice(60, 160), slice(155, 185))
    mid_luma = m[mullion].mean(axis=2)
    desk_luma = p[mullion].mean(axis=2)
    drop = mid_luma - desk_luma
    dirty_ratio = float((drop >= 40.0).mean())
    assert dirty_ratio < 0.02, f"mullion dirty_white ratio {dirty_ratio}"
    assert float(desk_luma.mean()) > float(mid_luma.mean()) - 10.0


def test_uneven_pane_family_prefers_leave_mid():
    """v8: solid pane pulls −EV; patchy dark sibling leaves mid (no blotch)."""
    h, w = 220, 400
    mid = np.full((h, w, 3), 130, dtype=np.uint8)
    under = np.full((h, w, 3), 45, dtype=np.uint8)
    # Solid recoverable left pane
    mid[40:180, 30:160, :] = 255
    under[40:180, 30:160, :] = (40, 120, 50)
    for i in range(50, 170, 8):
        under[i : i + 2, 45:145, :] = (30, 85, 35)
    # Right pane: blown mid but almost-useless dark speckled −EV (must leave mid)
    mid[40:180, 220:360, :] = 255
    under[40:180, 220:360, :] = (5, 8, 4)
    under[90:93, 260:280, :] = (25, 40, 20)  # feeble cue only
    # White mullion between
    mid[40:180, 160:220, :] = 248
    under[40:180, 160:220, :] = (90, 90, 88)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    left = float(matte[70:150, 50:140].mean())
    right = float(matte[70:150, 240:340].mean())
    mullion = float(matte[70:150, 170:210].mean())
    # v8: solid high-conf left MUST pull; patchy right leaves mid
    assert left > 0.20, f"solid left must pull −EV (not leave-mid bank): {left}"
    assert right < 0.05, f"patchy right must leave mid: {right}"
    assert mullion < 0.08, f"mullion leaked: {mullion}"


def test_wall_ghost_still_killed_after_edge_lock():
    """Regression: beige wall between banks must stay matte 0 after v6 edge-lock."""
    h, w = 280, 480
    mid = np.zeros((h, w, 3), dtype=np.uint8)
    mid[:, :] = (168, 158, 142)
    under = np.zeros((h, w, 3), dtype=np.uint8)
    under[:, :] = (95, 88, 78)
    panes = [(50, 120, 40, 120), (50, 120, 140, 220), (140, 210, 40, 120), (140, 210, 140, 220)]
    for y0, y1, x0, x1 in panes:
        mid[y0:y1, x0:x1, :] = 255
        under[y0:y1, x0:x1, :] = (42, 118, 48)
        for i in range(y0 + 5, y1 - 5, 8):
            under[i : i + 2, x0 + 10 : x1 - 10, :] = (30, 80, 35)
    mid[50:210, 120:140, :] = 248
    under[50:210, 120:140, :] = (90, 90, 88)
    mid[120:140, 40:220, :] = 248
    under[120:140, 40:220, :] = (90, 90, 88)
    mid[40:220, 240:300, :] = (175, 165, 148)
    under[40:220, 240:300, :] = (100, 92, 82)
    mid[55:200, 320:450, :] = 255
    under[55:200, 320:450, :] = (38, 105, 50)
    for i in range(60, 190, 9):
        under[i : i + 2, 330:440, :] = (28, 75, 32)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    between_wall = float(matte[70:180, 250:290].mean())
    mullion = float(matte[70:110, 124:136].mean())
    assert between_wall < 0.02, f"WALL GHOST: {between_wall}"
    assert mullion < 0.08, f"mullion matte: {mullion}"
    assert float(matte[70:110, 60:100].mean()) > 0.20, "left glass too weak"
    # Frame lock may be edge-heavy when bars touch panes; matte must still be 0
    frame = _frame_edge_lock_mask(mid.astype(np.float32))
    assert float(frame.mean()) > 0.0



def test_pick_under_mid_over_unnamed_base_with_ev_siblings():
    """Cove-style names: Lighthouse.jpg + _under + _over → mid is the base plate."""
    under = Image.new("RGB", (64, 48), (20, 20, 20))
    mid = Image.new("RGB", (64, 48), (120, 120, 120))
    over = Image.new("RGB", (64, 48), (240, 240, 240))
    # Biases as exposure_bias_from_name would yield
    picked_mid, picked_under, picked_over = _pick_under_mid_over(
        [mid, over, under], [None, 2.0, -2.0]
    )
    import numpy as np
    assert tuple(np.asarray(picked_mid)[0, 0]) == (120, 120, 120)
    assert tuple(np.asarray(picked_under)[0, 0]) == (20, 20, 20)
    assert tuple(np.asarray(picked_over)[0, 0]) == (240, 240, 240)

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




def test_good_conf_exterior_prefers_ev_pull_v8():
    """v8: readable −EV exterior with solid conf must pull, not leave-mid bank."""
    h, w = 220, 360
    mid = np.full((h, w, 3), 140, dtype=np.uint8)
    under = np.full((h, w, 3), 50, dtype=np.uint8)
    # Multi-pane bank with solid foliage under (lawn/trees family)
    panes = [(40, 100, 40, 150), (40, 100, 170, 280), (120, 180, 40, 150), (120, 180, 170, 280)]
    for y0, y1, x0, x1 in panes:
        mid[y0:y1, x0:x1, :] = 255
        under[y0:y1, x0:x1, :] = (45, 125, 55)
        for i in range(y0 + 4, y1 - 4, 7):
            under[i : i + 2, x0 + 8 : x1 - 8, :] = (30, 90, 35)
    # Mullions
    mid[40:180, 150:170, :] = 250
    under[40:180, 150:170, :] = (95, 94, 92)
    mid[100:120, 40:280, :] = 250
    under[100:120, 40:280, :] = (95, 94, 92)
    matte = _glass_only_matte(mid.astype(np.float32), under.astype(np.float32))
    for y0, y1, x0, x1 in panes:
        pane = float(matte[y0 + 10 : y1 - 10, x0 + 15 : x1 - 15].mean())
        assert pane > 0.20, f"good-conf pane leave-mid wrongly: {pane} @ {(y0,x0)}"
    assert float(matte[50:90, 155:165].mean()) < 0.10


def test_highlight_cap_vs_mid_preserves_exterior_detail():
    """v8: desk glass must not clip harder than mid when mid still has lawn/trees."""
    h, w = 160, 240
    mid = np.full((h, w, 3), 120, dtype=np.uint8)
    # Mid glass still has exterior (not pure white)
    mid[30:130, 40:200, :] = (210, 215, 200)
    mid[50:90, 70:170, :] = (160, 190, 120)  # lawn/trees chroma
    # Desk over-clips to near-pure white (v7 failure mode)
    desk = mid.copy()
    desk[30:130, 40:200, :] = 252
    under = np.full((h, w, 3), 40, dtype=np.uint8)
    under[30:130, 40:200, :] = (50, 130, 55)
    out = _highlight_cap_vs_mid(
        Image.fromarray(desk, "RGB"),
        Image.fromarray(mid, "RGB"),
        under=Image.fromarray(under, "RGB"),
    )
    o = np.asarray(out, dtype=np.float32)
    m = mid.astype(np.float32)
    glass = (slice(40, 120), slice(60, 180))
    desk_l = float(o[glass].mean())
    mid_l = float(m[glass].mean())
    assert desk_l < 245.0, f"desk still near-pure white: {desk_l}"
    assert desk_l <= mid_l + 12.0, f"desk clips harder than mid: desk={desk_l} mid={mid_l}"
    # Exterior chroma family preserved (not washed to neutral white)
    chroma = o[glass].max(axis=2) - o[glass].min(axis=2)
    assert float(chroma.mean()) > 8.0, f"exterior chroma washed out: {chroma.mean()}"




def test_neutralize_magenta_highlights_kills_pink_sky():
    """Clipped magenta/pink sky → neutral grey/white (no invent blue)."""
    h, w = 120, 180
    img = np.full((h, w, 3), 80, dtype=np.uint8)
    # Blown pink sky patch (classic demosaic fail)
    img[10:50, 30:150, 0] = 252
    img[10:50, 30:150, 1] = 190
    img[10:50, 30:150, 2] = 245
    out = np.asarray(_neutralize_magenta_highlights(Image.fromarray(img, "RGB")), dtype=np.float32)
    patch = out[15:45, 40:140]
    mag = (patch[:, :, 0] + patch[:, :, 2]) / 2.0 - patch[:, :, 1]
    assert float(mag.mean()) < 6.0, f"magenta remains: {mag.mean()}"
    # Stays near-white/grey, not blue invent
    assert float(patch[:, :, 2].mean() - patch[:, :, 0].mean()) < 8.0
    # Interior body unchanged
    assert abs(float(out[80:110, 40:140].mean()) - 80.0) < 3.0


def test_body_beat_mid_lifts_crushed_sofa_not_glass():
    """Crushed interior lifts toward mid; blown glass stays capped (no milk)."""
    h, w = 160, 220
    mid = np.full((h, w, 3), 130, dtype=np.uint8)
    # Readable mid body (sofa family)
    mid[90:150, 20:200, :] = (160, 150, 140)
    # Mid glass still has exterior detail
    mid[20:70, 30:190, :] = (205, 210, 200)
    mid[35:55, 60:160, :] = (150, 175, 110)
    desk = mid.copy()
    desk[90:150, 20:200, :] = 18  # crushed sofa silhouette
    desk[20:70, 30:190, :] = 252  # over-clipped glass
    out = _body_beat_mid(
        Image.fromarray(desk, "RGB"),
        Image.fromarray(mid, "RGB"),
        amount=0.28,
    )
    o = np.asarray(out, dtype=np.float32)
    body = float(o[100:140, 40:180].mean())
    glass = float(o[25:65, 40:180].mean())
    glass_in = float(desk[25:65, 40:180].astype(np.float32).mean())
    assert body > 90.0, f"body still crushed: {body}"
    assert body > 18.0 + 40.0
    # Body lift must not bloom glass further (highlight_cap owns glass)
    assert glass <= glass_in + 1.5, f"glass bloomed by body lift: {glass} from {glass_in}"


def test_cove_sky_mid_bias_still_locked_after_v8():
    """Cove ACCEPT: bright sky stays mid-biased (never dirtier/pinker than mid)."""
    h, w = 140, 200
    mid = np.full((h, w, 3), 100, dtype=np.uint8)
    # Upper sky band — clean mid plate (cove sun family)
    mid[0:45, :, :] = (230, 235, 245)
    working = mid.copy()
    # Dirtier/weirder desk sky (green-yellow mud)
    working[0:45, :, :] = (180, 200, 140)
    out = _sky_mid_bias_restore(
        Image.fromarray(working, "RGB"),
        Image.fromarray(mid, "RGB"),
        under=None,
    )
    o = np.asarray(out, dtype=np.float32)
    sky = o[5:35, 20:180]
    msky = mid[5:35, 20:180].astype(np.float32)
    # Restored toward mid (not left muddy)
    assert float(np.abs(sky - msky).mean()) < 25.0, f"cove sky not mid-biased: {np.abs(sky-msky).mean()}"
    # Not pinker than mid
    mag_o = (sky[:, :, 0] + sky[:, :, 2]) / 2 - sky[:, :, 1]
    mag_m = (msky[:, :, 0] + msky[:, :, 2]) / 2 - msky[:, :, 1]
    assert float(mag_o.mean()) <= float(mag_m.mean()) + 4.0


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



def test_mertens_uint8_not_near_black():
    """OpenCV float[0,1] Mertens was near-black on cv2 5.x — uint8 path must stay lit."""
    under = Image.new("RGB", (320, 240), (40, 40, 40))
    mid = Image.new("RGB", (320, 240), (120, 110, 100))
    over = Image.new("RGB", (320, 240), (200, 190, 180))
    fused = merge_brackets_mertens([under, mid, over])
    mean = float(np.asarray(fused, dtype=np.float32).mean())
    assert mean > 40.0, f"Mertens near-black/crush: mean={mean}"


def test_exposure_fusion_beats_or_matches_mid_body():
    """Enfuse (if present) or uint8 Mertens must not land darker than mid body."""
    under = Image.new("RGB", (400, 300), (50, 45, 40))
    mid = Image.new("RGB", (400, 300), (130, 120, 110))
    over = Image.new("RGB", (400, 300), (210, 200, 190))
    # paint a sofa-ish body region
    m = np.asarray(mid).copy(); m[110:250, 50:300] = (140, 125, 110); mid = Image.fromarray(m)
    o = np.asarray(over).copy(); o[110:250, 50:300] = (200, 185, 165); over = Image.fromarray(o)
    fused, tag = merge_exposure_fusion([under, mid, over], mid=mid)
    assert tag in ("enfuse", "mertens_u8", "mid")
    if tag != "mid":
        assert _body_luma_mean(fused) >= _body_luma_mean(mid) * 0.95


def test_interior_body_lift_beats_mid_protects_glass():
    mid = np.full((160, 220, 3), 120, dtype=np.uint8)
    mid[20:70, 30:190] = 230  # glass
    mid[90:150, 20:200] = (150, 140, 130)
    desk = mid.copy()
    desk[90:150, 20:200] = 70
    out = _interior_body_lift(Image.fromarray(desk), Image.fromarray(mid), target_body=148.0)
    o = np.asarray(out, dtype=np.float32)
    assert float(o[100:140, 40:180].mean()) > 90.0
    assert float(o[25:65, 40:180].mean()) <= 232.0


def test_body_plates_for_fuse_drops_darkest():
    """RE interior: under reserved for −EV; body fuse is mid+overs."""
    under = Image.new("RGB", (64, 48), (40, 35, 30))
    mid = Image.new("RGB", (64, 48), (120, 110, 100))
    over = Image.new("RGB", (64, 48), (200, 190, 180))
    plates = _body_plates_for_fuse([under, mid, over])
    assert len(plates) == 2
    means = [float(np.asarray(im.convert("L")).mean()) for im in plates]
    assert min(means) >= float(np.asarray(mid.convert("L")).mean()) - 1.0
