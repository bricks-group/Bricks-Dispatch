"""System alerts have one approved work inbox; no mail or SMS is sent here."""
from app.shared.alert_recipients import SYSTEM_ALERT_EMAIL
from app.services import vendor_selection, qc_hold_notify, probation_hold, credential_review_notify, applicant_review_runner, ops_autopilot, editor_review, editor_ledger
from app.routers import dispatch, admin_ops


def test_every_system_alert_uses_approved_inbox(monkeypatch):
    assert SYSTEM_ALERT_EMAIL == "raph@brxs.ca"
    monkeypatch.setenv("ADMIN_NOTIFICATION_EMAIL", "retired@example.invalid")
    monkeypatch.setenv("OPS_ALERT_EMAIL", "retired@example.invalid")
    monkeypatch.setenv("EDITOR_ALERT_EMAIL", "retired@example.invalid")
    assert all(value == SYSTEM_ALERT_EMAIL for value in (
        vendor_selection.OPS_EMAIL, vendor_selection.HOLD_ADMIN_EMAIL,
        qc_hold_notify.QC_HOLD_ADMIN_EMAIL, probation_hold.ADMIN_EMAIL,
        credential_review_notify.CREDENTIAL_ADMIN_EMAIL, applicant_review_runner.OWNER_EMAIL,
        ops_autopilot.kam_email(), editor_review.alert_email(),
        dispatch.ADMIN_NOTIFICATION_EMAIL, admin_ops.ADMIN_NOTIFICATION_EMAIL, editor_ledger.KAM_EMAIL,
    ))
