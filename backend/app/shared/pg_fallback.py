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



def pg_verify_auth_password(email: str, password: str) -> Optional[dict]:
    """Verify email+password against auth.users via pgcrypto crypt().

    Returns {"id": uuid_str, "email": email} or None. Uses direct Postgres
    only (Auth API is Fair-Use blocked). Returns None when PG unavailable
    so callers can distinguish with a separate connect check if needed.
    """
    email_l = (email or "").strip().lower()
    if not email_l or password is None or password == "":
        return None
    conn = _pg_connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            # Prefer deleted_at filter; fall back if column missing.
            sqls = [
                (
                    "SELECT id::text AS id, email::text AS email FROM auth.users "
                    "WHERE lower(email) = lower(%s) "
                    "AND encrypted_password IS NOT NULL "
                    "AND encrypted_password = crypt(%s, encrypted_password) "
                    "AND deleted_at IS NULL "
                    "LIMIT 1"
                ),
                (
                    "SELECT id::text AS id, email::text AS email FROM auth.users "
                    "WHERE lower(email) = lower(%s) "
                    "AND encrypted_password IS NOT NULL "
                    "AND encrypted_password = crypt(%s, encrypted_password) "
                    "LIMIT 1"
                ),
            ]
            last_err = None
            for sql in sqls:
                try:
                    cur.execute(sql, (email_l, password))
                    row = _row_from_cursor(cur)
                    if row and row.get("id"):
                        return {"id": str(row["id"]), "email": str(row.get("email") or email_l)}
                    return None
                except Exception as e:
                    last_err = e
                    err = str(e).lower()
                    if "deleted_at" in err or "column" in err:
                        continue
                    logger.error("pg auth password verify failed: %s", str(e)[:300])
                    return None
            if last_err is not None:
                logger.error("pg auth password verify failed: %s", str(last_err)[:300])
            return None
    except Exception as e:
        logger.error("pg auth password verify failed: %s", str(e)[:300])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


def pg_create_auth_user(
    email: str,
    password: str,
    *,
    full_name: str = "",
) -> Optional[dict]:
    """Insert auth.users (+ identity) via Postgres when Auth API is Fair-Use 402.

    Uses pgcrypto crypt()/gen_salt('bf'). Prefers a direct (non-pooler) DSN so
    the insert is not lost on transaction-mode pooler disconnects. Verifies the
    row exists before returning.
    Raises ValueError("email_taken") / ValueError("password_too_short").
    """
    import json
    import uuid
    from urllib.parse import urlparse

    email_l = (email or "").strip().lower()
    password = "" if password is None else str(password)
    name = (full_name or "").strip()
    if not email_l or not password:
        return None
    if len(password) < 8:
        raise ValueError("password_too_short")

    conn = None
    try:
        import psycopg2
        from urllib.parse import urlparse as _urlparse

        candidates = list(_pg_dsn_candidates() or [])
        def _score(u: str) -> int:
            try:
                h = (_urlparse(u).hostname or "").lower()
            except Exception:
                return 50
            if h.startswith("db.") and h.endswith(".supabase.co"):
                return 0
            if "pooler.supabase.com" in h and ":5432" in (u or ""):
                return 1
            if "pooler.supabase.com" in h:
                return 10
            return 5
        candidates.sort(key=_score)
        last_err = None
        for url in candidates:
            try:
                if url.startswith("postgres://"):
                    url = "postgresql://" + url[len("postgres://"):]
                c = psycopg2.connect(url, connect_timeout=8)
                c.autocommit = True
                conn = c
                break
            except Exception as e:
                last_err = e
                continue
        if conn is None:
            conn = _pg_connect()
            if conn is not None:
                try:
                    conn.autocommit = True
                except Exception:
                    pass
            else:
                logger.error("pg_create_auth_user connect failed: %s", type(last_err).__name__ if last_err else "none")
                return None
    except Exception as e:
        logger.error("pg_create_auth_user connect setup failed: %s", str(e)[:300])
        return None

    user_id = str(uuid.uuid4())
    meta = {"full_name": name} if name else {}
    app_meta = {"provider": "email", "providers": ["email"]}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM auth.users WHERE lower(email) = lower(%s) LIMIT 1",
                (email_l,),
            )
            if cur.fetchone():
                raise ValueError("email_taken")

            insert_sqls = [
                """
                INSERT INTO auth.users (
                  instance_id, id, aud, role, email, encrypted_password,
                  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
                  created_at, updated_at, confirmation_token, recovery_token,
                  email_change_token_new, email_change, is_sso_user, is_anonymous
                ) VALUES (
                  '00000000-0000-0000-0000-000000000000'::uuid,
                  %s::uuid, 'authenticated', 'authenticated', %s,
                  crypt(%s, gen_salt('bf')), now(), %s::jsonb, %s::jsonb,
                  now(), now(), '', '', '', '', false, false
                )
                RETURNING id::text AS id, email::text AS email
                """,
                """
                INSERT INTO auth.users (
                  instance_id, id, aud, role, email, encrypted_password,
                  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
                  created_at, updated_at, confirmation_token, recovery_token,
                  email_change_token_new, email_change
                ) VALUES (
                  '00000000-0000-0000-0000-000000000000'::uuid,
                  %s::uuid, 'authenticated', 'authenticated', %s,
                  crypt(%s, gen_salt('bf')), now(), %s::jsonb, %s::jsonb,
                  now(), now(), '', '', '', ''
                )
                RETURNING id::text AS id, email::text AS email
                """,
            ]
            row = None
            last_err = None
            params = (user_id, email_l, password, json.dumps(app_meta), json.dumps(meta))
            for sql in insert_sqls:
                try:
                    cur.execute(sql, params)
                    row = _row_from_cursor(cur)
                    if row and row.get("id"):
                        break
                except Exception as e:
                    last_err = e
                    row = None
                    continue
            if not row or not row.get("id"):
                if last_err:
                    logger.error("pg_create_auth_user insert failed: %s", str(last_err)[:400])
                return None

            # Verify persistence (guards pooler/transaction drop)
            cur.execute(
                "SELECT id::text AS id, email::text AS email FROM auth.users WHERE id = %s::uuid LIMIT 1",
                (row["id"],),
            )
            verified = _row_from_cursor(cur)
            if not verified or not verified.get("id"):
                logger.error("pg_create_auth_user insert did not persist id=%s", row.get("id"))
                return None

            identity_id = str(uuid.uuid4())
            identity_data = {
                "sub": verified["id"],
                "email": email_l,
                "email_verified": True,
                "phone_verified": False,
            }
            if name:
                identity_data["full_name"] = name
            try:
                cur.execute(
                    """
                    INSERT INTO auth.identities (
                      id, user_id, identity_data, provider, provider_id,
                      last_sign_in_at, created_at, updated_at
                    ) VALUES (
                      %s::uuid, %s::uuid, %s::jsonb, 'email', %s,
                      now(), now(), now()
                    )
                    """,
                    (identity_id, verified["id"], json.dumps(identity_data), verified["id"]),
                )
            except Exception as e:
                logger.warning("identity insert failed: %s", str(e)[:200])

            return {
                "id": str(verified["id"]),
                "email": str(verified.get("email") or email_l),
            }
    except ValueError:
        raise
    except Exception as e:
        logger.error("pg_create_auth_user failed: %s", str(e)[:400])
        return None
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
    existing = pg_select_vendor_member(uid, email_l)
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
            return pg_select_vendor_member(uid, email_l)
    except Exception as e:
        logger.error("pg_upsert_vendor_member failed: %s", str(e)[:400])
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
