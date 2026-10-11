"""Render pilot outputs in the real OS /v/<id> page (local build of Bricks-OS frontend, Spark viewer) in headless Chromium.
The public-visit manifest API is mocked locally, so nothing touches production. usage: viewer_check.py out.json file.sog[:name] ..."""
import json, re, sys, threading, http.server, time, pathlib, os
from playwright.sync_api import sync_playwright
BUILD = "/tmp/os-clean/frontend/build"; ROOT = "/workspace/visit-pilot"
class SPA(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*"); super().end_headers()
    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/files/"): return ROOT + "/" + p[len("/files/"):]
        f = pathlib.Path(BUILD + p)
        return str(f) if f.is_file() else BUILD + "/index.html"
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8771), SPA); threading.Thread(target=srv.serve_forever, daemon=True).start()
VID = "22222222-3333-4444-8555-666666666666"
outp = sys.argv[1]; res = {}
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    for arg in sys.argv[2:]:
        rel, shot = arg.split(":") if ":" in arg else (arg, os.path.basename(arg) + ".png")
        fmt = rel.rsplit(".", 1)[1]
        man = {"id": VID, "title": "TEST pilot (public Mip-NeRF 360 scene)", "address": "TEST " + rel, "unbranded": True,
               "rooms": [{"id": "r1", "name": os.path.basename(rel), "floor_index": 0, "position": 0,
                          "asset_url": "http://127.0.0.1:8771/files/" + rel, "format": fmt}]}
        pg = b.new_page(viewport={"width": 1280, "height": 800}); logs = []
        pg.on("console", lambda m: logs.append(m.type + ": " + m.text))
        body = json.dumps(man)
        pg.route(re.compile(r".*/api/public/visits/.*"), lambda r, req=None, body=body: r.fulfill(status=200, content_type="application/json", body=body))
        t0 = time.time()
        pg.goto(f"http://127.0.0.1:8771/v/{VID}?unbranded=1", wait_until="load", timeout=60000)
        while time.time() - t0 < 240 and "Loading Virtual Visit" in pg.inner_text("body"): time.sleep(2)
        time.sleep(8)
        path = f"{ROOT}/shots/{shot}"
        pg.screenshot(path=path, timeout=240000, animations="disabled")
        res[shot] = {"file": rel, "load_s": round(time.time() - t0, 1), "body": pg.inner_text("body")[:200],
                     "errors": [l for l in logs if l.startswith("error")][:5]}
        print(shot, json.dumps(res[shot]), flush=True)
        pg.close()
    b.close()
json.dump(res, open(outp, "w"), indent=2)
