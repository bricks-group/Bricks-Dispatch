"""Evidence-based applicant review: form evidence, review engine, owner email."""
from __future__ import annotations

import io
import os
import re
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

os.environ.setdefault("APPLICANT_UPLOAD_SECRET", "test-upload-secret-not-real")

from app.services import applicant_evidence as ev  # noqa: E402
from app.services import applicant_review as ar  # noqa: E402
from app.services import applicant_email as mail  # noqa: E402
from app.shared.job_scope import KAM_MEMBER_ID  # noqa: E402
from app.shared.schemas import VendorApplicationCreate  # noqa: E402

TODAY = date.today()


def _jpeg(seed: int, size=(1400, 933)) -> bytes:
    """Distinct synthetic photo per seed (random blocks, so dHashes differ)."""
    import random
    from PIL import Image, ImageDraw

    rnd = random.Random(seed * 7919)
    im = Image.new("RGB", size, (rnd.randint(60, 200), rnd.randint(60, 200), rnd.randint(60, 200)))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.rectangle([(x, y), (x + rnd.randint(60, 500), y + rnd.randint(60, 400))],
                    fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _availability(**over):
    av = {
        "days_of_week": ["mon", "tue", "wed", "thu", "fri"],
        "time_windows": ["daytime", "early_morning"],
        "weekends": False,
        "earliest_start_date": (TODAY + timedelta(days=7)).isoformat(),
        "shoots_per_week": 6,
        "max_shoots_per_day": 3,
        "notice_hours": 24,
        "has_car": True,
        "has_licence": True,
    }
    av.update(over)
    return av


def _session_paths(n=10, cert=False):
    s = ev.mint_upload_session()
    paths = [ev.sample_path(s, i, "sample", "image/jpeg") for i in range(n)]
    cert_path = ev.sample_path(s, 99, "cert", "image/jpeg") if cert else None
    return s, paths, cert_path


def _application(n_samples=10, drone=False, **over) -> VendorApplicationCreate:
    s, paths, cert = _session_paths(n_samples, cert=drone)
    data = dict(
        name="Alex Rivera",
        email="alex.rivera.example@example.com",
        phone="+1 416 555 0199",
        vendor_type="photographer",
        specialties=["photo"] + (["drone"] if drone else []),
        portfolio_links=[],
        equipment_list=[],
        equipment={"camera_body": "Sony A7 IV", "lenses": "16-35mm f/4, 24-70mm", "tripod": "Manfrotto 190",
                   "lighting": "Godox V1", "drone_model": "DJI Mini 4 Pro" if drone else ""},
        city="Toronto",
        accepted_terms=True,
        hst_exempt=True,
        drone_certified=drone,
        rpas_certificate_level="advanced" if drone else None,
        rpas_certificate_number="PC-12345678" if drone else None,
        sample_upload_session=s,
        sample_paths=paths,
        rpas_cert_path=cert,
        availability=_availability(),
    )
    data.update(over)
    return VendorApplicationCreate(**data)


# ---------------------------------------------------------------------------
# Form validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validate_accepts_full_application():
    from app.routers.onboarding import _validate_application

    app = _application()
    await _validate_application(app)
    assert app.equipment_list[0] == "Sony A7 IV"
    assert app.availability["jobs_per_day"] == 3
    assert app.availability["work_start"] == "06:00" and app.availability["work_end"] == "17:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [0, 7, 16])
async def test_validate_requires_8_to_15_samples(n):
    from app.routers.onboarding import _validate_application

    with pytest.raises(HTTPException) as exc:
        await _validate_application(_application(n_samples=n))
    assert "sample" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_validate_rejects_paths_outside_the_session():
    from app.routers.onboarding import _validate_application

    app = _application()
    app.sample_paths = app.sample_paths[:-1] + ["bricks-go/applicants/someoneelse/sample-01-aaaa.jpg"]
    with pytest.raises(HTTPException) as exc:
        await _validate_application(app)
    assert "through this form" in exc.value.detail


@pytest.mark.asyncio
async def test_validate_rejects_forged_session():
    from app.routers.onboarding import _validate_application

    app = _application()
    exp, rand, _sig = app.sample_upload_session.split(".")
    app.sample_upload_session = f"{exp}.{rand}.{'0' * 24}"
    with pytest.raises(HTTPException):
        await _validate_application(app)


@pytest.mark.asyncio
async def test_drone_claim_needs_level_number_and_cert_photo():
    from app.routers.onboarding import _validate_application

    app = _application(drone=True)
    await _validate_application(app)  # complete claim passes
    app = _application(drone=True, rpas_certificate_level="", rpas_certificate_number="", rpas_cert_path=None)
    with pytest.raises(HTTPException) as exc:
        await _validate_application(app)
    d = exc.value.detail
    assert "level" in d and "certificate number" in d and "photo of your RPAS" in d


@pytest.mark.asyncio
async def test_structured_gear_and_availability_required():
    from app.routers.onboarding import _validate_application

    app = _application(equipment={"camera_body": "", "lenses": "", "tripod": ""}, availability=None)
    with pytest.raises(HTTPException) as exc:
        await _validate_application(app)
    d = exc.value.detail
    assert "camera body" in d and "lenses" in d and "tripod" in d and "availability" in d


def test_upload_session_paths():
    s = ev.mint_upload_session()
    assert ev.verify_upload_session(s)
    p = ev.sample_path(s, 3, "sample", "image/png")
    assert p.startswith("bricks-go/applicants/") and p.endswith(".png")
    assert ev.path_in_session(p, s)
    assert not ev.path_in_session(p.replace("sample-03", "../x"), s)
    other = ev.mint_upload_session()
    assert not ev.path_in_session(p, other)
    assert not ev.verify_upload_session(s, now=int(s.split(".")[0]) + 7200 + 1)


def test_sniff_mime():
    assert ev.sniff_mime(_jpeg(1)) == "image/jpeg"
    assert ev.sniff_mime(b"<html>not an image</html>") is None


@pytest.mark.asyncio
async def test_sample_upload_endpoint_limits(monkeypatch):
    from app.routers.onboarding import mint_sample_uploads
    from app.shared.schemas import SampleUploadRequest

    async def fake_signed(path):
        return {"signed_url": f"https://storage.example/{path}?token=t", "token": "t", "path": path}

    monkeypatch.setattr("app.services.storage.create_signed_upload_url", fake_signed)
    ok = await mint_sample_uploads(SampleUploadRequest(files=[
        {"name": f"{i}.jpg", "type": "image/jpeg", "size": 3_000_000} for i in range(15)
    ] + [{"name": "cert.jpg", "type": "image/jpeg", "size": 500_000, "kind": "rpas_cert"}]))
    assert len(ok["uploads"]) == 16
    assert all(ev.path_in_session(u["path"], ok["session"]) for u in ok["uploads"])
    for bad in (
        [{"name": "x.gif", "type": "image/gif", "size": 2_000_000}],
        [{"name": "x.jpg", "type": "image/jpeg", "size": 40 * 1024 * 1024}],
        [{"name": "x.jpg", "type": "image/jpeg", "size": 2_000}],
        [{"name": f"{i}.jpg", "type": "image/jpeg", "size": 2_000_000} for i in range(16)],
    ):
        with pytest.raises(HTTPException):
            await mint_sample_uploads(SampleUploadRequest(files=bad))


# ---------------------------------------------------------------------------
# Evidence rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("city,expected", [
    ("Toronto", True), ("Mississauga, ON", True), ("Thornhill", True), ("Oshawa", True),
    ("Hamilton", False), ("Türkiye/ toronto", False), ("", None),
])
def test_city_in_gta(city, expected):
    assert ev.city_in_gta(city) is expected


@pytest.mark.parametrize("body,cls", [
    ("Sony A7 IV", "ff"), ("Canon R6 Mark II", "ff"), ("Nikon Z6ii", "ff"), ("Sony a6400", "apsc"),
    ("Fujifilm X-T4", "apsc"), ("Panasonic GH5", "mft"), ("iPhone 15 Pro", "phone"), ("Hasselblad X2D", "medium"),
])
def test_camera_class(body, cls):
    assert ev.camera_class(body) == cls


def test_gear_minimums():
    good = ev.equipment_evidence({"camera_body": "Sony A7 IV", "lenses": "16-35mm", "tripod": "yes"})
    assert ev.equipment_meets_minimum(good)
    crop = ev.equipment_evidence({"camera_body": "Sony a6400", "lenses": "10-18mm", "tripod": "Manfrotto"})
    assert ev.equipment_meets_minimum(crop)
    narrow = ev.equipment_evidence({"camera_body": "Sony a6400", "lenses": "16-50mm kit", "tripod": "Manfrotto"})
    assert not ev.equipment_meets_minimum(narrow)
    phone = ev.equipment_evidence({"camera_body": "iPhone 15", "lenses": "13mm", "tripod": "none"})
    assert not ev.equipment_meets_minimum(phone)
    assert all(b["source"] == ev.SELF_REPORTED for b in good + phone)


def test_rpas_formats():
    assert ev.rpas_number_format("PC-12345678") == "pilot_cert"
    assert ev.rpas_number_format("C-2414849516") == "drone_registration"
    assert ev.rpas_number_format("") == "missing"
    b = ev.rpas_evidence(True, "advanced", "C-2414849516", False)
    assert b["ok"] is False and "drone registration" in b["text"] and "can't be checked online" in b["text"]
    b = ev.rpas_evidence(True, "basic", "PC-12345678", True)
    assert b["ok"] is True and "cert photo attached" in b["text"]
    assert ev.rpas_evidence(False, "", "", False) is None


def test_limited_availability():
    av = ev.normalize_availability(_availability(), today=TODAY)
    assert ev.availability_evidence(av, today=TODAY)["ok"] is True
    low = ev.normalize_availability(_availability(days_of_week=["sat"], weekends=True, time_windows=["evening"],
                                                  shoots_per_week=1, notice_hours=72, has_car=False), today=TODAY)
    out = ev.availability_evidence(low, today=TODAY)
    assert out["ok"] is False and out["limited"] and out["text"].startswith("Limited availability")
    assert out["source"] == ev.SELF_REPORTED
    s = ev.availability_summary(av)
    assert "Mon-Fri" in s and "1 day notice" in s and "car + licence" in s


# ---------------------------------------------------------------------------
# Review engine (vision faked; never calls the network)
# ---------------------------------------------------------------------------
def _vision(grade="pass", real_estate=True, watermark=False):
    async def fake(prompt, jpeg, client=None):
        if "pilot certificate" in prompt:
            return {"is_tc_pilot_certificate": True, "level": "advanced", "certificate_number": "PC-12345678"}, 0.0003
        crit = {k: grade for k in ar.RUBRIC_KEYS}
        return {"is_real_estate": real_estate, "subject": "interior" if real_estate else "not_real_estate",
                "criteria": crit, "watermark": watermark, "watermark_text": "", "looks_stock": False,
                "note": "Clean kitchen frame."}, 0.0006
    return fake


def _inp(n=10, **over):
    blobs = {f"bricks-go/applicants/abc/sample-{i:02d}.jpg": _jpeg(i + 1) for i in range(n)}
    data = dict(member_id="m-1", name="Alex Rivera", city="Toronto",
                equipment={"camera_body": "Sony A7 IV", "lenses": "16-35mm", "tripod": "Manfrotto"},
                sample_paths=list(blobs), availability=ev.normalize_availability(_availability(), today=TODAY))
    data.update(over)
    return ar.ReviewInput(**data), blobs


async def _run(inp, blobs, vision, **kw):
    async def fetch(p):
        return blobs.get(p)

    async def no_links(urls):
        from app.services.portfolio_check import PortfolioCheck
        return PortfolioCheck(())

    kw.setdefault("portfolio_checker", no_links)
    kw.setdefault("page_scraper", AsyncMock(return_value=[]))
    with patch.object(ar, "call_vision", vision), patch.object(ar, "opencv_grades", lambda b: {}):
        r = await ar.run_review(inp, fetch_bytes=fetch, other_hashes=kw.pop("other_hashes", []),
                                use_vision=True, today=TODAY, **kw)
    r.pop("_thumb_bytes", None)
    return r


@pytest.mark.asyncio
async def test_strong_applicant_is_green_recommend():
    inp, blobs = _inp()
    r = await _run(inp, blobs, _vision("pass"))
    assert r["verdict"] == ar.RECOMMEND and r["colour"] == ar.GREEN
    assert r["score"] >= ar.RECOMMEND_MIN_SCORE and not r["incomplete"]
    assert set(r["parts"]) == {"samples", "gear", "availability", "location"}
    assert all(c["status"] == ar.GREEN for c in r["criteria"].values())
    assert len(r["thumbs"]) == 3 and r["thumbs"][-1]["label"] == "Weakest"
    assert r["cost_usd"] == pytest.approx(0.006)
    assert any(b["key"] == "samples" and b["source"] == ev.VERIFIED for b in r["evidence"])


@pytest.mark.asyncio
async def test_no_samples_is_capped_grey_incomplete_never_green():
    inp, _ = _inp(n=0)
    r = await _run(inp, {}, _vision("pass"))
    assert r["needs_samples"] and r["incomplete"]
    assert r["colour"] == ar.GREY and r["verdict"] != ar.RECOMMEND
    assert r["score"] <= ar.INCOMPLETE_SCORE_CAP
    assert r["parts"]["samples"]["status"] == ar.GREY
    assert not r["vision_ran"]
    assert any("sample photos" in m for m in r["missing"])
    s = next(b for b in r["evidence"] if b["key"] == "samples")
    assert s["source"] == ev.COULDNT_CHECK


@pytest.mark.asyncio
async def test_not_real_estate_samples_are_not_a_fit():
    inp, blobs = _inp()
    r = await _run(inp, blobs, _vision("pass", real_estate=False))
    assert r["verdict"] == ar.NOT_A_FIT and r["colour"] == ar.RED


@pytest.mark.asyncio
async def test_weak_photos_score_low():
    inp, blobs = _inp()
    r = await _run(inp, blobs, _vision("fail"))
    assert r["verdict"] == ar.NOT_A_FIT
    assert r["parts"]["samples"]["status"] == ar.RED
    assert all(c["status"] == ar.RED for c in r["criteria"].values())


@pytest.mark.asyncio
async def test_duplicates_across_applicants_flagged():
    inp, blobs = _inp()
    first = ar.prepare_image(next(iter(blobs.values())))
    r = await _run(inp, blobs, _vision("pass"),
                   other_hashes=[{"member_id": "someone-else", "sha256": first["sha256"], "dhash": first["dhash"]}])
    assert any(f["key"] == "duplicate_other" for f in r["flags"])
    assert r["verdict"] == ar.NOT_A_FIT


@pytest.mark.asyncio
async def test_watermark_blocks_recommend():
    inp, blobs = _inp()
    r = await _run(inp, blobs, _vision("pass", watermark=True))
    assert any(f["key"] == "watermark" for f in r["flags"])
    assert r["verdict"] == ar.NEEDS_REVIEW and r["colour"] == ar.YELLOW


@pytest.mark.asyncio
async def test_dead_portfolio_link_is_verified_not_loading():
    from app.services.portfolio_check import LinkCheck, PortfolioCheck

    async def dead(urls):
        return PortfolioCheck(tuple(LinkCheck(u, "failed", "dns") for u in urls))

    inp, blobs = _inp(portfolio_links=["https://abdullah-photos.example"])
    r = await _run(inp, blobs, _vision("pass"), portfolio_checker=dead)
    b = next(x for x in r["evidence"] if x["key"] == "portfolio")
    assert b["source"] == ev.VERIFIED and "doesn't load" in b["text"] and "doesn't resolve" in b["text"]
    assert r["parts"]["portfolio"]["status"] == ar.RED
    assert r["verdict"] != ar.RECOMMEND


@pytest.mark.asyncio
async def test_instagram_is_couldnt_check():
    from app.services.portfolio_check import LinkCheck, PortfolioCheck

    async def ig(urls):
        return PortfolioCheck(tuple(LinkCheck(u, "instagram") for u in urls))

    inp, blobs = _inp(portfolio_links=["https://instagram.com/someone"])
    r = await _run(inp, blobs, _vision("pass"), portfolio_checker=ig)
    b = next(x for x in r["evidence"] if x["key"] == "portfolio")
    assert b["source"] == ev.COULDNT_CHECK and "Instagram" in b["text"]


@pytest.mark.asyncio
async def test_drone_registration_number_flagged_and_weights_renormalise():
    inp, blobs = _inp(drone_claimed=True, rpas_level="advanced", rpas_number="C-2414849516", rpas_cert_path="")
    r = await _run(inp, blobs, _vision("pass"))
    assert r["parts"]["drone"]["status"] == ar.RED
    assert sum(p["max_points"] for p in r["parts"].values()) == pytest.approx(100, abs=0.2)
    assert any("drone registration" in m for m in r["missing"])
    assert r["verdict"] != ar.RECOMMEND


@pytest.mark.asyncio
async def test_unreadable_upload_is_not_reviewed():
    inp, blobs = _inp(n=8)
    k = next(iter(blobs))
    blobs[k] = b"<html>nope</html>" * 100
    r = await _run(inp, blobs, _vision("pass"))
    bad = next(i for i in r["images"] if i["ref"] == k)
    assert bad["error"] == "not_an_image" and not bad["reviewed"]
    assert r["needs_samples"]  # only 7 reviewed


def test_review_input_from_legacy_member():
    m = {"id": "x", "name": "Old Applicant", "home_city": "Türkiye/ toronto", "vendor_type": "photographer",
         "equipment_list": ["Sony A7III", "Sigma 14-24mm", "DJI Mini 3 Pro", "Lightroom"],
         "portfolio_links": ["https://example.com"], "drone_certified": False}
    inp = ar.review_input_from_member(m, None)
    assert not inp.equipment_structured and inp.equipment["camera_body"] == "Sony A7III"
    assert inp.equipment["drone_model"] == "DJI Mini 3 Pro" and inp.equipment["lighting"] == ""
    assert inp.sample_paths == [] and inp.availability is None


# ---------------------------------------------------------------------------
# Owner email / SMS
# ---------------------------------------------------------------------------
async def _review_dict(**kw):
    inp, blobs = _inp(**kw)
    return await _run(inp, blobs, _vision("pass"))


def _render(review, **kw):
    args = dict(member_id="m-1", name="Alex Rivera", city="Toronto", specialties=["photo", "drone"],
                found_via="Instagram", review=review,
                thumb_urls=[{"label": "Best", "url": "https://x.example/a.jpg"},
                            {"label": "Best", "url": "https://x.example/b.jpg"},
                            {"label": "Weakest", "url": "https://x.example/c.jpg"}])
    args.update(kw)
    return mail.render_html(**args), mail.render_text(**args), mail.render_sms(
        member_id="m-1", name="Alex Rivera", city="Toronto", review=review)


@pytest.mark.asyncio
async def test_email_has_score_band_tags_and_no_internal_names():
    r = await _review_dict()
    html, text, sms = _render(r)
    for blob in (html, text, sms):
        low = blob.lower()
        assert "jev" not in low and "gemini" not in low and "openrouter" not in low and "typesafe" not in low
    assert f"{r['score']}<span" in html and "/100" in html
    assert "Recommend" in html and "VERDICT" in html
    assert "VERIFIED" in html and "SELF-REPORTED" in html
    assert "GOOD ·" in html  # part bar has a word label, not colour only
    assert "#4A6B3C" in html  # status green from the design rules
    assert "https://app.brxs.ca/hiring/m-1" in html and "?action=approve" in html
    assert "border-radius:2px" in html and "9999px" not in html and "border-radius:50%" not in html
    assert "Instagram" in html and "Mon-Fri" in html
    assert "<table" in html and "font-family:'General Sans',Helvetica,Arial" in html
    assert text.startswith("Recommend · ") and "[Verified]" in text and "Rubric:" in text
    assert len(sms) <= 200 and "app.brxs.ca/hiring/m-1" in sms


@pytest.mark.asyncio
async def test_incomplete_email_says_capped_and_not_reviewed():
    r = await _review_dict(n=0)
    html, text, sms = _render(r, thumb_urls=[])
    assert "Incomplete" in html and "Capped at 60" in html
    assert "Not run: no sample photos were reviewed." in html
    assert "Needs samples" in html and "Needs samples." in sms
    assert "Strong across the set" not in html


@pytest.mark.asyncio
async def test_runner_notifies_only_kam_and_never_raises(monkeypatch):
    from app.services import applicant_review_runner as runner

    sms = MagicMock(side_effect=RuntimeError("twilio down"))
    email = AsyncMock(side_effect=RuntimeError("resend down"))
    r = await _review_dict()
    member = {"id": "m-1", "name": "Alex Rivera", "home_city": "Toronto", "source": "instagram"}
    with patch("app.notifications.send_dispatch_sms", sms), \
            patch("app.services.notifications.send_email_via_resend", email), \
            patch.object(runner, "_owner_phone", AsyncMock(return_value="+14165550100")), \
            patch.object(runner, "signed_thumb_urls", AsyncMock(return_value=[])):
        await runner.notify_owner(member, r)
    assert sms.call_args.args[0] == "+14165550100"
    kw = email.await_args.kwargs
    assert kw["to"] == "raphael@brxs.ca" and kw["text"] and kw["html"]
    assert "alex.rivera" not in kw["to"]


def test_apply_path_no_longer_calls_the_text_guess():
    src = Path(__file__).resolve().parents[1].joinpath("app/routers/onboarding.py").read_text()
    apply_src = src[src.index("async def apply_to_bricks"):src.index("async def _apply_to_bricks_inner")]
    assert "jev_applicant_poor_fit" not in apply_src
    assert "review_application" in apply_src
    assert "notify_owner_vendor_application" not in apply_src


@pytest.mark.asyncio
async def test_review_backlog_requires_auth_and_never_notifies(monkeypatch):
    from starlette.requests import Request
    from app.routers.onboarding import applicant_review_backlog
    from app.services import applicant_review_runner as runner

    monkeypatch.setenv("CRON_SECRET", "cron-test")
    def req(headers):
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})
    with pytest.raises(HTTPException):
        await applicant_review_backlog(req({}))
    calls = []

    async def fake_review(mid, notify=True, trigger="apply", **kw):
        calls.append((mid, notify, trigger))
        return {"verdict": "needs_review", "score": 20}

    with patch.object(runner, "pending_without_review", AsyncMock(return_value=["a", "b"])), \
            patch.object(runner, "review_application", fake_review):
        out = await applicant_review_backlog(req({"Authorization": "Bearer cron-test"}))
    assert [c[0] for c in calls] == ["a", "b"] and all(c[1] is False and c[2] == "backlog" for c in calls)
    assert out["reviewed"][0]["score"] == 20
