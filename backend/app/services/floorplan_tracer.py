"""Derive a 2D floor plan from aligned 360 stations (Matterport-style).

NOT Gemini-guessing from snapshot photos. Geometry comes from station pose
(x, y, heading) + room labels in the capture manifest, or an optional
RoomPlan JSON sidecar. Stations without pose are laid out in capture order.
"""
from __future__ import annotations

import io
import json
import math
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1600
CANVAS_H = 1100
MARGIN = 90



STEP_M = 3.0


def infer_station_poses(stations: list[dict]) -> list[dict]:
    """Fill missing pose_x/y by walking along each prior station heading.

    Phone capture (web/Expo) rarely sends lidar poses. Without this, plans
    render as a row of identical 10 ft boxes instead of a path.
    Heading 0 = north. Step is ~3 m along the look direction.
    """
    if not stations:
        return []
    out: list[dict] = []
    for i, raw in enumerate(stations):
        st = dict(raw)
        px, py = st.get("pose_x"), st.get("pose_y")
        if px is not None and py is not None:
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
        out.append(st)
    return out


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


def _font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
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
    inner_w = CANVAS_W - 2 * MARGIN
    inner_h = CANVAS_H - 2 * MARGIN - 40
    s = min(inner_w / span_x, inner_h / span_y)

    def xy(x, y):
        return (int(MARGIN + (x - minx) * s), int(MARGIN + 36 + (y - miny) * s))

    return xy, s


def render_plan_png(rooms: list[dict], title: str = "Floor plan") -> bytes:
    if not rooms:
        raise FloorplanError("No stations to derive a floor plan from")
    xy, s = _scale(rooms)
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), "#f7f4ef")
    draw = ImageDraw.Draw(img)
    title_font = _font(28)
    label_font = _font(18)
    meta_font = _font(12)
    draw.text((MARGIN, 22), title, fill="#1c1917", font=title_font)
    draw.text((CANVAS_W - MARGIN, 28), "BRICKS", fill="#a8a29e", font=meta_font, anchor="ra")
    fills = ["#fafaf9", "#f5f0e8", "#efe8dc", "#e7ddd0"]
    wall = "#1c1917"
    path_pts = []
    for i, r in enumerate(rooms):
        x0, y0 = xy(r["x"], r["y"])
        x1, y1 = xy(r["x"] + r["w"], r["y"] + r["h"])
        draw.rectangle([x0, y0, x1, y1], fill=fills[i % len(fills)], outline=wall, width=4)
        if i > 0:
            mx = (x0 + x1) // 2
            draw.line([(mx - 18, y0), (mx + 18, y0)], fill="#f7f4ef", width=8)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        draw.text((cx, cy - 10), r["name"], fill=wall, font=label_font, anchor="mm")
        for j, st in enumerate(r.get("stations") or []):
            px, py = st.get("pose_x"), st.get("pose_y")
            if px is not None and py is not None:
                sx, sy = xy(float(px), float(py))
            else:
                n = max(len(r.get("stations") or []), 1)
                sx = x0 + int((j + 1) / (n + 1) * (x1 - x0))
                sy = cy + 22
            draw.ellipse([sx - 7, sy - 7, sx + 7, sy + 7], fill="#8B3A2A", outline=wall)
            idx = st.get("station_index", j)
            draw.text((sx, sy), str(idx), fill="#fff", font=meta_font, anchor="mm")
            heading = st.get("heading")
            if heading is not None:
                rad = math.radians(float(heading) - 90)
                draw.line([(sx, sy), (sx + int(18 * math.cos(rad)), sy + int(18 * math.sin(rad)))], fill="#8B3A2A", width=2)
            path_pts.append((sx, sy))
    for a, b in zip(path_pts, path_pts[1:]):
        draw.line([a, b], fill="#8B3A2A", width=2)
    bar = int(10 * s) if s > 0 else 80
    bx, by = MARGIN, CANVAS_H - 36
    draw.line([(bx, by), (bx + bar, by)], fill=wall, width=3)
    draw.text((bx + bar + 10, by), "10 ft (approx)", fill="#57534e", font=meta_font, anchor="lm")
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
            return render_plan_png(mapped, title=title)
    if not stations:
        raise FloorplanError("No aligned stations (or RoomPlan) to derive a floor plan from")
    stations = infer_station_poses(stations)
    rooms = stations_to_rooms(stations)
    title = f"{address} — floor plan" if address else "Floor plan"
    return render_plan_png(rooms, title=title)
