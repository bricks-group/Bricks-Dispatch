"""vendor_members rows with NULL text/list columns must still parse.

Auto-assign builds VendorMember(**row) and skips rows that fail validation,
so a NULL title (signup-created members) silently removed a fully onboarded
vendor from the pool.
"""
from app.shared.schemas import VendorMember


def _row(**over):
    row = {
        "id": "e2e7e570-0000-4000-8000-0000000e2e01",
        "vendor_id": "default_vendor",
        "name": "TEST Vendor",
        "email": "test@example.com",
        "title": None,
        "phone": None,
        "home_city": None,
        "internal_notes": None,
        "skills": None,
        "service_areas": None,
        "is_active": True,
        "onboarding_status": "approved",
        "hst_number": None,
        "home_lat": 48.38,
        "home_lng": -89.25,
    }
    row.update(over)
    return row


def test_null_non_optional_columns_take_defaults():
    v = VendorMember(**_row())
    assert v.title == "Photographer IC"
    assert v.phone == ""
    assert v.home_city == ""
    assert v.skills == []
    assert v.service_areas == []


def test_optional_columns_keep_none():
    v = VendorMember(**_row())
    assert v.hst_number is None


def test_real_values_untouched():
    v = VendorMember(**_row(title="Lead", phone="555"))
    assert v.title == "Lead" and v.phone == "555"
