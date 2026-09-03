"""Compile path-confined 3D tour from equirect stations."""
from __future__ import annotations
import json
from html import escape as html_escape

class TourBuildError(RuntimeError):
    pass
def build_tour_json(stations):
    scenes = []
    for i, st in enumerate(stations):
        scenes.append({
            "id": st["id"],
            "title": st.get("label") or st.get("room") or ("Station %s" % i),
            "room": st.get("room") or "Room",
            "panorama": st.get("file") or ("scenes/%s.jpg" % i),
            "heading": st.get("heading"),
            "next": stations[i + 1]["id"] if i + 1 < len(stations) else None,
            "prev": stations[i - 1]["id"] if i > 0 else None,
            "station_index": st.get("station_index", i),
        })
    return json.dumps({"version": 1, "kind": "equirect-walkthrough", "scenes": scenes}, indent=2)

def build_tour_html(stations, *, address="", title="3D tour"):
    if not stations:
        raise TourBuildError("No equirect stations to compile")
    addr = html_escape(address or "Property")
    ttl = html_escape(title or "3D tour")
    n = len(stations)
    radios = []
    views = []
    nav = []
    for i, st in enumerate(stations):
        sid = "st%s" % i
        src = html_escape(st.get("file") or ("scenes/%s.jpg" % i))
        label = html_escape(st.get("label") or st.get("room") or ("Station %s" % i))
        checked = " checked" if i == 0 else ""
        radios.append("<input type=\"radio\" name=\"station\" id=\"%s\"%s/>" % (sid, checked))
        prev_id = "st%s" % (i - 1) if i else None
        next_id = "st%s" % (i + 1) if i + 1 < n else None
        prev_l = "<label for=\"%s\" class=\"btn\">Previous</label>" % prev_id if prev_id else "<span class=\"btn off\">Previous</span>"
        next_l = "<label for=\"%s\" class=\"btn go\">Next station</label>" % next_id if next_id else "<span class=\"btn off\">Next station</span>"
        views.append(
            "<div class=\"view v%s\"><img src=\"%s\" alt=\"%s\"/><div class=\"cap\">%s</div><div class=\"nav\">%s%s</div></div>"
            % (i, src, label, label, prev_l, next_l)
        )
        nav.append("<label for=\"%s\" class=\"dot d%s\">%s</label>" % (sid, i, label))
    css_rules = []
    for i in range(n):
        css_rules.append("#st%s:checked ~ .stage .v%s{display:flex}" % (i, i))
        css_rules.append("#st%s:checked ~ .dots .d%s{border-color:#fafaf9}" % (i, i))
    return "\n".join([
        "<!DOCTYPE html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\"/>",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>",
        "<title>%s — %s</title>" % (ttl, addr),
        "<style>",
        "html,body{margin:0;height:100%;background:#0c0a09;color:#fafaf9;font-family:Georgia,serif}",
        "header{display:flex;justify-content:space-between;align-items:baseline;padding:12px 16px;border-bottom:1px solid #292524}",
        ".kicker{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#a8a29e}",
        "h1{margin:4px 0 0;font-size:18px;font-weight:500}",
        "input{position:absolute;opacity:0;pointer-events:none}",
        ".stage{height:calc(100% - 120px);position:relative}",
        ".view{display:none;flex-direction:column;height:100%}",
        ".view img{flex:1;width:100%;object-fit:cover;background:#000}",
        ".cap{padding:8px 16px;font-size:15px}",
        ".nav{display:flex;gap:8px;padding:0 16px 12px}",
        ".btn{border:1px solid #44403c;padding:8px 14px;cursor:pointer;font:500 13px/1 Georgia,serif}",
        ".btn.go{border-color:#8B3A2A;background:#8B3A2A}",
        ".btn.off{opacity:.35}",
        ".dots{display:flex;gap:6px;padding:8px 16px;overflow-x:auto}",
        ".dot{border:1px solid #44403c;padding:6px 10px;white-space:nowrap;cursor:pointer;font-size:12px}",
        ".hint{font-family:ui-monospace,monospace;font-size:11px;color:#a8a29e;padding:4px 16px 12px}",
        "\n".join(css_rules),
        "</style></head><body>",
        "<header><div><div class=\"kicker\">Bricks · 3D tour</div><h1>%s</h1></div><div class=\"kicker\">%s</div></header>" % (ttl, addr),
        "".join(radios),
        "<div class=\"stage\">" + "".join(views) + "</div>",
        "<div class=\"dots\">" + "".join(nav) + "</div>",
        "<div class=\"hint\">Path-confined 360 stations · scroll the panorama to look around · next station follows the capture path</div>",
        "</body></html>",
    ])
