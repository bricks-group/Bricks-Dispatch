"""Delivery email v2: one template, QC-gated gallery, light/dark, motion that degrades."""
import asyncio
import io
import re
from unittest.mock import AsyncMock

from PIL import Image

from app.routers import delivery_email_media as route
from app.services import client_comms as cc
from app.services import delivery_email as de
from app.services import delivery_email_media as dem


def _stills(n):
    return [de.PreviewStill(f"https://d/t{i}.jpg", f"https://d/h{i}.jpg", f"Photo {i + 1} of {n}, 1 Main St") for i in range(n)]


def _ctx(**kw):
    base = dict(recipient_name="Alex", address="1 Main St", primary_url="https://app.brxs.ca/share/tok",
                desk_url="https://app.brxs.ca/properties/j1", share_link=True, photo_count=24,
                services=["24 photos", "Video"], stills=_stills(5), more_count=19)
    base.update(kw)
    return de.DeliveryEmailContext(**base)


def test_render_has_light_dark_and_one_orange_button():
    r = de.render_delivery_email(_ctx())
    h = r.html
    assert '<meta name="color-scheme" content="light dark">' in h
    assert '<meta name="supported-color-schemes" content="light dark">' in h
    assert "@media (prefers-color-scheme:dark)" in h
    assert "[data-ogsc] .bx-ink" in h and "[data-ogsb] .bx-card" in h
    assert h.count(">View your gallery<") == 2  # VML (Outlook) + HTML twin
    assert h.count('bgcolor="#D2542B"') == 1
    assert r.subject == "Your photos are ready · 1 Main St"
    for banned in (" GO ", "Bricks OS", "GO!"):
        assert banned not in h and banned not in r.text
    assert "help@brxs.ca" in h and "help@brxs.ca" in r.text
    assert "https://app.brxs.ca/share/tok" in r.text


def test_motion_is_progressive_and_reduced_motion_safe():
    h = de.render_delivery_email(_ctx()).html
    motion = h.split("@media (prefers-reduced-motion:no-preference)")[1].split("</style>")[0]
    assert "@keyframes bx-rise" in motion and "@keyframes bx-brick" in motion
    assert h.count("@keyframes") == motion.count("@keyframes")  # no motion outside the guard
    # Tiles rest fully visible inline — clients without @keyframes see the static grid.
    for tile in re.findall(r'<td class="bx-t\d"[^>]*>', h):
        assert "opacity:0" not in tile
    # Live brick twin: exact logo geometry, hidden unless @supports clients swap it in.
    assert "left:3.00px;top:4.00px;width:11.00px;height:6.40px" in h
    assert "left:10.20px;top:11.60px;width:10.80px;height:8.40px" in h
    assert 'class="bx-mark-live" style="display:none' in h
    # Motion CSS is in its own <style>, so Gmail dropping it keeps the mobile block.
    base = h.split("<style>")[1]
    assert "max-width:620px" in base and "@keyframes" not in base


def test_gallery_grid_alt_text_and_more_count():
    h = de.render_delivery_email(_ctx()).html
    assert h.count('class="bx-tile-img') == 4
    assert 'alt="Photo 1 of 5, 1 Main St"' in h
    assert "+19 more photos in your gallery" in h
    # 4 stills → hero + 2 tiles (even grid)
    h3 = de.render_delivery_email(_ctx(stills=_stills(4))).html
    assert h3.count('class="bx-tile-img') == 2
    assert de.shown_still_count(_stills(4)) == 3


def test_gif_hero_keeps_static_for_reduced_motion():
    h = de.render_delivery_email(_ctx(hero_gif_url="https://d/x.gif")).html
    assert 'src="https://d/x.gif"' in h
    assert '<source srcset="https://d/h0.jpg" media="(prefers-reduced-motion: reduce)">' in h


def test_no_stills_is_graceful():
    r = de.render_delivery_email(_ctx(stills=[], photo_count=0, more_count=0, headline_what="floor plan",
                                      headline_verb="is", button_label="View your floor plan", services=["Floor plan"]))
    assert "<img class=\"bx-hero-img" not in r.html and "<img class=\"bx-tile-img" not in r.html
    assert "Everything for this delivery is waiting on one page" in r.html
    assert r.subject == "Your floor plan is ready · 1 Main St"


def test_client_wording_has_no_listing_or_seller():
    r = de.render_delivery_email(_ctx(client=True, address=""))
    low = r.html.lower()
    assert "listing" not in low and "seller" not in low
    assert "your bricks account" in low


# --- QC gate ---------------------------------------------------------------

def _row(**kw):
    base = {"id": "u1", "job_id": "j1", "filename": "IMG_1.jpg", "processed_url": "bricks-go/jobs/j1/u1/IMG_1.jpg",
            "processing_status": "completed", "qc_status": "passed", "provider": None}
    base.update(kw)
    return base


def test_only_qc_passed_listing_stills_are_eligible():
    assert dem.is_preview_eligible(_row())
    for bad in (
        {"qc_status": "flagged"}, {"qc_status": "rejected"}, {"qc_status": "pending"}, {"qc_status": None},
        {"processing_status": "failed"}, {"processing_status": "processing"},
        {"provider": "bracket_sibling"}, {"provider": "visit"},
        {"filename": "DSC_1.ARW", "processed_url": "bricks-go/jobs/j1/u1/DSC_1.ARW"},
        {"filename": "floor_plan.png"}, {"filename": "reel.mp4", "processed_url": "x/reel.mp4"},
        {"filename": "plan.pdf", "processed_url": "x/plan.pdf"},
    ):
        assert not dem.is_preview_eligible(_row(**bad)), bad


def test_candidates_skip_flagged_and_non_photo_kinds():
    urls = [
        {"upload_id": "a"}, {"upload_id": "b", "qc_flagged": True}, {"upload_id": "c", "kind": "floorplan"},
        {"upload_id": "d", "kind": "reel"}, {"upload_id": "e", "kind": "drone"}, {"url": "x"}, {"upload_id": "a"},
    ]
    assert dem.preview_candidate_ids(urls) == ["a", "e"]


def test_signature_binds_job_asset_and_ids(monkeypatch):
    monkeypatch.setenv("DELIVERY_EMAIL_MEDIA_SECRET", "test-secret")
    sig = dem.sign_asset("j1", "u1-tile.jpg")
    assert dem.verify_asset("j1", "u1-tile.jpg", "", sig)
    assert not dem.verify_asset("j2", "u1-tile.jpg", "", sig)
    assert not dem.verify_asset("j1", "u2-tile.jpg", "", sig)
    gsig = dem.sign_asset("j1", "crossfade.gif", "a,b")
    assert not dem.verify_asset("j1", "crossfade.gif", "a,c", gsig)
    url = dem.asset_url("j1", "crossfade.gif", "a,b", base="https://dispatch-api.brxs.ca")
    assert url.startswith("https://dispatch-api.brxs.ca/api/public/delivery-email/j1/crossfade.gif?ids=a,b&s=")
    assert dem.parse_asset("../x.jpg") is None and dem.parse_asset("u1-hero.jpg") == ("hero", "u1")


def _wire(monkeypatch, delivered=True, rows=None, data=b"\xff\xd8jpeg"):
    monkeypatch.setenv("DELIVERY_EMAIL_MEDIA_SECRET", "test-secret")
    monkeypatch.setattr(dem, "job_is_delivered", AsyncMock(return_value=delivered))
    monkeypatch.setattr(dem, "load_eligible_uploads", AsyncMock(return_value=rows if rows is not None else [_row()]))
    monkeypatch.setattr(dem, "build_asset", AsyncMock(return_value=data))


def _get(job, asset, s, ids=""):
    return asyncio.run(route.delivery_email_asset(job, asset, s=s, ids=ids))


def test_route_serves_signed_qc_passed_image(monkeypatch):
    _wire(monkeypatch)
    r = _get("j1", "u1-tile.jpg", dem.sign_asset("j1", "u1-tile.jpg"))
    assert r.status_code == 200 and r.media_type == "image/jpeg"
    assert "max-age=3600" in r.headers["cache-control"]


def test_route_404s_bad_sig_undelivered_or_qc_hold(monkeypatch):
    _wire(monkeypatch)
    assert _get("j1", "u1-tile.jpg", "nope").status_code == 404
    sig = dem.sign_asset("j1", "u1-tile.jpg")
    _wire(monkeypatch, delivered=False)
    assert _get("j1", "u1-tile.jpg", sig).status_code == 404
    _wire(monkeypatch, rows=[])  # QC hold / rejected after delivery
    assert _get("j1", "u1-tile.jpg", sig).status_code == 404
    _wire(monkeypatch, rows=[_row(id="a")])  # GIF needs every frame still eligible
    gsig = dem.sign_asset("j1", "crossfade.gif", "a,b")
    assert _get("j1", "crossfade.gif", gsig, "a,b").status_code == 404


def _jpeg(color, size=(1600, 1067)):
    buf = io.BytesIO()
    im = Image.new("RGB", size, color)
    for x in range(0, size[0], 40):  # some detail so the palette works
        im.paste(tuple((c + x) % 255 for c in color), (x, 0, x + 20, size[1]))
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_gif_under_budget_and_first_frame_is_hero():
    srcs = [_jpeg((200, 120, 80)), _jpeg((60, 90, 140)), _jpeg((30, 140, 60))]
    gif = dem.make_crossfade_gif(srcs)
    assert gif and len(gif) <= dem.GIF_BUDGET_BYTES
    im = Image.open(io.BytesIO(gif))
    assert im.n_frames >= 6 and im.info.get("loop") == 0
    assert dem.make_crossfade_gif(srcs[:1]) is None
    assert dem.make_crossfade_gif(srcs, budget=10) is None


def test_jpeg_cover_sizes():
    out = Image.open(io.BytesIO(dem.make_jpeg(_jpeg((10, 20, 30), (4000, 3000)), dem.TILE_SIZE)))
    assert out.size == dem.TILE_SIZE


def test_services_line():
    urls = [{"upload_id": f"u{i}"} for i in range(3)] + [
        {"upload_id": "d", "kind": "drone"}, {"filename": "tour.mp4", "url": "x"},
        {"upload_id": "f", "kind": "floorplan"}, {"kind": "visit", "url": "v"},
    ]
    assert dem.services_from_delivery(urls, photo_count=24) == ["24 photos", "Drone", "Video", "Virtual Visit", "Floor plan"]
    assert dem.services_from_delivery([], ["3D tour"]) == ["3D tour"]


def test_gallery_preview_picks_hero_tiles_and_gif(monkeypatch):
    monkeypatch.setenv("DELIVERY_EMAIL_MEDIA_SECRET", "test-secret")
    rows = [_row(id=f"u{i}") for i in range(7)]
    monkeypatch.setattr(dem, "load_eligible_uploads", AsyncMock(return_value=rows))
    monkeypatch.setattr(dem, "build_asset", AsyncMock(return_value=b"x"))
    pv = asyncio.run(dem.gallery_preview("j1", [{"upload_id": r["id"]} for r in rows], address="1 Main St"))
    assert pv["total"] == 7 and len(pv["stills"]) == 5
    assert pv["stills"][0].hero_url and "u0-hero.jpg" in pv["stills"][0].hero_url
    assert "ids=u0,u5,u6" in pv["hero_gif_url"]
    assert pv["stills"][0].alt == "Photo 1 of 7, 1 Main St"


def test_gallery_preview_drops_gif_when_build_fails(monkeypatch):
    monkeypatch.setenv("DELIVERY_EMAIL_MEDIA_SECRET", "test-secret")
    rows = [_row(id=f"u{i}") for i in range(3)]
    monkeypatch.setattr(dem, "load_eligible_uploads", AsyncMock(return_value=rows))

    async def build(job_id, asset, ids, by_id):
        return None if asset == "crossfade.gif" else b"x"

    monkeypatch.setattr(dem, "build_asset", build)
    pv = asyncio.run(dem.gallery_preview("j1", [{"upload_id": r["id"]} for r in rows]))
    assert pv["hero_gif_url"] == "" and len(pv["stills"]) == 3


def test_photo_email_sends_text_part_and_companion_gets_prerendered(monkeypatch):
    sent = []

    async def send(to, subject, html, text=None):
        sent.append((to, subject, html, text))
        return True

    job = {"id": "j1", "booking_id": "e2e", "address": "1 Main St", "agent_email": "a@example.com", "agent_name": "Alex"}
    monkeypatch.setattr(cc, "_load_job", AsyncMock(return_value=job))
    monkeypatch.setattr(cc, "_client_share_link", AsyncMock(return_value="https://app.brxs.ca/share/tok"))
    monkeypatch.setattr(cc, "_agent_allows", lambda e, f: True)
    monkeypatch.setattr(cc, "_email_agent_direct", send)
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "sent"
    assert sent[0][3] and "View your gallery: https://app.brxs.ca/share/tok" in sent[0][3]

    calls = []

    async def comp(path, payload):
        calls.append(payload)
        return {"sent": True}

    job2 = {**job, "agent_email": "", "booking_id": "6f1c1f9e-3c9b-4a59-9d55-1b7a9a0f5e11"}
    monkeypatch.setattr(cc, "_load_job", AsyncMock(return_value=job2))
    monkeypatch.setattr(cc, "_call_booking_comms_json", comp)
    assert asyncio.run(cc.notify_photos_delivered("j1", [])) == "sent"
    p = calls[0]
    assert p["subject"] == "Your photos are ready · 1 Main St"
    assert "<!DOCTYPE html>" in p["html"] and p["text"]


def test_capture_email_uses_share_page_not_signed_links(monkeypatch):
    sent = []

    async def send(to, subject, html, text=None):
        sent.append((subject, html, text))
        return True

    job = {"booking_id": "e2e", "address": "1 Main St", "agent_email": "a@example.com", "agent_name": "Alex"}
    monkeypatch.setattr(cc, "_load_job", AsyncMock(return_value=job))
    monkeypatch.setattr(cc, "_client_share_link", AsyncMock(return_value="https://app.brxs.ca/share/tok"))
    monkeypatch.setattr(cc, "_agent_allows", lambda e, f: True)
    monkeypatch.setattr(cc, "_email_agent_direct", send)
    urls = [{"filename": "floorplan.pdf", "url": "https://x/object/sign/a?token=1", "kind": "floorplan"}]
    assert asyncio.run(cc.notify_capture_delivered("j1", products=["floor plan"], delivery_urls=urls)) is True
    subject, html, text = sent[0]
    assert subject == "Your floor plan is ready · 1 Main St"
    assert "object/sign" not in html and "https://app.brxs.ca/share/tok" in html
    assert ">View your floor plan<" in html and "Copy client link" in html
