"""Owner can manually assign / reassign default_vendor jobs."""
from app.shared.job_scope import (
    KAM_MEMBER_ID,
    ORG_VENDOR_ID,
    actor_can_assign,
    assignment_fields,
    is_manual_assignable,
    is_unassigned_assignee,
    job_visible_to,
    jobs_or_filter,
    resolve_manual_assignment,
    vendor_scope_ids,
)

KAM = {
    "id": KAM_MEMBER_ID,
    "vendor_id": KAM_MEMBER_ID,  # historical: member UUID used as vendor_id
    "role": "Owner",
    "title": "Owner",
    "email": "akashorter@icloud.com",
    "payout_mode": "owner_full",
    "is_active": True,
    "onboarding_status": "approved",
}

IC = {
    "id": "ic-active-1",
    "vendor_id": ORG_VENDOR_ID,
    "role": "Photographer IC",
    "title": "Photographer IC",
    "email": "ic@brxs.ca",
    "is_active": True,
    "onboarding_status": "approved",
}

AMAN = {
    "id": "aman-pending",
    "vendor_id": ORG_VENDOR_ID,
    "role": "Photographer IC",
    "email": "aman@test.com",
    "is_active": True,
    "onboarding_status": "pending",
}

INACTIVE = {
    "id": "inactive-1",
    "vendor_id": ORG_VENDOR_ID,
    "role": "Photographer IC",
    "is_active": False,
    "onboarding_status": "approved",
}

UNASSIGNED_JOB = {
    "id": "job-unassigned-book",
    "vendor_id": ORG_VENDOR_ID,
    "assignee_id": "",
    "status": "pending_assignment",
    "address": "10 Main St, Mississauga",
}


def test_owner_can_assign():
    assert actor_can_assign(KAM)
    assert not actor_can_assign(IC)


def test_manual_assignable_includes_kam_excludes_pending():
    assert is_manual_assignable(KAM)
    assert is_manual_assignable(IC)
    assert not is_manual_assignable(AMAN)
    assert not is_manual_assignable(INACTIVE)


def test_unassigned_default_vendor_job_listable_for_owner():
    vendors = vendor_scope_ids(KAM_MEMBER_ID, KAM)
    assert ORG_VENDOR_ID in vendors
    filt = jobs_or_filter(vendors, [KAM_MEMBER_ID])
    assert f"vendor_id.eq.{ORG_VENDOR_ID}" in filt
    assert job_visible_to(UNASSIGNED_JOB, KAM_MEMBER_ID, KAM, assigned_only=False, admin_all=True)
    assert not job_visible_to(UNASSIGNED_JOB, KAM_MEMBER_ID, KAM, assigned_only=True, admin_all=False)


def test_owner_assigns_default_vendor_job_to_self():
    code, err, patch = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=UNASSIGNED_JOB,
        member_id=KAM_MEMBER_ID,
        target_member=KAM,
    )
    assert code == 200
    assert err == "ok"
    assert patch["assignee_id"] == KAM_MEMBER_ID
    assert patch["status"] == "assigned"


def test_owner_assigns_default_vendor_job_to_active_ic():
    code, err, patch = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=UNASSIGNED_JOB,
        member_id=IC["id"],
        target_member=IC,
    )
    assert code == 200
    assert patch["assignee_id"] == IC["id"]
    assert patch["status"] == "assigned"


def test_owner_reassign_keeps_in_progress():
    job = {**UNASSIGNED_JOB, "assignee_id": KAM_MEMBER_ID, "status": "in_progress"}
    code, _, patch = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=job,
        member_id=IC["id"],
        target_member=IC,
    )
    assert code == 200
    assert patch["assignee_id"] == IC["id"]
    assert patch["status"] == "in_progress"


def test_owner_unassign():
    job = {**UNASSIGNED_JOB, "assignee_id": KAM_MEMBER_ID, "status": "assigned"}
    code, _, patch = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=job,
        member_id="unassign",
        target_member=None,
    )
    assert code == 200
    assert patch["assignee_id"] is None
    assert patch["status"] == "pending_assignment"


def test_missing_job_404():
    code, err, _ = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=None,
        member_id=KAM_MEMBER_ID,
        target_member=KAM,
    )
    assert code == 404
    assert err == "job_not_found"


def test_non_admin_ic_403():
    code, err, _ = resolve_manual_assignment(
        actor_id=IC["id"],
        actor_member=IC,
        job=UNASSIGNED_JOB,
        member_id=IC["id"],
        target_member=IC,
    )
    assert code == 403
    assert err == "insufficient_permissions"


def test_pending_aman_rejected():
    code, err, _ = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=UNASSIGNED_JOB,
        member_id=AMAN["id"],
        target_member=AMAN,
    )
    assert code == 404
    assert err == "member_not_found"


def test_inactive_member_rejected():
    code, err, _ = resolve_manual_assignment(
        actor_id=KAM_MEMBER_ID,
        actor_member=KAM,
        job=UNASSIGNED_JOB,
        member_id=INACTIVE["id"],
        target_member=INACTIVE,
    )
    assert code == 404
    assert err == "member_not_found"


def test_empty_assignee_is_unassigned():
    assert is_unassigned_assignee("")
    assert is_unassigned_assignee(None)
    assert is_unassigned_assignee("default_user")
    assert not is_unassigned_assignee(KAM_MEMBER_ID)


def test_assignment_fields_promotes_pending():
    patch = assignment_fields(UNASSIGNED_JOB, KAM_MEMBER_ID, now="t")
    assert patch == {"assignee_id": KAM_MEMBER_ID, "status": "assigned", "updated_at": "t"}
