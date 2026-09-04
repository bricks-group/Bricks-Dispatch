"""Derive a 2D floor plan from aligned 360 stations.

Geometry comes from station pose (x, y, heading) + room labels in the capture
manifest, or an optional RoomPlan JSON sidecar. Phone captures often omit
lidar poses — those are inferred by walking STEP_M along each heading.
Inferred layouts are approximate walk graphs, not surveyed floor plates.
"""
from __future__ import annotations

import io
import json
import math
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1600
CANVAS_H = 1100
MARGIN = 100

# Approximate walk spacing when pose_x/y are missing (not a surveyed grid).
STEP_M = 3.0


def infer_station_poses(stations: list[dict]) -> list[dict]:
    """Fill missing pose_x/y by walking along each prior station heading.

    Phone capture (web/Expo) rarely sends lidar poses. Without this, plans
    render as a row of identical boxes instead of a path.
    Heading 0 = north. Step is STEP_M (~3 m) along the look direction.
    Stations that receive filled poses are marked pose_inferred=True.
    """
    if not stations:
        return []
    out: list[dict] = []
    for i, raw in enumerate(stations):
        st = dict(raw)
        px, py = st.get("pose_x"), st.get("pose_y")
        if px is not None and py is not None:
            st.setdefault("pose_inferred", False)
            out.append(st)
            continue
        if i == 0:
            st["pose_x"] = 0.0
            st["pose_y"] = 0.0
        else:
            prev = out[i - 1]
            heading = prev.get("heading")
            try:
                heading = float(heading) if heading is not None else 90.0
            except (TypeError, ValueError):
                heading = 90.0
            rad = math.radians(heading)
            st["pose_x"] = float(prev.get("pose_x") or 0.0) + STEP_M * math.sin(rad)
            st["pose_y"] = float(prev.get("pose_y") or 0.0) - STEP_M * math.cos(rad)
        st["pose_inferred"] = True
        out.append(st)
    return out


def stations_poses_inferred(stations: list[dict]) -> bool:
    """True when any station pose was filled by infer_station_poses."""
    return any(bool(st.get("pose_inferred")) for st in stations)


class FloorplanError(RuntimeError):
    """Honest failure — caller must mark the session failed, never Delivered."""


def parse_roomplan_json(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    rooms = data.get("rooms") or data.get("CapturedRooms") or data.get("surfaces")
    if not rooms:
        return None
    return data


def stations_to_rooms(stations: list[dict]) -> list[dict]:
    groups: dict[str, list] = {}
    order: list[str] = []
    for i, st in enumerate(stations):
        name = str(st.get("room") or st.get("room_name") or f"Station {i + 1}").strip() or f"Room {i + 1}"
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(st)

    posed = all(
        st.get("pose_x") is not None and st.get("pose_y") is not None
        for st in stations
    ) if stations else False
    rooms = []
    if posed:
        for name in order:
            sts = groups[name]
            xs = [float(s["pose_x"]) for s in sts]
            ys = [float(s["pose_y"]) for s in sts]
            pad = 1.6
            x0, y0 = min(xs) - pad, min(ys) - pad
            x1, y1 = max(xs) + pad, max(ys) + pad
            rooms.append({
                "name": name,
                "x": x0, "y": y0, "w": max(x1 - x0, 2.5), "h": max(y1 - y0, 2.5),
                "stations": sts,
            })
        return rooms
    for i, name in enumerate(order):
        rooms.append({
            "name": name, "x": float(i * 12), "y": 0.0, "w": 10.0, "h": 10.0,
            "stations": groups[name],
        })
    return rooms


def _font(size: int, bold: bool = False):
    paths = []
    if bold:
        paths.extend((
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ))
    paths.extend((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ))
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _scale(rooms: list[dict]):
    minx = min(r["x"] for r in rooms)
    miny = min(r["y"] for r in rooms)
    maxx = max(r["x"] + r["w"] for r in rooms)
    maxy = max(r["y"] + r["h"] for r in rooms)
    span_x = max(maxx - minx, 1.0)
    span_y = max(maxy - miny, 1.0)
    # Leave room for title, north arrow, scale bar, honesty caption.
    inner_w = CANVAS_W - 2 * MARGIN - 40
    inner_h = CANVAS_H - 2 * MARGIN - 70
    s = min(inner_w / span_x, inner_h / span_y)

    def xy(x, y):
        return (int(MARGIN + (x - minx) * s), int(MARGIN + 44 + (y - miny) * s))

    return xy, s


def _draw_north_arrow(draw: ImageDraw.ImageDraw, cx: int, cy: int, font) -> None:
    """Simple north arrow — heading 0 is north in station poses."""
    shaft = 28
    # Arrow shaft + head pointing up (screen -Y = north on plan).
    draw.line([(cx, cy + shaft // 2), (cx, cy - shaft)], fill="#1c1917", width=3)
    draw.polygon(
        [(cx, cy - shaft - 2), (cx - 8, cy - shaft + 14), (cx + 8, cy - shaft + 14)],
        fill="#1c1917",
    )
    draw.text((cx, cy + shaft // 2 + 14), "N", fill="#1c1917", font=font, anchor="mm")


def _nice_scale_m(s: float) -> float:
    """Pick a readable scale-bar length in meters for current px/m."""
    if s <= 0:
        return STEP_M
    target_px = 120
    raw = target_px / s
    for cand in (1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0):
        if cand >= raw * 0.6:
            return cand
    return max(STEP_M, float(round(raw)))


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    bx: int,
    by: int,
    s: float,
    font,
    *,
    poses_inferred: bool,
    from_roomplan: bool,
) -> None:
    """Honest meter scale — never claims surveyed / lidar precision when inferred."""
    scale_m = _nice_scale_m(s)
    # Prefer STEP_M when poses were inferred so the bar matches walk spacing.
    if poses_inferred:
        scale_m = STEP_M
    bar = max(int(scale_m * s), 24)
    wall = "#1c1917"
    # Bar with end ticks.
    draw.line([(bx, by), (bx + bar, by)], fill=wall, width=3)
    draw.line([(bx, by - 6), (bx, by + 6)], fill=wall, width=2)
    draw.line([(bx + bar, by - 6), (bx + bar, by + 6)], fill=wall, width=2)
    if poses_inferred:
        label = f"{scale_m:g} m approx  ·  layout inferred (~{STEP_M:g} m steps)"
    elif from_roomplan:
        label = f"{scale_m:g} m"
    else:
        label = f"{scale_m:g} m (approx)"
    draw.text((bx + bar + 12, by), label, fill="#57534e", font=font, anchor="lm")


def _label_bbox(draw: ImageDraw.ImageDraw, text: str, font, cx: int, cy: int, pad: int = 8):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return [cx - tw // 2 - pad, cy - th // 2 - pad, cx + tw // 2 + pad, cy + th // 2 + pad]


def render_plan_png(
    rooms: list[dict],
    title: str = "Floor plan",
    *,
    poses_inferred: bool = False,
    from_roomplan: bool = False,
) -> bytes:
    if not rooms:
        raise FloorplanError("No stations to derive a floor plan from")
    xy, s = _scale(rooms)
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), "#f4f1ea")
    draw = ImageDraw.Draw(img)
    title_font = _font(26, bold=True)
    label_font = _font(17, bold=True)
    meta_font = _font(12)
    small_font = _font(11)

    draw.text((MARGIN, 20), title, fill="#1c1917", font=title_font)
    draw.text((CANVAS_W - MARGIN, 26), "BRICKS", fill="#a8a29e", font=meta_font, anchor="ra")

    # Soft overall footprint shadow first (behind rooms).
    for r in rooms:
        x0, y0 = xy(r["x"], r["y"])
        x1, y1 = xy(r["x"] + r["w"], r["y"] + r["h"])
        draw.rectangle([x0 + 4, y0 + 4, x1 + 4, y1 + 4], fill="#e7e5e0")

    fills = ["#fafaf9", "#f7f2e9", "#efe8dc", "#e8ddd0"]
    wall = "#1c1917"
    wall_inner = "#44403c"
    path_pts = []

    for i, r in enumerate(rooms):
        x0, y0 = xy(r["x"], r["y"])
        x1, y1 = xy(r["x"] + r["w"], r["y"] + r["h"])
        # Outer wall then fill for clearer footprint edges.
        draw.rectangle([x0, y0, x1, y1], fill=fills[i % len(fills)], outline=wall, width=5)
        # Inner hairline for wall thickness read.
        if x1 - x0 > 16 and y1 - y0 > 16:
            draw.rectangle([x0 + 3, y0 + 3, x1 - 3, y1 - 3], outline=wall_inner, width=1)
        # Door gap on shared edge toward previous room (visual only).
        if i > 0:
            mx = (x0 + x1) // 2
            draw.line([(mx - 20, y0), (mx + 20, y0)], fill=fills[i % len(fills)], width=10)
            draw.line([(mx - 20, y0), (mx + 20, y0)], fill="#f4f1ea", width=6)

        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        name = str(r.get("name") or f"Room {i + 1}")
        # Label near top of room so station dots stay readable in the center.
        label_y = y0 + max(22, min(36, (y1 - y0) // 5))
        chip = _label_bbox(draw, name, label_font, cx, label_y, pad=7)
        # Keep chip inside room bounds when possible.
        chip[0] = max(chip[0], x0 + 4)
        chip[2] = min(chip[2], x1 - 4)
        draw.rounded_rectangle(chip, radius=6, fill="#fffdf8", outline="#d6d3d1", width=1)
        draw.text((cx, label_y), name, fill=wall, font=label_font, anchor="mm")

        for j, st in enumerate(r.get("stations") or []):
            px, py = st.get("pose_x"), st.get("pose_y")
            if px is not None and py is not None:
                sx, sy = xy(float(px), float(py))
            else:
                n = max(len(r.get("stations") or []), 1)
                sx = x0 + int((j + 1) / (n + 1) * (x1 - x0))
                sy = cy + 28
            draw.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], fill="#8B3A2A", outline=wall, width=2)
            idx = st.get("station_index", j)
            draw.text((sx, sy), str(idx), fill="#fff", font=meta_font, anchor="mm")
            heading = st.get("heading")
            if heading is not None:
                try:
                    rad = math.radians(float(heading) - 90)
                    draw.line(
                        [(sx, sy), (sx + int(20 * math.cos(rad)), sy + int(20 * math.sin(rad)))],
                        fill="#8B3A2A",
                        width=2,
                    )
                except (TypeError, ValueError):
                    pass
            path_pts.append((sx, sy))

    for a, b in zip(path_pts, path_pts[1:]):
        draw.line([a, b], fill="#8B3A2A", width=2)

    # North arrow (top-right of plan area).
    _draw_north_arrow(draw, CANVAS_W - MARGIN - 28, MARGIN + 70, _font(14, bold=True))

    # Scale bar + honesty caption.
    bx, by = MARGIN, CANVAS_H - 48
    _draw_scale_bar(
        draw, bx, by, s, meta_font,
        poses_inferred=poses_inferred,
        from_roomplan=from_roomplan,
    )
    if poses_inferred:
        caption = "Approximate layout from capture path — not lidar / surveyed precision"
        draw.text((MARGIN, CANVAS_H - 24), caption, fill="#78716c", font=small_font, anchor="ls")
    elif from_roomplan:
        draw.text((MARGIN, CANVAS_H - 24), "RoomPlan geometry", fill="#78716c", font=small_font, anchor="ls")
    else:
        draw.text(
            (MARGIN, CANVAS_H - 24),
            "Station poses · room boxes approximate",
            fill="#78716c",
            font=small_font,
            anchor="ls",
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def derive_floorplan(stations: list[dict], address: str = "", roomplan: Optional[dict] = None) -> bytes:
    if roomplan:
        raw_rooms = roomplan.get("rooms") or []
        mapped = []
        for i, r in enumerate(raw_rooms):
            if not isinstance(r, dict):
                continue
            mapped.append({
                "name": r.get("name") or r.get("label") or f"Room {i+1}",
                "x": float(r.get("x", i * 12)),
                "y": float(r.get("y", 0)),
                "w": float(r.get("w", r.get("width", 10))),
                "h": float(r.get("h", r.get("depth", r.get("length", 10)))),
                "stations": [],
            })
        if mapped:
            title = f"{address} — floor plan" if address else "Floor plan"
            return render_plan_png(mapped, title=title, poses_inferred=False, from_roomplan=True)
    if not stations:
        raise FloorplanError("No aligned stations (or RoomPlan) to derive a floor plan from")
    stations = infer_station_poses(stations)
    inferred = stations_poses_inferred(stations)
    rooms = stations_to_rooms(stations)
    title = f"{address} — floor plan" if address else "Floor plan"
    return render_plan_png(rooms, title=title, poses_inferred=inferred, from_roomplan=False)
