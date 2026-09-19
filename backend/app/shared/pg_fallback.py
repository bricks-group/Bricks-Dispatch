"""Direct Postgres fallback when PostgREST is Fair-Use / 402 blocked.

Mirrors Bricks-OS book_checkout._pg_* helpers. Needs POSTGRES_URL or
POSTGRES_URL_NON_POOLING on the Vercel project (present on bricks-os-api;
add the same secrets to bricks-dispatch-api for this path to activate).
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


def pg_insert_job_assigned_to_owner(job: dict) -> Optional[str]:
    """INSERT job already assigned to Kam (Owner) for GO visibility."""
    conn = _pg_connect()
    if not conn:
        logger.error("pg job fallback skipped: POSTGRES_URL missing")
        return None
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
