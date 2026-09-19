"""Direct Postgres fallback when PostgREST is Fair-Use / 402 blocked.

Mirrors Bricks-OS book_checkout._pg_* helpers. Needs POSTGRES_URL or
POSTGRES_URL_NON_POOLING on the Vercel project (present on bricks-os-api;
add the same secrets to bricks-dispatch-api, OR this module will POST the
job to OS /api/internal/create-assigned-job which already has Postgres).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from .job_scope import KAM_MEMBER_ID

logger = logging.getLogger(__name__)

ORG_VENDOR_ID = "default_vendor"


def _is_rest_quota_error(err: object) -> bool:
    """True when PostgREST/Fair-Use blocked the write (Canada Free exceed_storage)."""
    e = str(err or "").lower()
    return any(
        tok in e
        for tok in (
            "402",
            "exceed_storage",
            "storage_size_quota",
            "fair-use",
            "fair use",
            "fair_use",
            "restricted",
            "quota exceeded",
            "over_quota",
        )
    )


def _pg_connect():
    url = os.environ.get("POSTGRES_URL_NON_POOLING") or os.environ.get("POSTGRES_URL")
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    import psycopg2

    conn = psycopg2.connect(url, connect_timeout=10)
    conn.autocommit = True
    return conn




def os_insert_job_assigned_to_owner(job: dict) -> Optional[str]:
    """When local POSTGRES_URL is missing, ask OS (which has it) to INSERT the job."""
    import httpx
    from .auth import get_own_internal_api_key

    os_url = (os.environ.get("BRICKS_OS_API_URL") or "https://api.brxs.ca").strip().rstrip("/")
    key = get_own_internal_api_key()
    if not key:
        logger.error("os job fallback skipped: INTERNAL_API_KEYS missing")
        return None
    payload = dict(job)
    payload["assignee_id"] = KAM_MEMBER_ID
    payload["status"] = "assigned"
    if not payload.get("vendor_id"):
        payload["vendor_id"] = ORG_VENDOR_ID
    try:
        r = httpx.post(
            f"{os_url}/api/internal/create-assigned-job",
            headers={
                "Content-Type": "application/json",
                "X-Internal-Api-Key": key,
            },
            json={"job": payload},
            timeout=20.0,
        )
        if r.status_code >= 400:
            logger.error(
                "os job fallback HTTP %s: %s",
                r.status_code,
                (r.text or "")[:240],
            )
            return None
        body = r.json() if r.content else {}
        jid = body.get("job_id") or payload.get("id")
        logger.warning(
            "os job fallback ok id=%s assignee=%s",
            jid,
            KAM_MEMBER_ID,
        )
        return jid
    except Exception as e:
        logger.error("os job fallback failed: %s", str(e)[:300])
        return None


def pg_insert_job_assigned_to_owner(job: dict) -> Optional[str]:
    """INSERT job already assigned to Kam (Owner) for GO visibility.

    Prefers local POSTGRES_URL; if unset, proxies to OS which already has it.
    """
    conn = _pg_connect()
    if not conn:
        logger.warning("pg job fallback: no POSTGRES_URL — trying OS proxy")
        return os_insert_job_assigned_to_owner(job)
    job = dict(job)
    job["assignee_id"] = KAM_MEMBER_ID
    job["status"] = "assigned"
    if not job.get("vendor_id"):
        job["vendor_id"] = ORG_VENDOR_ID

    cols = [
        "id", "booking_id", "address", "city", "state", "zip_code",
        "lat", "lng", "time_window_start", "time_window_end", "job_type",
        "status", "priority", "property_details", "client_instructions",
        "assignee_id", "vendor_id", "scheduled_date", "agent_name",
        "agent_email", "agent_brokerage", "agent_user_id", "brokerage_id",
        "package_id", "package_name", "package_price", "rack_price",
        "add_ons", "billing_type", "created_at", "updated_at",
        "payout_status", "min_tier_required", "preferred_tier", "vendor_tier",
        "is_rush",
    ]
    vals: list[Any] = []
    for c in cols:
        v = job.get(c)
        if c == "add_ons":
            vals.append(json.dumps(v if v is not None else []))
        else:
            vals.append(v)
    # Soft-drop columns that may not exist; try full then slim.
    attempts = [
        cols,
        [c for c in cols if c not in (
            "agent_user_id", "brokerage_id", "billing_type", "vendor_tier",
            "min_tier_required", "preferred_tier", "is_rush", "payout_status",
        )],
    ]
    try:
        for use_cols in attempts:
            use_vals = [vals[cols.index(c)] for c in use_cols]
            placeholders = ", ".join(["%s"] * len(use_cols))
            sql = (
                f"INSERT INTO public.jobs ({', '.join(use_cols)}, checklist, ai_flags) "
                f"VALUES ({placeholders}, '[]'::jsonb, '[]'::jsonb) "
                f"ON CONFLICT (id) DO NOTHING RETURNING id::text"
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, use_vals)
                    row = cur.fetchone()
                    if row:
                        logger.warning(
                            "pg job fallback ok id=%s assignee=%s",
                            row[0],
                            KAM_MEMBER_ID,
                        )
                        return row[0]
                    return job.get("id")
            except Exception as e:
                err = str(e)
                if "checklist" in err.lower() or "ai_flags" in err.lower():
                    sql2 = (
                        f"INSERT INTO public.jobs ({', '.join(use_cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT (id) DO NOTHING RETURNING id::text"
                    )
                    try:
                        with conn.cursor() as cur:
                            cur.execute(sql2, use_vals)
                            row = cur.fetchone()
                            if row:
                                return row[0]
                            return job.get("id")
                    except Exception as e2:
                        logger.error("pg job fallback slim failed: %s", str(e2)[:300])
                        continue
                logger.error("pg job fallback attempt failed: %s", err[:300])
                continue
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
