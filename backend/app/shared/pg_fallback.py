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
    # Prefer pooler (POSTGRES_URL). Vercel cannot dial IPv6 db.*.supabase.co.
    url = os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_URL_NON_POOLING")
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        conn.autocommit = True
        return conn
    except Exception as e:
        logger.warning("pg connect failed: %s", type(e).__name__)
        return None




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


def _os_internal_post(path: str, payload: dict) -> Optional[dict]:
    """POST JSON to Bricks-OS internal Fair-Use helper. Returns parsed body or None."""
    import httpx
    from .auth import get_own_internal_api_key

    os_url = (os.environ.get("BRICKS_OS_API_URL") or "https://api.brxs.ca").strip().rstrip("/")
    key = get_own_internal_api_key()
    if not key:
        logger.error("os internal %s skipped: INTERNAL_API_KEYS missing", path)
        return None
    try:
        r = httpx.post(
            f"{os_url}{path}",
            headers={
                "Content-Type": "application/json",
                "X-Internal-Api-Key": key,
            },
            json=payload,
            timeout=20.0,
        )
        if r.status_code >= 400:
            logger.error(
                "os internal %s HTTP %s: %s",
                path,
                r.status_code,
                (r.text or "")[:240],
            )
            return None
        return r.json() if r.content else {}
    except Exception as e:
        logger.error("os internal %s failed: %s", path, str(e)[:300])
        return None


def _row_from_cursor(cur) -> Optional[dict]:
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    out = {}
    for i, c in enumerate(cols):
        v = row[i]
        if hasattr(v, "isoformat"):
            try:
                v = v.isoformat()
            except Exception:
                v = str(v)
        out[c] = v
    return out


def _rows_from_cursor(cur) -> list[dict]:
    cols = [d[0] for d in cur.description] if cur.description else []
    out = []
    for row in cur.fetchall() or []:
        d = {}
        for i, c in enumerate(cols):
            v = row[i]
            if hasattr(v, "isoformat"):
                try:
                    v = v.isoformat()
                except Exception:
                    v = str(v)
            d[c] = v
        out.append(d)
    return out


def pg_select_vendor_member(actor_id: str = "", email: str = "") -> Optional[dict]:
    """Load vendor_members via Postgres (or OS proxy) when PostgREST is 402."""
    from .job_scope import KAM_EMAILS, KAM_MEMBER_ID, pick_canonical_member

    actor_id = (actor_id or "").strip()
    email_l = (email or "").strip().lower()
    conn = _pg_connect()
    if not conn:
        body = _os_internal_post(
            "/api/internal/vendor-member",
            {"actor_id": actor_id, "email": email_l},
        )
        if not body:
            return None
        member = body.get("member")
        return member if isinstance(member, dict) else None

    rows: list[dict] = []
    try:
        with conn.cursor() as cur:
            if actor_id:
                cur.execute("SELECT * FROM public.vendor_members WHERE id = %s LIMIT 1", (actor_id,))
                row = _row_from_cursor(cur)
                if row:
                    rows.append(row)
                # Also match auth_user_id when JWT sub != member id
                cur.execute(
                    "SELECT * FROM public.vendor_members WHERE auth_user_id::text = %s LIMIT 5",
                    (actor_id,),
                )
                for r in _rows_from_cursor(cur):
                    if r not in rows and str(r.get("id")) not in {str(x.get("id")) for x in rows}:
                        rows.append(r)
            if actor_id.lower().startswith("0c3fa7c2") or email_l in KAM_EMAILS:
                cur.execute(
                    "SELECT * FROM public.vendor_members WHERE id = %s LIMIT 1",
                    (KAM_MEMBER_ID,),
                )
                row = _row_from_cursor(cur)
                if row and str(row.get("id")) not in {str(x.get("id")) for x in rows}:
                    rows.append(row)
            if email_l:
                cur.execute(
                    "SELECT * FROM public.vendor_members WHERE lower(email) = %s LIMIT 10",
                    (email_l,),
                )
                for r in _rows_from_cursor(cur):
                    if str(r.get("id")) not in {str(x.get("id")) for x in rows}:
                        rows.append(r)
        picked = pick_canonical_member(rows, actor_id, email_l)
        if picked:
            logger.warning("pg vendor_member fallback ok id=%s", picked.get("id"))
        return picked
    except Exception as e:
        logger.error("pg vendor_member select failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_jobs_for_assignees(
    assignee_ids: list[str],
    *,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """List jobs for photographer aliases via Postgres (or OS proxy)."""
    ids = [str(a).strip() for a in (assignee_ids or []) if str(a).strip()]
    if not ids:
        return [], 0
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    conn = _pg_connect()
    if not conn:
        body = _os_internal_post(
            "/api/internal/list-jobs-for-assignees",
            {
                "assignee_ids": ids,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        )
        if not body:
            return [], 0
        jobs = body.get("jobs") if isinstance(body.get("jobs"), list) else []
        total = int(body.get("total") or len(jobs))
        return jobs, total

    try:
        with conn.cursor() as cur:
            params: list[Any] = [ids]
            where = "assignee_id = ANY(%s)"
            if status:
                where += " AND status = %s"
                params.append(status)
            cur.execute(
                f"SELECT count(*) FROM public.jobs WHERE {where}",
                params,
            )
            total = int((cur.fetchone() or [0])[0] or 0)
            params2 = list(params) + [limit, offset]
            cur.execute(
                f"SELECT * FROM public.jobs WHERE {where} "
                f"ORDER BY scheduled_date ASC NULLS LAST, time_window_start ASC NULLS LAST "
                f"LIMIT %s OFFSET %s",
                params2,
            )
            jobs = _rows_from_cursor(cur)
            # Normalize add_ons JSON if returned as str
            for j in jobs:
                ao = j.get("add_ons")
                if isinstance(ao, str):
                    try:
                        j["add_ons"] = json.loads(ao)
                    except Exception:
                        pass
            logger.warning(
                "pg jobs list fallback ok n=%s total=%s assignees=%s",
                len(jobs),
                total,
                ids[:3],
            )
            return jobs, total
    except Exception as e:
        logger.error("pg jobs list failed: %s", str(e)[:300])
        return [], 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_job_by_id(job_id: str) -> Optional[dict]:
    """Fetch one job by id via Postgres or OS proxy."""
    jid = str(job_id or "").strip()
    if not jid:
        return None
    conn = _pg_connect()
    if not conn:
        body = _os_internal_post(
            "/api/internal/list-jobs-for-assignees",
            {"assignee_ids": [KAM_MEMBER_ID], "limit": 200, "offset": 0, "job_id": jid},
        )
        # Prefer dedicated job_id filter when OS supports it; else scan.
        if body and isinstance(body.get("job"), dict):
            return body["job"]
        jobs = body.get("jobs") if body and isinstance(body.get("jobs"), list) else []
        return next((j for j in jobs if str(j.get("id")) == jid), None)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.jobs WHERE id = %s LIMIT 1", (jid,))
            row = _row_from_cursor(cur)
            if row and isinstance(row.get("add_ons"), str):
                try:
                    row["add_ons"] = json.loads(row["add_ons"])
                except Exception:
                    pass
            return row
    except Exception as e:
        logger.error("pg job by id failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
