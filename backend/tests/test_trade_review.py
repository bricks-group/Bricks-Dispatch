"""Rules rubric for non-photographer trades and the trade-led owner email."""
from datetime import date

from app.services import applicant_email as mail
from app.services.trade_review import run_trade_review

TODAY = date(2026, 11, 2)


def _member(**kw):
    m = {"id": "m1", "vendor_type": "handyman", "name": "Test Applicant", "phone": "4165550000",
         "home_city": "Toronto", "hst_number": "123456789RT0001",
         "application_profile": {"trade": "handyman",
                                 "credentials": {"years_experience": "6", "business_name": "Test Co"},
                                 "vetting": {"reviews_link": "https://g.page/test"}}}
    m.update(kw)
    return m


def _all_docs():
    return [
        {"doc_type": "coi", "status": "verified", "expiry_date": "2027-06-01"},
        {"doc_type": "id_verification", "status": "verified"},
        {"doc_type": "police_check", "status": "verified"},
    ] + [{"doc_type": "before_after", "status": "pending"} for _ in range(10)]


def test_complete_handyman_recommends_with_trade_weights():
    r = run_trade_review(_member(), _all_docs(), TODAY)
    assert r["trade"] == "handyman" and r["rubric_version"].startswith("trade-rules")
    assert set(r["parts"]) == {"proof", "credentials", "identity", "experience", "completeness"}
    assert r["parts"]["credentials"]["max_points"] == 30
    assert r["verdict"] == "recommend", r
    assert not r["incomplete"] and r["score"] >= 75


def test_missing_documents_caps_score_and_lists_them():
    r = run_trade_review(_member(), [], TODAY)
    assert r["incomplete"] and r["score"] <= 60
    assert r["verdict"] == "needs_review" and r["colour"] == "grey"
    assert any("liability" in m.lower() for m in r["missing"])


def test_expired_coi_flags_and_blocks_recommend():
    docs = _all_docs()
    docs[0] = {"doc_type": "coi", "status": "verified", "expiry_date": "2026-10-01"}
    r = run_trade_review(_member(), docs, TODAY)
    assert r["verdict"] != "recommend"
    assert r["flags"] and "expired" in r["flags"][0]["text"]


def test_no_reviews_or_refs_is_missing_for_occupied_home_trade():
    m = _member()
    m["application_profile"]["vetting"] = {}
    r = run_trade_review(m, _all_docs(), TODAY)
    assert "a reviews link or two references" in r["missing"]


def test_subject_leads_with_trade_for_non_photographers_only():
    r = run_trade_review(_member(), _all_docs(), TODAY)
    s = mail.subject("Test Applicant", r)
    assert s.startswith(r["trade_label"] + " · ") and s.endswith("Test Applicant applied to Bricks")
    photo = {"score": 80, "colour": "green", "verdict": "recommend", "trade": "photographer"}
    assert not mail.subject("P", photo).startswith("Photographer")


def test_email_renders_trade_parts_and_rubric_note():
    r = run_trade_review(_member(), [], TODAY)
    kw = dict(member_id="m1", name="Test Applicant", city="Toronto", specialties=["handyman"],
              found_via="", review=r, thumb_urls=[])
    html, text = mail.render_html(**kw), mail.render_text(**kw)
    assert "Proof of work" in html and "Credentials" in text
    assert "until required documents are in" in text
    assert "no sample photos" not in text.lower()


def test_partial_proof_gets_partial_credit():
    docs = [d for d in _all_docs() if d["doc_type"] != "before_after"] + [{"doc_type": "before_after", "status": "pending"}] * 5
    r = run_trade_review(_member(), docs, TODAY)
    assert r["parts"]["proof"]["value"] == 0.5 and r["incomplete"]


def test_backlog_picks_up_trades_after_30_minutes():
    from datetime import datetime, timezone
    from app.services.applicant_review_runner import _backlog_due
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    assert _backlog_due({"created_at": "2026-11-02T14:59:00+00:00"}, "photographer", now)
    assert not _backlog_due({"created_at": "2026-11-02T14:45:00+00:00"}, "cleaner", now)
    assert _backlog_due({"created_at": "2026-11-02T14:20:00Z"}, "cleaner", now)
    assert not _backlog_due({"created_at": ""}, "stager", now)
