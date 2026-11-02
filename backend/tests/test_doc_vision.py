"""Document content check: blank detection, point rules, verdict impact."""
import asyncio
import io
from datetime import date
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

from app.services import doc_vision as dv
from app.services.trade_review import run_trade_review

TODAY = date(2026, 11, 2)


def _png(blank: bool) -> bytes:
    im = Image.new("RGB", (900, 600), "white")
    if not blank:
        d = ImageDraw.Draw(im)
        for i in range(25):
            d.text((40, 20 + i * 22), f"CERTIFICATE OF LIABILITY INSURANCE line {i} Named insured: Test Handy Co", fill="black")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def _pdf(blank: bool) -> bytes:
    if blank:
        return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n" + b" " * 300)
    b = io.BytesIO(); Image.open(io.BytesIO(_png(False))).convert("RGB").save(b, "PDF"); return b.getvalue()


def run(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


def test_blank_detection_local_no_model_call():
    assert dv.image_blank(_png(True)) is True and dv.image_blank(_png(False)) is False
    assert dv.pdf_blank(_pdf(True)) is True and dv.pdf_blank(_pdf(False)) is False
    m = {"name": "Test Handy", "vendor_type": "handyman"}
    with patch.object(dv, "_call", new=AsyncMock(side_effect=AssertionError("no model for blanks"))):
        for data in (_png(True), _pdf(True), b""):
            cc = run(dv.check_document({"doc_type": "coi", "file_url": "x/coi.pdf"}, m, data=data or b"\0", today=TODAY))
            assert cc["overall"] == dv.BLANK, cc


COI_GOOD = {"blank": False, "readable": True, "doc_type_matches": True, "what_it_is": "certificate of liability insurance",
            "insured_name": "Test Handy Co", "coverage_amount_cad": "2,000,000", "expiry_date": "2027-06-30"}


def test_coi_points_verified_and_mismatch():
    kw = dict(names=["Test Handy", "Test Handy Co"], entered_expiry="2027-06-30", min_limit_cents=200000000, today=TODAY)
    o, pts = dv.evaluate("coi", COI_GOOD, **kw)
    assert o == dv.OK and {p["point"] for p in pts} >= {"Insured name", "Coverage amount", "Expiry"}
    o, pts = dv.evaluate("coi", {**COI_GOOD, "coverage_amount_cad": 1000000}, **kw)
    assert o == dv.BAD and any(p["point"] == "Coverage amount" and p["status"] == "mismatch" for p in pts)
    o, pts = dv.evaluate("coi", {**COI_GOOD, "expiry_date": "2027-01-15"}, **kw)
    assert o == dv.BAD and any(p["point"] == "Expiry" and "entered" in p["detail"] for p in pts)
    o, pts = dv.evaluate("coi", {**COI_GOOD, "insured_name": "Someone Else Roofing"}, **kw)
    assert o == dv.BAD
    o, pts = dv.evaluate("coi", {**COI_GOOD, "coverage_amount_cad": None}, **kw)
    assert o == dv.PARTIAL
    o, _ = dv.evaluate("coi", {**COI_GOOD, "doc_type_matches": False, "what_it_is": "a restaurant menu"}, **kw)
    assert o == dv.BAD
    o, _ = dv.evaluate("coi", {"blank": True}, **kw)
    assert o == dv.BLANK


def test_id_and_certs():
    o, pts = dv.evaluate("id_verification", {"readable": True, "doc_type_matches": True, "holder_name": "TEST HANDY",
                                              "has_photo": True, "expiry_date": "2029-01-01", "id_kind": "Ontario driver's licence"},
                         names=["Test Handy"], entered_expiry=None, min_limit_cents=None, today=TODAY)
    assert o == dv.OK
    o, _ = dv.evaluate("id_verification", {"readable": True, "doc_type_matches": True, "holder_name": "Test Handy",
                                            "has_photo": True, "expiry_date": "2025-01-01"},
                       names=["Test Handy"], entered_expiry=None, min_limit_cents=None, today=TODAY)
    assert o == dv.BAD
    o, _ = dv.evaluate("rhi_or_training", {"readable": True, "doc_type_matches": True, "holder_name": "Test Inspector",
                                            "issuer": "Ontario Association of Home Inspectors", "expiry_date": "2027-03-31"},
                       names=["Test Inspector"], entered_expiry="2027-03-31", min_limit_cents=None, today=TODAY)
    assert o == dv.OK
    o, _ = dv.evaluate("police_check", {"readable": True, "doc_type_matches": True, "holder_name": "Test Handy",
                                         "issuer": "Toronto Police Service", "issue_date": "2024-01-01"},
                       names=["Test Handy"], entered_expiry=None, min_limit_cents=None, today=TODAY)
    assert o == dv.BAD


def _member():
    return {"id": "m1", "name": "Test Handy", "vendor_type": "handyman", "phone": "+15555550100", "home_city": "Toronto",
            "application_profile": {"trade": "handyman", "credentials": {"business_name": "Test Handy Co", "years_experience": "6"},
                                    "vetting": {"reviews_link": "https://example.com/r"}}}


def _docs(overall_map):
    base = [("coi", "2027-06-30"), ("before_after", None), ("id_verification", None), ("police_check", None)]
    out = []
    for k, exp in base:
        cc = {"version": dv.VERSION, "overall": overall_map.get(k, "verified"), "points": [{"point": "Content", "status": "verified", "detail": "ok"}]}
        out.append({"doc_type": k, "status": "pending", "expiry_date": exp, "file_url": f"f/{k}", "validation_result": {"content_check": cc}})
    out += [{**out[1]} for _ in range(9)]
    return out


def test_trade_review_blank_docs_never_recommended():
    good = run_trade_review(_member(), _docs({}), today=TODAY)
    assert good["verdict"] == "recommend", good["verdict_reasons"]
    for bad in ("blank", "unreadable", "mismatch"):
        r = run_trade_review(_member(), _docs({"coi": bad, "id_verification": bad}), today=TODAY)
        assert r["verdict"] == "needs_review" and r["score"] <= 50 and r["doc_problem"], (bad, r["score"])
    r = run_trade_review(_member(), _docs({k: "blank" for k in ("coi", "before_after", "id_verification", "police_check")}), today=TODAY)
    assert r["verdict"] != "recommend" and r["score"] <= 50


def test_trade_review_unchecked_docs_not_recommended():
    docs = _docs({})
    for d in docs:
        d["validation_result"] = None
    r = run_trade_review(_member(), docs, today=TODAY)
    assert r["verdict"] == "needs_review" and any("Couldn't verify" in x for x in r["verdict_reasons"])


def test_email_lists_points_verified_vs_unverified():
    from app.services import applicant_email as mail
    r = run_trade_review(_member(), _docs({"coi": "mismatch"}), today=TODAY)
    r["doc_checks"][0]["points"] = [{"point": "Insured name", "status": "verified", "detail": "Test Handy Co"},
                                    {"point": "Coverage amount", "status": "unverified", "detail": "Not visible on the file."}]
    kw = dict(member_id="m1", name="Test Handy", city="Toronto", specialties=[], found_via="", review=r, thumb_urls=[])
    html, text = mail.render_html(**kw), mail.render_text(**kw)
    assert "Document check" in html and "VERIFIED" in html and "UNVERIFIED" in html
    assert "Verified: Insured name" in text and "Unverified: Coverage amount" in text


def test_check_member_docs_stores_and_caches():
    class T:
        def __init__(s): s.updates = []
        def table(s, n): s.n = n; return s
        def update(s, v): s.updates.append(v); return s
        def eq(s, *a): return s
        async def execute(s): return None
    db = T()
    docs = [{"id": "d1", "doc_type": "coi", "file_url": "f/coi.pdf", "expiry_date": "2027-06-30", "status": "pending"}]
    with patch.object(dv, "_fetch", new=AsyncMock(return_value=_pdf(False))), \
         patch.object(dv, "_call", new=AsyncMock(return_value=(COI_GOOD, 0.0004))), \
         patch("app.services.applicant_review.vision_configured", return_value=True):
        out = run(dv.check_member_docs(db, _member(), docs))
        assert out[0]["validation_result"]["content_check"]["overall"] == dv.OK and len(db.updates) == 1
        run(dv.check_member_docs(db, _member(), out))  # cached
        assert len(db.updates) == 1
