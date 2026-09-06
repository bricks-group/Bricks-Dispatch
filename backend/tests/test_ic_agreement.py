"""IC agreement served by GET /api/onboarding/ic-agreement must be v1.5 verbatim."""
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


def test_ic_agreement_constants_are_v15():
    version = _assignment_value(ONBOARDING, "_IC_AGREEMENT_VERSION")
    effective = _assignment_value(ONBOARDING, "_IC_AGREEMENT_EFFECTIVE_DATE")
    text = _assignment_value(ONBOARDING, "_IC_AGREEMENT_TEXT")

    assert version == "1.5"
    assert effective == "2026-09-06"
    assert text.startswith("BRICKS INDEPENDENT CONTRACTOR AGREEMENT  v1.5")
    assert "Effective: September 6, 2026" in text
    assert 'Contracting party: Bricks Group ("Bricks")' in text
    assert "Questions: booking@brxs.ca" in text
    assert text.endswith("Bricks Group · Ontario, Canada")

    assert "INSURANCE AND WSIB (REQUIRED BEFORE FIRST JOB)" in text
    assert "WSIB Certificate of Clearance" in text
    assert "Binding arbitration is not required." in text
    assert "fixed amount per accepted booking" in text
    assert "fixed published dollar amounts from the add-on rate card" in text
    assert "Tier C 35%, Tier B 45%, Tier A 55%" not in text
    assert "percentage of rack" in text
    assert "35%" not in text and "45%" not in text and "55%" not in text
    assert "18. CHANGES; ENTIRE AGREEMENT" in text
    assert "Photos only / Essentials" in text
    assert "Story / Showcase" in text
    assert "Full suite" in text
    assert "Virtual Visit (free-roam Gaussian splat) is always a paid add-on" in text
    assert "Standard —" not in text
    assert "Premium —" not in text
    assert "Luxury —" not in text

    assert "BRICKS DOES NOT REQUIRE PROOF OF INSURANCE" not in text
    assert "Bricks retains a platform fee from each job value" not in text
    assert "submitted to binding arbitration in Toronto" not in text
    assert "two (2) weeks for every year" not in text
    assert "legal@" not in text.lower()


def test_get_ic_agreement_returns_version_date_text():
    src = ONBOARDING.read_text()
    assert '"version": _IC_AGREEMENT_VERSION' in src
    assert '"effective_date": _IC_AGREEMENT_EFFECTIVE_DATE' in src
    assert '"text": _IC_AGREEMENT_TEXT' in src
    assert "ic_agreement_version=_IC_AGREEMENT_VERSION" in src


def test_application_schema_defaults_to_v15():
    src = SCHEMAS.read_text()
    assert 'ic_agreement_version: str = "1.5"' in src
    assert 'ic_agreement_version: str = "1.1"' not in src
