"""IC agreement served by GET /api/onboarding/ic-agreement must be v1.9 verbatim.

v1.9 (2026-10-23): same terms as v1.8, one contact (booking@brxs.ca) for payout
disputes and questions. The v1.8 text stays in docs/legal/ for existing signers.

v1.8 Clause 6: insurance/WSIB are not required for apply or first dispatch,
and missing/lapsed coverage is not suspension. Matches dispatch eligibility
(no has_lapsed_insurance gate).
"""
import ast
from pathlib import Path

ONBOARDING = Path(__file__).resolve().parents[1] / "app" / "routers" / "onboarding.py"
SCHEMAS = Path(__file__).resolve().parents[1] / "app" / "shared" / "schemas.py"


def _assignment_value(path: Path, name: str):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr == "strip"
                    ):
                        return ast.literal_eval(value.func.value).strip()
                    return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in {path}")


def test_ic_agreement_constants_are_v19():
    version = _assignment_value(ONBOARDING, "_IC_AGREEMENT_VERSION")
    effective = _assignment_value(ONBOARDING, "_IC_AGREEMENT_EFFECTIVE_DATE")
    text = _assignment_value(ONBOARDING, "_IC_AGREEMENT_TEXT")

    assert version == "1.9"
    assert effective == "2026-10-23"
    assert text.startswith("BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.9")
    assert "Effective: October 23, 2026" in text
    assert 'Contracting party: Bricks Group ("Bricks")' in text
    assert "Questions: booking@brxs.ca" in text
    assert "Raise payout disputes in writing to booking@brxs.ca" in text
    assert "raphael@" not in text
    assert text.endswith("Bricks Group · Ontario, Canada")

    # Clause 6 honesty — not a dispatch gate (PR #26 / IC v1.8).
    assert "6. INSURANCE AND WSIB" in text
    assert "INSURANCE AND WSIB (REQUIRED BEFORE FIRST JOB)" not in text
    assert "not required to apply" in text
    assert "not required before a first dispatched job" in text
    assert "not suspension, refusal, or cancellation of dispatch" in text
    assert "Binding arbitration is not required." in text
    assert "fixed amount per accepted booking" in text
    assert "published add-on rate card for your tier — fixed dollars" in text
    assert "Tier C 35%, Tier B 45%, Tier A 55%" not in text
    assert "percentage of rack" in text
    assert "35%" not in text and "45%" not in text and "55%" not in text
    assert "18. CHANGES; ENTIRE AGREEMENT" in text
    assert "Photos only —" in text
    assert "Story —" in text
    assert "Showcase —" in text
    assert "Essentials —" in text
    assert "Full suite" in text
    assert "Legacy Estate" not in text
    assert "$55.15" in text
    assert "Virtual Visit (free-roam Gaussian splat) is always a paid add-on" in text
    assert "standard 24–48 hour target from shoot end" in text
    assert "times are targets, not guarantees" in text
    assert "standard 24 hours from shoot end" not in text
    assert "Standard —" not in text
    assert "Premium —" not in text
    assert "Luxury —" not in text

    assert "Bricks retains a platform fee from each job value" not in text
    assert "submitted to binding arbitration in Toronto" not in text
    assert "two (2) weeks for every year" not in text
    assert "legal@" not in text.lower()
    assert "trip fee" not in text.lower()
    assert "$60" not in text


def test_get_ic_agreement_returns_version_date_text():
    src = ONBOARDING.read_text()
    assert '"version": _IC_AGREEMENT_VERSION' in src
    assert '"effective_date": _IC_AGREEMENT_EFFECTIVE_DATE' in src
    assert '"text": _IC_AGREEMENT_TEXT' in src
    assert "ic_agreement_version=_IC_AGREEMENT_VERSION" in src


def test_application_schema_defaults_to_v19():
    src = SCHEMAS.read_text()
    assert 'ic_agreement_version: str = "1.9"' in src
    assert 'ic_agreement_version: str = "1.8"' not in src
    assert 'ic_agreement_version: str = "1.7"' not in src
    assert 'ic_agreement_version: str = "1.1"' not in src


def test_v18_text_kept_for_existing_signers():
    legal = ONBOARDING.parents[3] / "docs" / "legal"
    v18 = (legal / "ic-agreement-v1.8.txt").read_text()
    live = _assignment_value(ONBOARDING, "_IC_AGREEMENT_TEXT")
    assert v18.startswith("BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.8")
    assert "Effective: October 3, 2026" in v18
    # Between v1.8 and v1.9 only the header changes, plus the 2026-10-30
    # removal of the no-show trip fee sentence (Kam ruling; no version bump —
    # counsel owns the legal text). docs/legal v1.8 stays as signed.
    trip_fee = (
        " Inaccessible property or a client no-show reported in-app may qualify"
        " for the published trip fee then in effect."
    )
    strip = lambda t: "\n".join(t.strip().splitlines()[2:])
    assert trip_fee in v18
    assert strip(v18).replace(trip_fee, "") == strip(live)
    assert (legal / "ic-agreement-v1.8-edited-2026-10-23.txt").exists()
