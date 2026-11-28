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


def _pg_dsn_candidates() -> list:
    """Ordered DSNs: env URLs, then pooler rewrites for Vercel IPv4."""
    from urllib.parse import quote, urlparse, urlunparse

    raw: list = []
    for key in ("POSTGRES_URL", "POSTGRES_URL_NON_POOLING"):
        u = (os.environ.get(key) or "").strip()
        if u and u not in raw:
            raw.append(u)
    out: list = []
    for u in raw:
        if u not in out:
            out.append(u)
        try:
            p = urlparse(u)
            host = (p.hostname or "").lower()
            if not (host.startswith("db.") and host.endswith(".supabase.co")):
                continue
            ref = host[len("db.") : -len(".supabase.co")]
            if not ref or not p.password:
                continue
            region = (os.environ.get("SUPABASE_REGION") or "ca-central-1").strip()
            user = p.username or "postgres"
            users = []
            if not user.startswith("postgres."):
                users.append(f"postgres.{ref}")
            users.append(user)
            path = p.path or "/postgres"
            for pool_host in (
                f"aws-0-{region}.pooler.supabase.com",
                f"aws-1-{region}.pooler.supabase.com",
            ):
                for uname in users:
                    netloc = f"{quote(uname, safe='')}:{quote(p.password, safe='')}@{pool_host}:6543"
                    dsn = urlunparse((p.scheme or "postgresql", netloc, path, "", p.query, ""))
                    if dsn not in out:
                        out.append(dsn)
        except Exception:
            continue
    return out


def _pg_connect():
    try:
        import psycopg2
    except Exception as e:
        logger.warning("pg connect failed: %s", type(e).__name__)
        return None
    last_err = None
    for url in _pg_dsn_candidates():
        try:
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://") :]
            conn = psycopg2.connect(url, connect_timeout=8)
            conn.autocommit = True
            return conn
        except Exception as e:
            last_err = e
            try:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or "?"
            except Exception:
                host = "?"
            logger.warning("pg connect failed host=%s err=%s", host, type(e).__name__)
            continue
    if last_err is not None:
        logger.warning("pg connect exhausted: %s", type(last_err).__name__)
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
    # Assign-first: keep checkout/intake assignee when present; else Owner (Kam).
    aid = (job.get("assignee_id") or "").strip()
    if not aid or aid == "default_user":
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
        "subtotal_cents", "tax_cents", "total_cents", "tax_rate", "tax_province", "tax_jurisdiction", "price_basis",
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
            if actor_id.lower() == KAM_MEMBER_ID or email_l in KAM_EMAILS:
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
    limit = max(1, min(int(limit or 50), 500))
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


def pg_select_occupied_jobs_by_assignees(
    assignee_ids: list[str],
    scheduled_date: Optional[str] = None,
) -> dict[str, list[dict]]:
    """Jobs on assignees' books for auto-assign conflict/travel (Fair-Use PG path).

    Groups by assignee_id. Returns {} on connect/query failure so callers can soft-fail.
    """
    ids = [str(a).strip() for a in (assignee_ids or []) if str(a).strip()][:80]
    if not ids:
        return {}
    conn = _pg_connect()
    if not conn:
        return {}

    cols_full = (
        "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
        "confirmed_arrive_at, selected_slot_start, selected_slot_end, "
        "estimated_duration_min, lat, lng, status, address, city"
    )
    cols_slim = (
        "id, assignee_id, scheduled_date, time_window_start, time_window_end, "
        "lat, lng, status, address, city"
    )
    statuses = ["assigned", "in_progress", "pending_assignment", "completed"]
    date_s = str(scheduled_date)[:10] if scheduled_date else None

    def _query(cur, col_sql: str) -> list[dict]:
        params: list[Any] = [ids, statuses]
        where = "assignee_id = ANY(%s) AND status = ANY(%s)"
        if date_s:
            where += " AND scheduled_date = %s"
            params.append(date_s)
        cur.execute(
            f"SELECT {col_sql} FROM public.jobs WHERE {where} LIMIT 400",
            params,
        )
        return _rows_from_cursor(cur)

    try:
        with conn.cursor() as cur:
            try:
                rows = _query(cur, cols_full)
            except Exception as col_err:
                logger.warning(
                    "pg occupied jobs full cols failed, retry slim: %s",
                    str(col_err)[:200],
                )
                try:
                    conn.rollback()
                except Exception:
                    pass
                rows = _query(cur, cols_slim)
            out: dict[str, list[dict]] = {}
            for row in rows:
                aid = row.get("assignee_id")
                if aid:
                    out.setdefault(str(aid), []).append(row)
            logger.warning(
                "pg occupied jobs fallback ok n=%s assignees=%s date=%s",
                sum(len(v) for v in out.values()),
                ids[:3],
                date_s,
            )
            return out
    except Exception as e:
        logger.error("pg occupied jobs failed: %s", str(e)[:300])
        return {}
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
            if row:
                for jk in ("add_ons", "checklist", "ai_flags", "property_details"):
                    if isinstance(row.get(jk), str):
                        try:
                            row[jk] = json.loads(row[jk])
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



def pg_select_vendor_members_by_vendor_ids(vendor_ids: list[str]) -> list[dict]:
    """Load vendor_members for one or more vendor_ids via Postgres when PostgREST is 402."""
    ids = [str(v).strip() for v in (vendor_ids or []) if str(v).strip()]
    if not ids:
        return []
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_vendor_members_by_vendor_ids: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.vendor_members WHERE vendor_id = ANY(%s)",
                (ids,),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg_select_vendor_members_by_vendor_ids ok n=%s vendor_ids=%s",
                len(rows),
                ids[:5],
            )
            return rows
    except Exception as e:
        logger.error("pg_select_vendor_members_by_vendor_ids failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_update_job(
    job_id: str,
    fields: dict,
    *,
    expected_status: Optional[str] = None,
    only_if_null: Optional[str] = None,
) -> bool:
    """UPDATE public.jobs SET … via Postgres when PostgREST is Fair-Use 402.

    Whitelist covers capture compile deliver paths: updated_at freshness,
    checklist marks, status/arrive fields, and uploaded_count for stills
    registration under Fair Use. Returns True when at least one row was updated.
    With expected_status, only a row still in that status is updated.
    With only_if_null (a whitelisted column name), the row is updated only when
    that column is still NULL — used to stamp vendor_accepted_at once.
    """
    jid = str(job_id or "").strip()
    if not jid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {
        "updated_at",
        "checklist",
        "status",
        "confirmed_arrive_at",
        "time_window_start",
        "time_window_end",
        "preferred_window",
        "uploaded_count",
        "assignee_id",
        "estimated_duration_min",
        "selected_slot_start",
        "selected_slot_end",
        "payout_amount",
        "payout_status",
        # Client no-show evidence (noshow_policy)
        "checked_in_at",
        "check_in_lat",
        "check_in_lng",
        "check_in_accuracy_m",
        "check_in_distance_m",
        "noshow_photo_path",
        "noshow_photo_taken_at",
        "noshow_photo_uploaded_at",
        "noshow_call_attempted_at",
        "noshow_call_method",
        # Termination-policy acceptance stamp (watch-action Accept)
        "vendor_accepted_at",
    }
    json_cols = {"checklist"}
    cols: list[tuple[str, Any]] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in json_cols:
            if isinstance(v, (dict, list)):
                cols.append((k, json.dumps(v)))
            elif v is None:
                cols.append((k, json.dumps([])))
            elif isinstance(v, str):
                # Already JSON text — keep as-is for ::jsonb cast
                cols.append((k, v))
            else:
                cols.append((k, json.dumps(v)))
        else:
            cols.append((k, v))
    if not cols:
        return False
    # Always refresh updated_at if caller forgot (OS living-file freshness).
    if "updated_at" not in {c[0] for c in cols}:
        from datetime import datetime, timezone
        cols.append(("updated_at", datetime.now(timezone.utc).isoformat()))

    set_parts = []
    vals: list[Any] = []
    for c, v in cols:
        if c in json_cols:
            set_parts.append(f"{c} = %s::jsonb")
        else:
            set_parts.append(f"{c} = %s")
        vals.append(v)
    vals.append(jid)
    set_sql = ", ".join(set_parts)
    where_sql = "id = %s"
    if expected_status is not None:
        where_sql += " AND status = %s"
        vals.append(expected_status)
    if only_if_null is not None:
        null_col = str(only_if_null).strip()
        if null_col not in allowed:
            return False
        where_sql += f" AND {null_col} IS NULL"

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_job: no Postgres connection job=%s", jid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.jobs SET {set_sql} WHERE {where_sql}",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_job ok id=%s fields=%s",
                    jid,
                    [c[0] for c in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_job failed id=%s: %s", jid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_update_vendor_member_fields(member_id: str, fields: dict) -> bool:
    """UPDATE public.vendor_members SET … via Postgres when PostgREST is Fair-Use 402.

    Whitelist: decline_rate, on_time_rate, total_assignments, internal_notes, updated_at,
    terms_accepted_at, ic_agreement_version.
    Returns True when at least one row was updated.
    """
    mid = str(member_id or "").strip()
    if not mid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {
        "decline_rate",
        "on_time_rate",
        "total_assignments",
        "internal_notes",
        "updated_at",
        "terms_accepted_at",
        "ic_agreement_version",
        "ic_amendment_version",
        "ic_amendment_accepted_at",
    }
    cols: list[tuple[str, Any]] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        cols.append((k, v))
    if not cols:
        return False
    if "updated_at" not in {c[0] for c in cols}:
        from datetime import datetime, timezone
        cols.append(("updated_at", datetime.now(timezone.utc).isoformat()))

    set_parts = [f"{c} = %s" for c, _v in cols]
    vals: list[Any] = [v for _c, v in cols]
    vals.append(mid)
    set_sql = ", ".join(set_parts)

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_vendor_member_fields: no Postgres connection id=%s", mid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.vendor_members SET {set_sql} WHERE id = %s",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_vendor_member_fields ok id=%s fields=%s",
                    mid,
                    [c[0] for c in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_vendor_member_fields failed id=%s: %s", mid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass



def pg_insert_ops_escalation(row: dict) -> bool:
    """INSERT public.ops_escalations via Postgres when PostgREST is Fair-Use 402.

    Columns match _escalate_no_vendor: booking_id, job_id, escalation_type,
    attempt_count, error_message, status. Generates uuid id + created_at when
    missing. Returns True when a row was inserted.
    """
    if not isinstance(row, dict) or not row:
        return False

    import uuid
    from datetime import datetime, timezone

    allowed = {
        "id",
        "booking_id",
        "job_id",
        "escalation_type",
        "attempt_count",
        "error_message",
        "status",
        "created_at",
        "resolved_at",
        "resolved_by",
        "notes",
    }
    data = {k: v for k, v in row.items() if k in allowed}
    if not data.get("escalation_type"):
        data["escalation_type"] = "no_vendor_available"
    if not data.get("status"):
        data["status"] = "open"
    if data.get("attempt_count") is None:
        data["attempt_count"] = 1
    else:
        try:
            data["attempt_count"] = int(data["attempt_count"])
        except Exception:
            data["attempt_count"] = 1
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    if not data.get("created_at"):
        data["created_at"] = datetime.now(timezone.utc).isoformat()

    # Prefer stable column order for readable SQL/logs
    order = [
        "id",
        "booking_id",
        "job_id",
        "escalation_type",
        "attempt_count",
        "error_message",
        "status",
        "created_at",
        "resolved_at",
        "resolved_by",
        "notes",
    ]
    cols = [c for c in order if c in data]
    vals = [data[c] for c in cols]
    if "escalation_type" not in cols or "status" not in cols:
        return False

    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO public.ops_escalations ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )

    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_insert_ops_escalation: no Postgres connection job=%s",
            str(data.get("job_id") or "")[:12],
        )
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_insert_ops_escalation ok id=%s job=%s type=%s",
                    data.get("id"),
                    data.get("job_id"),
                    data.get("escalation_type"),
                )
            return ok
    except Exception as e:
        logger.error(
            "pg_insert_ops_escalation failed job=%s: %s",
            data.get("job_id"),
            str(e)[:400],
        )
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass




# Real public.uploads columns only — media_type / requires_staging are NOT DB cols.
_UPLOADS_COLUMNS = (
    "id",
    "job_id",
    "filename",
    "file_size",
    "mime_type",
    "thumbnail",
    "file_url",
    "thumbnail_url",
    "created_at",
    "processing_status",
    "provider",
    "provider_job_id",
    "processed_url",
    "processed_at",
    "qc_status",
    "qc_score",
    "qc_notes",
    "qc_at",
    "qc_variant_url",
)


def pg_insert_upload(row: dict) -> bool:
    """INSERT/UPSERT public.uploads via Postgres when PostgREST is Fair-Use 402.

    Whitelists real columns only (never media_type / requires_staging). On conflict
    (id) updates file metadata so a retried complete is idempotent. Returns True
    when a row was inserted or updated.
    """
    if not isinstance(row, dict) or not row:
        return False
    uid = str(row.get("id") or "").strip()
    jid = str(row.get("job_id") or "").strip()
    if not uid or not jid:
        return False

    cols: list[str] = []
    vals: list[Any] = []
    for c in _UPLOADS_COLUMNS:
        if c not in row:
            continue
        v = row.get(c)
        if c == "id":
            v = uid
        elif c == "job_id":
            v = jid
        elif c == "file_size":
            try:
                v = int(v or 0)
            except Exception:
                v = 0
        cols.append(c)
        vals.append(v)
    if "id" not in cols or "job_id" not in cols:
        return False
    if "filename" not in cols:
        cols.append("filename")
        vals.append(str(row.get("filename") or "still.jpg"))
    if "file_url" not in cols and row.get("file_url"):
        cols.append("file_url")
        vals.append(row.get("file_url"))

    placeholders = ", ".join(["%s"] * len(cols))
    # On conflict: refresh the file registration fields (idempotent complete).
    update_cols = [
        c for c in cols
        if c not in ("id", "created_at")
    ]
    if update_cols:
        set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        conflict_sql = f"ON CONFLICT (id) DO UPDATE SET {set_sql}"
    else:
        conflict_sql = "ON CONFLICT (id) DO NOTHING"

    sql = (
        f"INSERT INTO public.uploads ({', '.join(cols)}) "
        f"VALUES ({placeholders}) {conflict_sql}"
    )

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_insert_upload: no Postgres connection id=%s", uid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_insert_upload ok id=%s job=%s cols=%s",
                    uid,
                    jid,
                    cols,
                )
            else:
                # DO NOTHING conflict with no update cols still counts as success
                logger.warning("pg_insert_upload noop id=%s job=%s", uid, jid)
                ok = True
            return ok
    except Exception as e:
        logger.error("pg_insert_upload failed id=%s: %s", uid, str(e)[:400])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_uploads_by_job_id(job_id: str, *, limit: int = 500) -> list[dict]:
    """List uploads for a job via Postgres when PostgREST is Fair-Use 402."""
    jid = str(job_id or "").strip()
    if not jid:
        return []
    limit = max(1, min(int(limit or 500), 500))
    conn = _pg_connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM public.uploads
                WHERE job_id = %s
                ORDER BY created_at DESC NULLS LAST
                LIMIT %s
                """,
                (jid, limit),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg_select_uploads_by_job_id ok job=%s n=%s", jid, len(rows)
            )
            return rows
    except Exception as e:
        logger.error("pg_select_uploads_by_job_id failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass



def pg_update_upload(upload_id: str, fields: dict) -> bool:
    """UPDATE public.uploads SET … via Postgres when PostgREST is Fair-Use 402.

    Whitelist covers enhance→listing: processing_status, processed_url/at, provider,
    provider_job_id, qc_* and qc_variant_url. Returns True when a row was updated.
    """
    uid = str(upload_id or "").strip()
    if not uid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {
        "processing_status",
        "provider",
        "provider_job_id",
        "processed_url",
        "processed_at",
        "qc_status",
        "qc_score",
        "qc_notes",
        "qc_at",
        "qc_variant_url",
        "file_url",
        "thumbnail_url",
        "thumbnail",
        "filename",
        "file_size",
        "mime_type",
    }
    cols: list[tuple[str, Any]] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "file_size":
            try:
                v = int(v or 0)
            except Exception:
                v = 0
        elif k == "qc_score" and v is not None:
            try:
                v = float(v)
            except Exception:
                continue
        cols.append((k, v))
    if not cols:
        return False
    set_parts = [f"{c} = %s" for c, _ in cols]
    vals: list[Any] = [v for _, v in cols]
    vals.append(uid)
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_upload: no Postgres connection id=%s", uid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.uploads SET {', '.join(set_parts)} WHERE id = %s",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_upload ok id=%s fields=%s",
                    uid,
                    [c for c, _ in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_upload failed id=%s: %s", uid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_media_delivery_by_job_id(job_id: str) -> Optional[dict]:
    """Fetch media_delivery row for a job via Postgres (Fair-Use finalize guard)."""
    jid = str(job_id or "").strip()
    if not jid:
        return None
    conn = _pg_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text AS id, job_id, booking_id, delivery_urls,
                       delivered_at, notified_at, notification_status, created_at
                FROM public.media_delivery
                WHERE job_id = %s
                LIMIT 1
                """,
                (jid,),
            )
            rows = _rows_from_cursor(cur)
            if rows:
                logger.warning("pg_select_media_delivery_by_job_id ok job=%s", jid)
                return rows[0]
            return None
    except Exception as e:
        logger.error(
            "pg_select_media_delivery_by_job_id failed job=%s: %s",
            jid,
            str(e)[:300],
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_upsert_media_delivery(row: dict) -> bool:
    """INSERT/UPSERT public.media_delivery on job_id when PostgREST is Fair-Use 402.

    Lands listing delivery_urls after enhance so OS/Companion share is not hollow.
    """
    if not isinstance(row, dict) or not row:
        return False
    jid = str(row.get("job_id") or "").strip()
    if not jid:
        return False
    urls = row.get("delivery_urls")
    if urls is None:
        urls = []
    if isinstance(urls, (dict, list)):
        urls_json = json.dumps(urls)
    elif isinstance(urls, str):
        urls_json = urls
    else:
        urls_json = json.dumps([])
    delivered_at = row.get("delivered_at")
    if not delivered_at:
        from datetime import datetime, timezone
        delivered_at = datetime.now(timezone.utc).isoformat()
    notification_status = str(row.get("notification_status") or "pending")
    booking_id = row.get("booking_id")
    booking_id = str(booking_id).strip() if booking_id else None

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_upsert_media_delivery: no Postgres connection job=%s", jid[:12])
        return False
    try:
        with conn.cursor() as cur:
            if booking_id:
                cur.execute(
                    """
                    INSERT INTO public.media_delivery
                        (job_id, booking_id, delivery_urls, delivered_at, notification_status)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        booking_id = COALESCE(EXCLUDED.booking_id, media_delivery.booking_id),
                        delivery_urls = EXCLUDED.delivery_urls,
                        delivered_at = EXCLUDED.delivered_at,
                        notification_status = EXCLUDED.notification_status
                    """,
                    (jid, booking_id, urls_json, delivered_at, notification_status),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO public.media_delivery
                        (job_id, delivery_urls, delivered_at, notification_status)
                    VALUES (%s, %s::jsonb, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        delivery_urls = EXCLUDED.delivery_urls,
                        delivered_at = EXCLUDED.delivered_at,
                        notification_status = EXCLUDED.notification_status
                    """,
                    (jid, urls_json, delivered_at, notification_status),
                )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_upsert_media_delivery ok job=%s urls=%s",
                    jid,
                    len(urls) if isinstance(urls, list) else "?",
                )
            return ok
    except Exception as e:
        logger.error("pg_upsert_media_delivery failed job=%s: %s", jid, str(e)[:400])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_update_media_delivery(job_id: str, fields: dict) -> bool:
    """UPDATE media_delivery notification fields via Postgres (Fair-Use)."""
    jid = str(job_id or "").strip()
    if not jid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {"notification_status", "notified_at", "delivery_urls", "delivered_at", "booking_id"}
    json_cols = {"delivery_urls"}
    cols: list[tuple[str, Any]] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in json_cols:
            if isinstance(v, (dict, list)):
                cols.append((k, json.dumps(v)))
            elif v is None:
                cols.append((k, json.dumps([])))
            else:
                cols.append((k, v if isinstance(v, str) else json.dumps(v)))
        else:
            cols.append((k, v))
    if not cols:
        return False
    set_parts = []
    vals: list[Any] = []
    for c, v in cols:
        if c in json_cols:
            set_parts.append(f"{c} = %s::jsonb")
        else:
            set_parts.append(f"{c} = %s")
        vals.append(v)
    vals.append(jid)
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_media_delivery: no Postgres connection job=%s", jid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.media_delivery SET {', '.join(set_parts)} WHERE job_id = %s",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_media_delivery ok job=%s fields=%s",
                    jid,
                    [c for c, _ in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_media_delivery failed job=%s: %s", jid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_claim_job_for_processing(job_id: str) -> bool:
    """Atomic jobs.status→processing gate via Postgres (Fair-Use process_job_assets).

    Mirrors REST `.not_.in_("status", [processing, delivered, …])`. Returns True
    when this caller claimed the job (row updated).
    """
    jid = str(job_id or "").strip()
    if not jid:
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_claim_job_for_processing: no Postgres connection job=%s", jid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.jobs
                SET status = %s, updated_at = %s
                WHERE id = %s
                  AND COALESCE(status, '') NOT IN (
                      'processing', 'delivered', 'cancelled',
                      'agent_no_show', 'dispatch_failed'
                  )
                """,
                ("processing", now, jid),
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning("pg_claim_job_for_processing ok job=%s", jid)
            return ok
    except Exception as e:
        logger.error(
            "pg_claim_job_for_processing failed job=%s: %s",
            jid,
            str(e)[:300],
        )
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass



def pg_upsert_vendor_member(
    user_id: str,
    email: str,
    *,
    name: str = "",
    role: str = "Photographer",
    vendor_id: str = "default_vendor",
    onboarding_status: str = "under_review",
) -> Optional[dict]:
    """Ensure vendor_members row exists for a Fair-Use signup."""
    uid = str(user_id or "").strip()
    email_l = (email or "").strip().lower()
    display = (name or "").strip() or (email_l.split("@")[0] if email_l else "")
    if not uid or not email_l:
        return None
    # Adopt an existing row only by id / auth_user_id — never by email, or a
    # signup for someone else's address would claim their vendor member.
    existing = pg_select_vendor_member(uid, "")
    if existing:
        return existing
    conn = _pg_connect()
    if not conn:
        return None
    try:
        try:
            conn.autocommit = True
        except Exception:
            pass
        with conn.cursor() as cur:
            attempts = [
                (
                    """
                    INSERT INTO public.vendor_members (
                      id, email, name, role, vendor_id, onboarding_status,
                      auth_user_id, created_at, updated_at
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, %s, %s::uuid, now(), now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      email = EXCLUDED.email,
                      name = COALESCE(NULLIF(EXCLUDED.name, ''), public.vendor_members.name),
                      updated_at = now()
                    RETURNING *
                    """,
                    (uid, email_l, display, role, vendor_id, onboarding_status, uid),
                ),
                (
                    """
                    INSERT INTO public.vendor_members (
                      id, email, name, role, vendor_id, onboarding_status,
                      created_at, updated_at
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, %s, now(), now()
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING *
                    """,
                    (uid, email_l, display, role, vendor_id, onboarding_status),
                ),
                (
                    """
                    INSERT INTO public.vendor_members (
                      id, email, name, role, vendor_id, created_at, updated_at
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, now(), now()
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING *
                    """,
                    (uid, email_l, display, role, vendor_id),
                ),
            ]
            last_err = None
            for sql, params in attempts:
                try:
                    cur.execute(sql, params)
                    row = _row_from_cursor(cur)
                    if row:
                        return row
                except Exception as e:
                    last_err = e
                    logger.warning("pg_upsert_vendor_member attempt failed: %s", str(e)[:240])
                    continue
            if last_err:
                logger.error("pg_upsert_vendor_member failed: %s", str(last_err)[:400])
            return pg_select_vendor_member(uid, "")
    except Exception as e:
        logger.error("pg_upsert_vendor_member failed: %s", str(e)[:400])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_insert_vendor_application(row: dict) -> Optional[dict]:
    """INSERT public.vendor_members for /api/onboarding/apply when PostgREST is 402.

    Whitelists applicant columns used by _apply_to_bricks_inner. JSON list/dict
    fields are cast to jsonb. Soft-drops unknown columns on older schemas by
    retrying without optional keys (portfolio_samples, vendor_type, …).
    """
    if not isinstance(row, dict) or not row:
        return None

    import uuid
    from datetime import datetime, timezone

    json_cols = {
        "portfolio_links",
        "portfolio_samples",
        "equipment_list",
        "service_areas",
        "operation_polygon",
        "skills",
        "application_profile",
    }
    allowed = {
        "id",
        "vendor_id",
        "name",
        "email",
        "phone",
        "role",
        "title",
        "vendor_type",
        "specialty",
        "skills",
        "is_active",
        "onboarding_status",
        "portfolio_links",
        "portfolio_samples",
        "equipment_list",
        "service_areas",
        "operation_polygon",
        "home_city",
        "max_travel_km",
        "drone_certified",
        "rpas_certificate_number",
        "rpas_certificate_level",
        "hst_number",
        "internal_notes",
        "home_lat",
        "home_lng",
        "ic_agreement_version",
        "terms_accepted_at",
        "application_token",
        "source",
        "source_detail",
        "application_profile",
        "rpas_certificate_url",
        "created_at",
        "updated_at",
    }

    data = {k: row[k] for k in allowed if k in row}
    if not data.get("email") or not data.get("name"):
        return None
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    data["email"] = str(data["email"]).lower().strip()
    if not data.get("vendor_id"):
        data["vendor_id"] = ORG_VENDOR_ID
    now = datetime.now(timezone.utc).isoformat()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    data.setdefault("role", "Applicant")
    data.setdefault("onboarding_status", "pending")
    data.setdefault("is_active", False)

    # Soft-drop order when older DBs lack columns.
    optional_drop_order = (
        "application_profile",
        "rpas_certificate_url",
        "portfolio_samples",
        "vendor_type",
        "application_token",
        "operation_polygon",
        "rpas_certificate_level",
        "rpas_certificate_number",
        "ic_agreement_version",
        "terms_accepted_at",
        "internal_notes",
        "home_lat",
        "home_lng",
    )

    def _build(payload: dict):
        cols: list[str] = []
        vals: list[Any] = []
        placeholders: list[str] = []
        for c, v in payload.items():
            if c in json_cols:
                if hasattr(v, "dict"):
                    v = v.dict()
                elif isinstance(v, list):
                    v = [(x.dict() if hasattr(x, "dict") else x) for x in v]
                if not isinstance(v, str):
                    try:
                        v = json.dumps(v if v is not None else [])
                    except Exception:
                        v = "[]"
                cols.append(c)
                vals.append(v)
                placeholders.append("%s::jsonb")
                continue
            if c == "is_active" or c == "drone_certified":
                v = bool(v)
            elif c == "max_travel_km":
                try:
                    v = int(v)
                except Exception:
                    v = 50
            cols.append(c)
            vals.append(v)
            placeholders.append("%s")
        sql = (
            f"INSERT INTO public.vendor_members ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING *"
        )
        return sql, vals

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_insert_vendor_application: no Postgres connection")
        return None
    try:
        working = dict(data)
        last_err = None
        with conn.cursor() as cur:
            for _ in range(len(optional_drop_order) + 1):
                try:
                    sql, vals = _build(working)
                    cur.execute(sql, vals)
                    out = _row_from_cursor(cur)
                    if out:
                        for jk in json_cols:
                            _parse_json_field(out, jk, default=[])
                        logger.warning(
                            "pg_insert_vendor_application ok id=%s email=%s",
                            out.get("id"),
                            (out.get("email") or "")[:40],
                        )
                        return out
                except Exception as e:
                    last_err = e
                    dropped = False
                    for key in optional_drop_order:
                        if key in working:
                            working.pop(key, None)
                            dropped = True
                            logger.warning(
                                "pg_insert_vendor_application soft-drop %s: %s",
                                key,
                                str(e)[:180],
                            )
                            break
                    if not dropped:
                        break
            if last_err:
                logger.error(
                    "pg_insert_vendor_application failed: %s", str(last_err)[:400]
                )
            return None
    except Exception as e:
        logger.error("pg_insert_vendor_application failed: %s", str(e)[:400])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass




# --- Fair-Use: availability gate + calendar FreeBusy + vendor notify ----------

_VENDOR_AVAILABILITY_JSON_COLS = ("blocked_windows",)

_VENDOR_AVAILABILITY_COLUMNS = (
    "id",
    "vendor_id",
    "member_id",
    "available",
    "work_start",
    "work_end",
    "jobs_per_day",
    "blocked_windows",
    "updated_at",
    # migration 0017: what the applicant said on apply (self-reported)
    "days_of_week",
    "time_windows",
    "weekends",
    "earliest_start_date",
    "shoots_per_week",
    "notice_hours",
    "has_car",
    "has_licence",
)

_NOTIFICATIONS_COLUMNS = (
    "id",
    "title",
    "body",
    "type",
    "data",
    "read",
    "member_id",
    "created_at",
)


def _parse_json_field(row: dict, key: str, default: Any = None) -> None:
    """In-place: if row[key] is a JSON string, parse it (jsonb from psycopg2)."""
    if key not in row:
        return
    v = row.get(key)
    if isinstance(v, str):
        try:
            row[key] = json.loads(v)
        except Exception:
            if default is not None:
                row[key] = default


def pg_select_vendor_availability(
    member_id: str, vendor_id: str | None = None
) -> Optional[dict]:
    """SELECT one vendor_availability row by member_id (+ optional vendor_id).

    Parses blocked_windows JSON. Returns None if missing or on failure.
    """
    mid = str(member_id or "").strip()
    if not mid:
        return None
    vid = str(vendor_id).strip() if vendor_id is not None else ""
    conn = _pg_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            if vid:
                cur.execute(
                    "SELECT * FROM public.vendor_availability "
                    "WHERE member_id = %s AND vendor_id = %s LIMIT 1",
                    (mid, vid),
                )
            else:
                cur.execute(
                    "SELECT * FROM public.vendor_availability "
                    "WHERE member_id = %s LIMIT 1",
                    (mid,),
                )
            rows = _rows_from_cursor(cur)
            if not rows:
                return None
            row = rows[0]
            for jk in _VENDOR_AVAILABILITY_JSON_COLS:
                _parse_json_field(row, jk, default=[])
            logger.warning(
                "pg_select_vendor_availability ok member=%s vendor=%s",
                mid[:12],
                (vid or "-")[:20],
            )
            return row
    except Exception as e:
        logger.error(
            "pg_select_vendor_availability failed member=%s: %s",
            mid[:12],
            str(e)[:300],
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_upsert_vendor_availability(row: dict) -> Optional[dict]:
    """INSERT … ON CONFLICT (vendor_id, member_id) DO UPDATE for Fair-Use hours.

    Whitelists real columns. Generates id + updated_at when missing. Casts
    blocked_windows to jsonb. Returns the row after upsert (RETURNING * / re-select).
    """
    if not isinstance(row, dict) or not row:
        return None

    import uuid
    from datetime import datetime, timezone

    mid = str(row.get("member_id") or "").strip()
    vid = str(row.get("vendor_id") or "").strip() or ORG_VENDOR_ID
    if not mid:
        return None

    data = {k: row[k] for k in _VENDOR_AVAILABILITY_COLUMNS if k in row}
    data["member_id"] = mid
    data["vendor_id"] = vid
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    if not data.get("updated_at"):
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if "available" not in data:
        data["available"] = True
    if "work_start" not in data:
        data["work_start"] = "08:00"
    if "work_end" not in data:
        data["work_end"] = "17:00"
    if "jobs_per_day" not in data:
        data["jobs_per_day"] = 4
    if "blocked_windows" not in data:
        data["blocked_windows"] = []

    cols: list[str] = []
    vals: list[Any] = []
    placeholders: list[str] = []
    for c in _VENDOR_AVAILABILITY_COLUMNS:
        if c not in data:
            continue
        v = data[c]
        if c == "jobs_per_day":
            try:
                v = int(v)
            except Exception:
                v = 4
        elif c == "available":
            v = bool(v)
        elif c == "blocked_windows":
            if not isinstance(v, str):
                try:
                    if isinstance(v, list):
                        v = [
                            (x.dict() if hasattr(x, "dict") else x)
                            for x in v
                        ]
                    v = json.dumps(v if v is not None else [])
                except Exception:
                    v = "[]"
            cols.append(c)
            vals.append(v)
            placeholders.append("%s::jsonb")
            continue
        cols.append(c)
        vals.append(v)
        placeholders.append("%s")

    if "member_id" not in cols or "vendor_id" not in cols:
        return None

    update_cols = (
        "available",
        "work_start",
        "work_end",
        "jobs_per_day",
        "blocked_windows",
        "updated_at",
        "days_of_week",
        "time_windows",
        "weekends",
        "earliest_start_date",
        "shoots_per_week",
        "notice_hours",
        "has_car",
        "has_licence",
    )
    set_parts = [f"{c} = EXCLUDED.{c}" for c in update_cols if c in cols]
    set_sql = ", ".join(set_parts) if set_parts else "updated_at = EXCLUDED.updated_at"

    sql = (
        f"INSERT INTO public.vendor_availability ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (vendor_id, member_id) DO UPDATE SET {set_sql} "
        f"RETURNING *"
    )

    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_upsert_vendor_availability: no Postgres connection member=%s",
            mid[:12],
        )
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            rows = _rows_from_cursor(cur)
            if not rows:
                cur.execute(
                    "SELECT * FROM public.vendor_availability "
                    "WHERE member_id = %s AND vendor_id = %s LIMIT 1",
                    (mid, vid),
                )
                rows = _rows_from_cursor(cur)
            if not rows:
                return None
            out = rows[0]
            for jk in _VENDOR_AVAILABILITY_JSON_COLS:
                _parse_json_field(out, jk, default=[])
            logger.warning(
                "pg_upsert_vendor_availability ok member=%s vendor=%s",
                mid[:12],
                vid[:20],
            )
            return out
    except Exception as e:
        logger.error(
            "pg_upsert_vendor_availability failed member=%s: %s",
            mid[:12],
            str(e)[:400],
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_vendor_availability_by_member_ids(member_ids: list[str]) -> dict[str, dict]:
    """Map member_id → vendor_availability row via Postgres (Fair-Use path).

    Returns {} on connect/query failure so callers can soft-fail open.
    """
    ids = [str(m).strip() for m in (member_ids or []) if str(m).strip()][:80]
    if not ids:
        return {}
    conn = _pg_connect()
    if not conn:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.vendor_availability "
                "WHERE member_id = ANY(%s) LIMIT 80",
                (ids,),
            )
            rows = _rows_from_cursor(cur)
            out: dict[str, dict] = {}
            for row in rows:
                for jk in _VENDOR_AVAILABILITY_JSON_COLS:
                    _parse_json_field(row, jk, default=[])
                mid = row.get("member_id")
                if mid:
                    out[str(mid)] = row
            logger.warning(
                "pg vendor_availability fallback ok n=%s members=%s",
                len(out),
                ids[:3],
            )
            return out
    except Exception as e:
        logger.error("pg vendor_availability failed: %s", str(e)[:300])
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_vendor_calendar_connections(member_id: str) -> list[dict]:
    """Rows from public.vendor_calendar_connections for one member (Fair-Use path)."""
    mid = str(member_id or "").strip()
    if not mid:
        return []
    conn = _pg_connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.vendor_calendar_connections "
                "WHERE member_id = %s LIMIT 20",
                (mid,),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg vendor_calendar_connections fallback ok n=%s member=%s",
                len(rows),
                mid[:12],
            )
            return rows
    except Exception as e:
        logger.error(
            "pg vendor_calendar_connections failed member=%s: %s",
            mid[:12],
            str(e)[:300],
        )
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass



_VENDOR_CALENDAR_CONNECTION_COLUMNS = (
    "id",
    "member_id",
    "vendor_id",
    "provider",
    "access_token",
    "refresh_token",
    "token_expires_at",
    "caldav_url",
    "caldav_username",
    "caldav_password",
    "ics_url",
    "created_at",
    "updated_at",
)


def pg_upsert_vendor_calendar_connection(row: dict) -> Optional[dict]:
    """INSERT … ON CONFLICT (member_id, provider) DO UPDATE for Fair-Use Connect.

    Whitelists real columns. Generates uuid id + created_at/updated_at when missing.
    On conflict updates credential columns + updated_at from EXCLUDED (never overwrites
    created_at). Returns the row after upsert (RETURNING * / re-select) or None.
    """
    if not isinstance(row, dict) or not row:
        return None

    import uuid
    from datetime import datetime, timezone

    mid = str(row.get("member_id") or "").strip()
    provider = str(row.get("provider") or "").strip().lower()
    if not mid or not provider:
        return None

    data = {k: row[k] for k in _VENDOR_CALENDAR_CONNECTION_COLUMNS if k in row}
    data["member_id"] = mid
    data["provider"] = provider
    if not data.get("vendor_id"):
        data["vendor_id"] = str(row.get("vendor_id") or "").strip() or ORG_VENDOR_ID
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    if not data.get("created_at"):
        data["created_at"] = now
    if not data.get("updated_at"):
        data["updated_at"] = now

    cols: list[str] = []
    vals: list[Any] = []
    placeholders: list[str] = []
    for c in _VENDOR_CALENDAR_CONNECTION_COLUMNS:
        if c not in data:
            continue
        cols.append(c)
        vals.append(data[c])
        placeholders.append("%s")

    if "member_id" not in cols or "provider" not in cols:
        return None

    # Do NOT overwrite created_at on conflict.
    update_cols = (
        "vendor_id",
        "access_token",
        "refresh_token",
        "token_expires_at",
        "caldav_url",
        "caldav_username",
        "caldav_password",
        "ics_url",
        "updated_at",
    )
    set_parts = [f"{c} = EXCLUDED.{c}" for c in update_cols if c in cols]
    set_sql = ", ".join(set_parts) if set_parts else "updated_at = EXCLUDED.updated_at"

    sql = (
        f"INSERT INTO public.vendor_calendar_connections ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (member_id, provider) DO UPDATE SET {set_sql} "
        f"RETURNING *"
    )

    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_upsert_vendor_calendar_connection: no Postgres connection member=%s provider=%s",
            mid[:12],
            provider,
        )
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            rows = _rows_from_cursor(cur)
            if not rows:
                cur.execute(
                    "SELECT * FROM public.vendor_calendar_connections "
                    "WHERE member_id = %s AND provider = %s LIMIT 1",
                    (mid, provider),
                )
                rows = _rows_from_cursor(cur)
            if not rows:
                return None
            out = rows[0]
            logger.warning(
                "pg_upsert_vendor_calendar_connection ok member=%s provider=%s",
                mid[:12],
                provider,
            )
            return out
    except Exception as e:
        logger.error(
            "pg_upsert_vendor_calendar_connection failed member=%s provider=%s: %s",
            mid[:12],
            provider,
            str(e)[:400],
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_delete_vendor_calendar_connection(member_id: str, provider: str) -> bool:
    """DELETE vendor_calendar_connections row by member_id + provider (Fair-Use path).

    Returns True when the DELETE executed without error (rowcount >= 0 is ok).
    """
    mid = str(member_id or "").strip()
    prov = str(provider or "").strip().lower()
    if not mid or not prov:
        return False
    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_delete_vendor_calendar_connection: no Postgres connection member=%s provider=%s",
            mid[:12],
            prov,
        )
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.vendor_calendar_connections "
                "WHERE member_id = %s AND provider = %s",
                (mid, prov),
            )
            logger.warning(
                "pg_delete_vendor_calendar_connection ok member=%s provider=%s rowcount=%s",
                mid[:12],
                prov,
                getattr(cur, "rowcount", None),
            )
            return True
    except Exception as e:
        logger.error(
            "pg_delete_vendor_calendar_connection failed member=%s provider=%s: %s",
            mid[:12],
            prov,
            str(e)[:400],
        )
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass



def pg_insert_notification(row: dict) -> bool:
    """INSERT public.notifications via Postgres when PostgREST is Fair-Use 402.

    Whitelists NotificationRecord columns only (id, title, body, type, data,
    read, member_id, created_at). Generates uuid id + created_at when missing.
    """
    if not isinstance(row, dict) or not row:
        return False

    import uuid
    from datetime import datetime, timezone

    data = {k: row[k] for k in _NOTIFICATIONS_COLUMNS if k in row}
    if not data.get("title") and not data.get("body"):
        return False
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    if not data.get("created_at"):
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    if "type" not in data:
        data["type"] = "info"
    if "read" not in data:
        data["read"] = False
    if "data" not in data:
        data["data"] = {}

    cols: list[str] = []
    vals: list[Any] = []
    for c in _NOTIFICATIONS_COLUMNS:
        if c not in data:
            continue
        v = data[c]
        if c == "data":
            if not isinstance(v, str):
                try:
                    v = json.dumps(v if v is not None else {})
                except Exception:
                    v = "{}"
            cols.append(c)
            vals.append(v)
        else:
            cols.append(c)
            vals.append(v)
    if "id" not in cols or "title" not in cols:
        return False

    # data is jsonb — cast when present
    placeholders = []
    for c in cols:
        if c == "data":
            placeholders.append("%s::jsonb")
        else:
            placeholders.append("%s")
    sql = (
        f"INSERT INTO public.notifications ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )

    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_insert_notification: no Postgres connection title=%s",
            str(data.get("title") or "")[:40],
        )
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_insert_notification ok id=%s member=%s type=%s",
                    data.get("id"),
                    data.get("member_id"),
                    data.get("type"),
                )
            return ok
    except Exception as e:
        logger.error(
            "pg_insert_notification failed id=%s: %s",
            data.get("id"),
            str(e)[:400],
        )
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_push_tokens_for_member(member_id: str) -> list[dict]:
    """Active push_tokens for a member via Postgres (Fair-Use path for notify)."""
    mid = str(member_id or "").strip()
    if not mid:
        return []
    conn = _pg_connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.push_tokens "
                "WHERE member_id = %s AND active = true LIMIT 50",
                (mid,),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg push_tokens fallback ok n=%s member=%s",
                len(rows),
                mid[:12],
            )
            return rows
    except Exception as e:
        logger.error(
            "pg push_tokens failed member=%s: %s",
            mid[:12],
            str(e)[:300],
        )
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_notifications_for_member(member_id: str, *, limit: int = 100) -> list[dict]:
    """List notifications for a photographer via Postgres (Fair-Use inbox path)."""
    mid = str(member_id or "").strip()
    if not mid:
        return []
    lim = max(1, min(int(limit or 100), 200))
    conn = _pg_connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, body, type, data, read, member_id, created_at "
                "FROM public.notifications "
                "WHERE member_id = %s "
                "ORDER BY created_at DESC NULLS LAST "
                "LIMIT %s",
                (mid, lim),
            )
            rows = _rows_from_cursor(cur)
            for r in rows:
                _parse_json_field(r, "data", default={})
                if "read" in r:
                    r["read"] = bool(r.get("read"))
            logger.warning(
                "pg_select_notifications_for_member ok n=%s member=%s",
                len(rows),
                mid[:12],
            )
            return rows
    except Exception as e:
        logger.error(
            "pg_select_notifications_for_member failed member=%s: %s",
            mid[:12],
            str(e)[:300],
        )
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_notification_by_id(notification_id: str) -> Optional[dict]:
    """Fetch one notification row by id via Postgres."""
    nid = str(notification_id or "").strip()
    if not nid:
        return None
    conn = _pg_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, body, type, data, read, member_id, created_at "
                "FROM public.notifications WHERE id = %s LIMIT 1",
                (nid,),
            )
            row = _row_from_cursor(cur)
            if not row:
                return None
            _parse_json_field(row, "data", default={})
            if "read" in row:
                row["read"] = bool(row.get("read"))
            return row
    except Exception as e:
        logger.error(
            "pg_select_notification_by_id failed id=%s: %s",
            nid[:36],
            str(e)[:300],
        )
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_mark_notification_read(notification_id: str, member_id: str | None = None) -> bool:
    """Mark one notification read via Postgres. Optionally scope to member_id."""
    nid = str(notification_id or "").strip()
    if not nid:
        return False
    mid = str(member_id or "").strip() or None
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn = _pg_connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            if mid:
                # updated_at may not exist on older schemas — try with it, then without
                try:
                    cur.execute(
                        "UPDATE public.notifications SET read = true, updated_at = %s "
                        "WHERE id = %s AND member_id = %s",
                        (now, nid, mid),
                    )
                except Exception:
                    cur.execute(
                        "UPDATE public.notifications SET read = true "
                        "WHERE id = %s AND member_id = %s",
                        (nid, mid),
                    )
            else:
                try:
                    cur.execute(
                        "UPDATE public.notifications SET read = true, updated_at = %s "
                        "WHERE id = %s",
                        (now, nid),
                    )
                except Exception:
                    cur.execute(
                        "UPDATE public.notifications SET read = true WHERE id = %s",
                        (nid,),
                    )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_mark_notification_read ok id=%s member=%s",
                    nid[:36],
                    (mid or "")[:12],
                )
            return ok
    except Exception as e:
        logger.error(
            "pg_mark_notification_read failed id=%s: %s",
            nid[:36],
            str(e)[:300],
        )
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_mark_all_notifications_read(member_id: str) -> int:
    """Mark all unread notifications for a member read. Returns rows updated (or -1 on soft fail)."""
    mid = str(member_id or "").strip()
    if not mid:
        return 0
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn = _pg_connect()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "UPDATE public.notifications SET read = true, updated_at = %s "
                    "WHERE member_id = %s AND read = false",
                    (now, mid),
                )
            except Exception:
                cur.execute(
                    "UPDATE public.notifications SET read = true "
                    "WHERE member_id = %s AND (read = false OR read IS NULL)",
                    (mid,),
                )
            n = int(cur.rowcount or 0)
            logger.warning(
                "pg_mark_all_notifications_read ok n=%s member=%s",
                n,
                mid[:12],
            )
            return n
    except Exception as e:
        logger.error(
            "pg_mark_all_notifications_read failed member=%s: %s",
            mid[:12],
            str(e)[:300],
        )
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


_PUSH_TOKENS_COLUMNS = (
    "id",
    "member_id",
    "token",
    "platform",
    "active",
    "created_at",
    "updated_at",
)


def pg_select_push_token_by_token(token: str) -> Optional[dict]:
    """Lookup push_tokens row by device token via Postgres."""
    tok = str(token or "").strip()
    if not tok:
        return None
    conn = _pg_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, member_id, token, platform, active, created_at, updated_at "
                "FROM public.push_tokens WHERE token = %s LIMIT 1",
                (tok,),
            )
            return _row_from_cursor(cur)
    except Exception as e:
        logger.error("pg_select_push_token_by_token failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_upsert_push_token(row: dict) -> Optional[dict]:
    """Insert or update a push_tokens row via Postgres (Fair-Use device register).

    Prefer update-by-token when a row exists; otherwise INSERT. Whitelists columns.
    """
    if not isinstance(row, dict) or not row:
        return None
    from datetime import datetime, timezone

    data = {k: row[k] for k in _PUSH_TOKENS_COLUMNS if k in row}
    tok = str(data.get("token") or "").strip()
    mid = str(data.get("member_id") or "").strip()
    if not tok or not mid:
        return None
    if not data.get("id"):
        import uuid

        data["id"] = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    if not data.get("created_at"):
        data["created_at"] = now
    data["updated_at"] = now
    if "platform" not in data:
        data["platform"] = "expo"
    if "active" not in data:
        data["active"] = True

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_upsert_push_token: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            existing = None
            try:
                cur.execute(
                    "SELECT id FROM public.push_tokens WHERE token = %s LIMIT 1",
                    (tok,),
                )
                existing = _row_from_cursor(cur)
            except Exception:
                existing = None

            if existing and existing.get("id"):
                cur.execute(
                    "UPDATE public.push_tokens SET member_id = %s, platform = %s, "
                    "active = %s, updated_at = %s WHERE id = %s",
                    (
                        mid,
                        data.get("platform") or "expo",
                        bool(data.get("active", True)),
                        now,
                        existing["id"],
                    ),
                )
                data["id"] = existing["id"]
                logger.warning(
                    "pg_upsert_push_token update ok id=%s member=%s",
                    data["id"],
                    mid[:12],
                )
                return data

            cols = [c for c in _PUSH_TOKENS_COLUMNS if c in data]
            placeholders = ", ".join(["%s"] * len(cols))
            sql = (
                f"INSERT INTO public.push_tokens ({', '.join(cols)}) "
                f"VALUES ({placeholders})"
            )
            vals = [data[c] for c in cols]
            cur.execute(sql, vals)
            logger.warning(
                "pg_upsert_push_token insert ok id=%s member=%s",
                data.get("id"),
                mid[:12],
            )
            return data
    except Exception as e:
        logger.error("pg_upsert_push_token failed: %s", str(e)[:400])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_dispatch_failed_jobs(limit: int = 50, offset: int = 0) -> list[dict]:
    """List admin dispatch queue jobs via Postgres (Fair-Use 402).

    Includes status=dispatch_failed, held_for_review, and legacy
    pending_assignment rows whose dispatch_explanation still carries the
    Jev "Held for a person" note (empty assignee only).
    Columns match admin_ops.list_dispatch_failures select list.
    Ordered by updated_at DESC. limit/offset clamped to safe bounds.
    """
    try:
        lim = max(1, min(int(limit or 50), 200))
    except Exception:
        lim = 50
    try:
        off = max(0, int(offset or 0))
    except Exception:
        off = 0

    cols = (
        "id, booking_id, address, city, job_type, package_name, min_tier_required, "
        "scheduled_date, time_window_start, time_window_end, created_at, updated_at, "
        "status, dispatch_explanation, assignee_id"
    )
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_dispatch_failed_jobs: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {cols} FROM public.jobs "
                "WHERE status IN ('dispatch_failed', 'held_for_review') "
                "   OR ("
                "        status = 'pending_assignment' "
                "        AND COALESCE(assignee_id, '') = '' "
                "        AND dispatch_explanation ILIKE %s"
                "   ) "
                "ORDER BY updated_at DESC NULLS LAST "
                "LIMIT %s OFFSET %s",
                ("%Held for a person%", lim, off),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg_select_dispatch_failed_jobs ok n=%s limit=%s offset=%s",
                len(rows),
                lim,
                off,
            )
            return rows
    except Exception as e:
        logger.error("pg_select_dispatch_failed_jobs failed: %s", str(e)[:400])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_ops_escalations_for_jobs(job_ids: list[str]) -> list[dict]:
    """Load ops_escalations rows for job ids via Postgres (Fair-Use 402).

    Returns job_id, attempt_count, error_message, status, created_at — same
    shape as admin_ops.list_dispatch_failures join.
    """
    ids = [str(j).strip() for j in (job_ids or []) if str(j).strip()]
    if not ids:
        return []
    # Cap IN list for safety
    ids = ids[:200]
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_ops_escalations_for_jobs: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_id, attempt_count, error_message, status, created_at "
                "FROM public.ops_escalations "
                "WHERE job_id = ANY(%s)",
                (ids,),
            )
            rows = _rows_from_cursor(cur)
            logger.warning(
                "pg_select_ops_escalations_for_jobs ok n=%s jobs=%s",
                len(rows),
                len(ids),
            )
            return rows
    except Exception as e:
        logger.error("pg_select_ops_escalations_for_jobs failed: %s", str(e)[:400])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_mark_ops_escalations_manual_retry(job_id: str) -> int:
    """Set open ops_escalations for a job to status=manual_retry via Postgres.

    Returns number of rows updated (0 when none open or PG unavailable).
    """
    jid = str(job_id or "").strip()
    if not jid:
        return 0
    conn = _pg_connect()
    if not conn:
        logger.warning(
            "pg_mark_ops_escalations_manual_retry: no Postgres connection job=%s",
            jid[:12],
        )
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.ops_escalations "
                "SET status = %s "
                "WHERE job_id = %s AND status = %s",
                ("manual_retry", jid, "open"),
            )
            n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            logger.warning(
                "pg_mark_ops_escalations_manual_retry ok job=%s n=%s",
                jid[:12],
                n,
            )
            return int(n)
    except Exception as e:
        logger.error(
            "pg_mark_ops_escalations_manual_retry failed job=%s: %s",
            jid[:12],
            str(e)[:400],
        )
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass



def pg_select_vendor_payout_rates() -> list[dict]:
    """Load vendor_payout_rates rows via Postgres when PostgREST is Fair-Use 402."""
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_vendor_payout_rates: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT package_id, sqft_tier, vendor_tier, amount_cents "
                "FROM public.vendor_payout_rates"
            )
            rows = _rows_from_cursor(cur)
            logger.warning("pg_select_vendor_payout_rates ok n=%s", len(rows))
            return rows
    except Exception as e:
        logger.error("pg_select_vendor_payout_rates failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_payout_by_job_id(job_id: str) -> Optional[dict]:
    """Fetch one payouts row by job_id via Postgres."""
    jid = str(job_id or "").strip()
    if not jid:
        return None
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_payout_by_job_id: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.payouts WHERE job_id = %s LIMIT 1",
                (jid,),
            )
            row = _row_from_cursor(cur)
            if row:
                logger.warning("pg_select_payout_by_job_id ok job=%s id=%s", jid[:12], row.get("id"))
            return row
    except Exception as e:
        logger.error("pg_select_payout_by_job_id failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_insert_payout(row: dict) -> Optional[dict]:
    """INSERT public.payouts via Postgres. Generates text uuid id when missing."""
    if not isinstance(row, dict) or not row:
        return None
    import uuid
    from datetime import datetime, timezone

    allowed = {
        "id",
        "job_id",
        "vendor_id",
        "contractor_id",
        "amount",
        "status",
        "stripe_transfer_id",
        "created_at",
        "net_amount",
        "trip_fees_total",
        "hst_paid_cents",
        "contractor_hst_rate",
        "paid_at",
    }
    data = {k: row[k] for k in allowed if k in row}
    if not data.get("job_id") or data.get("amount") is None or not data.get("vendor_id"):
        return None
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    if not data.get("created_at"):
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    if not data.get("status"):
        data["status"] = "pending"

    cols = list(data.keys())
    vals = [data[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_insert_payout: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO public.payouts ({col_sql}) VALUES ({placeholders}) RETURNING *",
                vals,
            )
            out = _row_from_cursor(cur)
            if out:
                logger.warning("pg_insert_payout ok id=%s job=%s", out.get("id"), out.get("job_id"))
            return out
    except Exception as e:
        logger.error("pg_insert_payout failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_update_payout(payout_id: str, fields: dict) -> bool:
    """UPDATE public.payouts SET … via Postgres when PostgREST is Fair-Use 402."""
    pid = str(payout_id or "").strip()
    if not pid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {
        "amount",
        "status",
        "stripe_transfer_id",
        "contractor_id",
        "net_amount",
        "trip_fees_total",
        "hst_paid_cents",
        "contractor_hst_rate",
        "paid_at",
    }
    cols: list[tuple[str, Any]] = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return False
    set_parts = [f"{c} = %s" for c, _v in cols]
    vals: list[Any] = [v for _c, v in cols]
    vals.append(pid)
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_payout: no Postgres connection id=%s", pid[:12])
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.payouts SET {', '.join(set_parts)} WHERE id = %s",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_payout ok id=%s fields=%s",
                    pid,
                    [c[0] for c in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_payout failed id=%s: %s", pid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_list_payouts(
    *,
    vendor_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List payouts via Postgres for admin list endpoint under Fair Use."""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_list_payouts: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            where_parts: list[str] = []
            params: list[Any] = []
            if vendor_id:
                where_parts.append("vendor_id = %s")
                params.append(vendor_id)
            if status:
                where_parts.append("status = %s")
                params.append(status)
            where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
            params.extend([limit, offset])
            cur.execute(
                f"SELECT * FROM public.payouts{where} "
                f"ORDER BY created_at DESC NULLS LAST LIMIT %s OFFSET %s",
                params,
            )
            rows = _rows_from_cursor(cur)
            logger.warning("pg_list_payouts ok n=%s", len(rows))
            return rows
    except Exception as e:
        logger.error("pg_list_payouts failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_payouts_status_amounts() -> list[dict]:
    """Select status+amount for all payouts (summary) via Postgres."""
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_payouts_status_amounts: no Postgres connection")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, amount FROM public.payouts")
            rows = _rows_from_cursor(cur)
            logger.warning("pg_select_payouts_status_amounts ok n=%s", len(rows))
            return rows
    except Exception as e:
        logger.error("pg_select_payouts_status_amounts failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_contractor_by_vendor_id(vendor_id: str) -> Optional[dict]:
    """Fetch contractors row by vendor_id via Postgres."""
    vid = str(vendor_id or "").strip()
    if not vid:
        return None
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_contractor_by_vendor_id: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stripe_account_id, connect_status, vendor_id, full_name, email, member_id "
                "FROM public.contractors WHERE member_id IS NULL AND (vendor_id = %s OR (vendor_id IS NULL "
                "AND (SELECT count(*) FROM public.contractors c2 WHERE c2.vendor_id IS NULL AND c2.member_id IS NULL) = 1)) "
                "ORDER BY (vendor_id IS NULL) LIMIT 1",
                (vid,),
            )
            row = _row_from_cursor(cur)
            if row and row.get("id") is not None:
                row["id"] = str(row["id"])
            return row
    except Exception as e:
        logger.error("pg_select_contractor_by_vendor_id failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_contractor_by_stripe_account_id(stripe_account_id: str) -> Optional[dict]:
    """Fetch contractors.id by stripe_account_id via Postgres."""
    aid = str(stripe_account_id or "").strip()
    if not aid:
        return None
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_contractor_by_stripe_account_id: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stripe_account_id, connect_status, vendor_id, member_id "
                "FROM public.contractors WHERE stripe_account_id = %s LIMIT 1",
                (aid,),
            )
            row = _row_from_cursor(cur)
            if row and row.get("id") is not None:
                row["id"] = str(row["id"])
            return row
    except Exception as e:
        logger.error("pg_select_contractor_by_stripe_account_id failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_contractor_by_member_id(member_id: str) -> Optional[dict]:
    """Fetch one photographer's contractors row (per-member Stripe Connect) via Postgres."""
    mid = str(member_id or "").strip()
    if not mid:
        return None
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_select_contractor_by_member_id: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stripe_account_id, connect_status, vendor_id, full_name, email, member_id "
                "FROM public.contractors WHERE member_id = %s LIMIT 1",
                (mid,),
            )
            row = _row_from_cursor(cur)
            if row and row.get("id") is not None:
                row["id"] = str(row["id"])
            return row
    except Exception as e:
        logger.error("pg_select_contractor_by_member_id failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_insert_contractor(row: dict) -> Optional[dict]:
    """INSERT public.contractors via Postgres for Connect onboarding under Fair Use."""
    if not isinstance(row, dict) or not row:
        return None
    import uuid
    from datetime import datetime, timezone

    allowed = {
        "id",
        "vendor_id",
        "member_id",
        "full_name",
        "email",
        "stripe_account_id",
        "connect_status",
        "phone",
        "tier",
        "status",
        "created_at",
    }
    data = {k: row[k] for k in allowed if k in row and row[k] is not None}
    if not (data.get("vendor_id") or data.get("member_id")) or not data.get("full_name") or not data.get("email"):
        return None
    if not data.get("id"):
        data["id"] = str(uuid.uuid4())
    if not data.get("created_at"):
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    if "connect_status" not in data:
        data["connect_status"] = "not_connected"
    if "status" not in data:
        data["status"] = "active"
    if "tier" not in data:
        data["tier"] = 1

    cols = list(data.keys())
    vals = [data[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)

    conn = _pg_connect()
    if not conn:
        logger.warning("pg_insert_contractor: no Postgres connection")
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO public.contractors ({col_sql}) VALUES ({placeholders}) RETURNING "
                f"id, vendor_id, full_name, email, stripe_account_id, connect_status",
                vals,
            )
            out = _row_from_cursor(cur)
            if out and out.get("id") is not None:
                out["id"] = str(out["id"])
                logger.warning("pg_insert_contractor ok id=%s vendor=%s", out["id"], out.get("vendor_id"))
            return out
    except Exception as e:
        logger.error("pg_insert_contractor failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_update_contractor(contractor_id: str, fields: dict) -> bool:
    """UPDATE public.contractors SET … via Postgres (connect_status / stripe_account_id)."""
    cid = str(contractor_id or "").strip()
    if not cid or not isinstance(fields, dict) or not fields:
        return False
    allowed = {
        "stripe_account_id",
        "connect_status",
        "connect_onboarded_at",
        "full_name",
        "email",
        "phone",
    }
    cols: list[tuple[str, Any]] = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return False
    set_parts = [f"{c} = %s" for c, _v in cols]
    vals: list[Any] = [v for _c, v in cols]
    vals.append(cid)
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_update_contractor: no Postgres connection id=%s", cid[:12])
        return False
    try:
        with conn.cursor() as cur:
            # COALESCE so connect_onboarded_at is only set the first time.
            set_sql = ", ".join(
                f"{c} = COALESCE({c}, %s)" if c == "connect_onboarded_at" else part
                for (c, _v), part in zip(cols, set_parts)
            )
            cur.execute(
                f"UPDATE public.contractors SET {set_sql} WHERE id = %s::uuid",
                vals,
            )
            ok = cur.rowcount > 0
            if ok:
                logger.warning(
                    "pg_update_contractor ok id=%s fields=%s",
                    cid,
                    [c[0] for c in cols],
                )
            return ok
    except Exception as e:
        logger.error("pg_update_contractor failed id=%s: %s", cid, str(e)[:300])
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Applicant review tables (migration 0017). Insert/select helpers.
# ---------------------------------------------------------------------------
_APPLICANT_TABLES = {
    "vendor_applicant_reviews": (
        ("member_id", "verdict", "needs_samples", "result", "model", "cost_usd", "trigger", "trade", "rubric_version"),
        {"result"},
    ),
    "vendor_applicant_image_hashes": (
        ("member_id", "source", "ref", "sha256", "dhash"),
        set(),
    ),
}


def pg_insert_applicant_rows(table: str, rows: list) -> int:
    """INSERT rows into an applicant review table via Postgres. Returns count."""
    spec = _APPLICANT_TABLES.get(table)
    if not spec or not rows:
        return 0
    cols, json_cols = spec
    conn = _pg_connect()
    if not conn:
        return 0
    n = 0
    try:
        with conn.cursor() as cur:
            for row in rows:
                use = [c for c in cols if c in row]
                vals = []
                ph = []
                for c in use:
                    v = row[c]
                    if c in json_cols:
                        v = json.dumps(v)
                        ph.append("%s::jsonb")
                    else:
                        ph.append("%s")
                    vals.append(v)
                cur.execute(
                    f"INSERT INTO public.{table} ({', '.join(use)}) VALUES ({', '.join(ph)})",
                    vals,
                )
                n += 1
        return n
    except Exception as e:
        logger.error("pg_insert_applicant_rows %s failed: %s", table, str(e)[:300])
        return n
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_applicant_hashes(limit: int = 5000) -> list:
    conn = _pg_connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT member_id, sha256, dhash FROM public.vendor_applicant_image_hashes "
                "ORDER BY created_at DESC LIMIT %s",
                (int(limit),),
            )
            return _rows_from_cursor(cur)
    except Exception as e:
        logger.error("pg_select_applicant_hashes failed: %s", str(e)[:300])
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
