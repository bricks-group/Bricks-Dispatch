"""Direct Postgres helpers for capture_* tables when PostgREST is Fair-Use 402.

Used by capture.py (and optionally capture_worker) so Start Capture → frames →
submit can create/read/update rows while Canada Free Storage quota blocks REST.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .pg_fallback import _pg_connect, _row_from_cursor, _rows_from_cursor

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_conn(fn):
    """Run fn(conn); return None on connect failure. Autocommit already set."""
    conn = _pg_connect()
    if not conn:
        logger.warning("pg_capture: no Postgres connection")
        return None
    try:
        return fn(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pg_select_session_in_progress(job_id: str, kind: str) -> Optional[dict]:
    jid = str(job_id or "").strip()
    k = str(kind or "splat").strip() or "splat"
    if not jid:
        return None

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM public.capture_sessions
                WHERE job_id = %s AND status = 'in_progress' AND kind = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (jid, k),
            )
            return _row_from_cursor(cur)

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_select_session_in_progress failed: %s", str(e)[:300])
        return None


def pg_select_session_latest(job_id: str, kind: str) -> Optional[dict]:
    jid = str(job_id or "").strip()
    k = str(kind or "splat").strip() or "splat"
    if not jid:
        return None

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM public.capture_sessions
                WHERE job_id = %s AND kind = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (jid, k),
            )
            return _row_from_cursor(cur)

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_select_session_latest failed: %s", str(e)[:300])
        return None


def pg_select_session_by_id(session_id: str, job_id: Optional[str] = None) -> Optional[dict]:
    sid = str(session_id or "").strip()
    if not sid:
        return None

    def run(conn):
        with conn.cursor() as cur:
            if job_id:
                cur.execute(
                    """
                    SELECT * FROM public.capture_sessions
                    WHERE id = %s::uuid AND job_id = %s
                    LIMIT 1
                    """,
                    (sid, str(job_id)),
                )
            else:
                cur.execute(
                    "SELECT * FROM public.capture_sessions WHERE id = %s::uuid LIMIT 1",
                    (sid,),
                )
            return _row_from_cursor(cur)

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_select_session_by_id failed: %s", str(e)[:300])
        return None


def pg_select_rooms(session_id: str) -> list[dict]:
    sid = str(session_id or "").strip()
    if not sid:
        return []

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, frames, tone, position, session_id, job_id
                FROM public.capture_rooms
                WHERE session_id = %s::uuid
                ORDER BY position ASC
                """,
                (sid,),
            )
            return _rows_from_cursor(cur)

    try:
        return _with_conn(run) or []
    except Exception as e:
        logger.error("pg_select_rooms failed: %s", str(e)[:300])
        return []


def pg_select_pipeline(session_id: str) -> list[dict]:
    sid = str(session_id or "").strip()
    if not sid:
        return []

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, detail, status, position, session_id, job_id
                FROM public.capture_pipeline
                WHERE session_id = %s::uuid
                ORDER BY position ASC
                """,
                (sid,),
            )
            return _rows_from_cursor(cur)

    try:
        return _with_conn(run) or []
    except Exception as e:
        logger.error("pg_select_pipeline failed: %s", str(e)[:300])
        return []


def pg_select_frames(session_id: str) -> list[dict]:
    sid = str(session_id or "").strip()
    if not sid:
        return []

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, session_id, room_id, job_id, storage_path, created_at,
                       station_index, yaw, content_type, role
                FROM public.capture_frames
                WHERE session_id = %s::uuid
                ORDER BY created_at ASC
                """,
                (sid,),
            )
            return _rows_from_cursor(cur)

    try:
        return _with_conn(run) or []
    except Exception as e:
        logger.error("pg_select_frames failed: %s", str(e)[:300])
        return []


def pg_create_session_with_defaults(
    job_id: str,
    kind: str,
    pipelines: list[tuple[str, str, str]],
) -> Optional[dict]:
    """INSERT capture_sessions + pipeline rows + Room 1. Returns session row."""
    jid = str(job_id or "").strip()
    k = str(kind or "splat").strip() or "splat"
    if not jid:
        return None
    sid = str(uuid.uuid4())
    now = _now_iso()

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.capture_sessions (
                  id, job_id, status, kind, frame_count, created_at, updated_at
                ) VALUES (
                  %s::uuid, %s, 'in_progress', %s, 0, %s::timestamptz, %s::timestamptz
                )
                RETURNING *
                """,
                (sid, jid, k, now, now),
            )
            session = _row_from_cursor(cur)
            if not session:
                return None
            for i, (title, detail, status) in enumerate(pipelines or []):
                cur.execute(
                    """
                    INSERT INTO public.capture_pipeline (
                      session_id, job_id, title, detail, status, position
                    ) VALUES (
                      %s::uuid, %s, %s, %s, %s, %s
                    )
                    """,
                    (sid, jid, title, detail, status, i),
                )
            cur.execute(
                """
                INSERT INTO public.capture_rooms (
                  session_id, job_id, name, frames, tone, position
                ) VALUES (
                  %s::uuid, %s, 'Room 1', 0, 'bad', 0
                )
                """,
                (sid, jid),
            )
            logger.warning("pg_capture create session ok id=%s job=%s kind=%s", sid, jid, k)
            return session

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_create_session_with_defaults failed: %s", str(e)[:400])
        return None


def pg_insert_room(
    session_id: str,
    job_id: str,
    *,
    name: str = "Room 1",
    frames: int = 0,
    tone: str = "bad",
    position: int = 0,
) -> Optional[dict]:
    sid = str(session_id or "").strip()
    jid = str(job_id or "").strip()
    if not sid:
        return None

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.capture_rooms (
                  session_id, job_id, name, frames, tone, position
                ) VALUES (
                  %s::uuid, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (sid, jid or None, name, int(frames), tone, int(position)),
            )
            return _row_from_cursor(cur)

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_insert_room failed: %s", str(e)[:300])
        return None


def pg_insert_frame(row: dict) -> Optional[dict]:
    """Insert capture_frames. Tries full column set then slim (path-only)."""
    sid = str(row.get("session_id") or "").strip()
    storage_path = str(row.get("storage_path") or "").strip()
    if not sid or not storage_path:
        return None
    now = row.get("created_at") or _now_iso()
    room_id = row.get("room_id") or None
    # Cast room_id only when present so NULL stays NULL.
    room_sql = "%s::uuid" if room_id else "%s"
    job_id = row.get("job_id") or None
    station_index = row.get("station_index")
    yaw = row.get("yaw")
    content_type = row.get("content_type")
    role = row.get("role")
    station_val = int(station_index) if station_index is not None else None

    def run(conn):
        with conn.cursor() as cur:
            attempts = [
                (
                    f"""
                    INSERT INTO public.capture_frames (
                      session_id, room_id, job_id, storage_path, created_at,
                      station_index, yaw, content_type, role
                    ) VALUES (
                      %s::uuid, {room_sql}, %s, %s, %s::timestamptz,
                      %s, %s, %s, %s
                    )
                    RETURNING *
                    """,
                    (
                        sid,
                        room_id,
                        job_id,
                        storage_path,
                        now,
                        station_val,
                        yaw,
                        content_type,
                        role,
                    ),
                ),
                (
                    f"""
                    INSERT INTO public.capture_frames (
                      session_id, room_id, job_id, storage_path, created_at
                    ) VALUES (
                      %s::uuid, {room_sql}, %s, %s, %s::timestamptz
                    )
                    RETURNING *
                    """,
                    (sid, room_id, job_id, storage_path, now),
                ),
            ]
            last_err = None
            for sql, params in attempts:
                try:
                    cur.execute(sql, params)
                    row_out = _row_from_cursor(cur)
                    if row_out:
                        return row_out
                except Exception as e:
                    last_err = e
                    continue
            if last_err:
                raise last_err
            return None

    try:
        return _with_conn(run)
    except Exception as e:
        logger.error("pg_insert_frame failed: %s", str(e)[:400])
        return None


def pg_update_session(session_id: str, fields: dict) -> bool:
    sid = str(session_id or "").strip()
    if not sid or not fields:
        return False
    allowed = {
        "status",
        "frame_count",
        "submitted_at",
        "output_storage_path",
        "updated_at",
        "kind",
    }
    cols = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return False
    if "updated_at" not in {c[0] for c in cols}:
        cols.append(("updated_at", _now_iso()))
    set_sql = ", ".join(f"{c} = %s" for c, _ in cols)
    vals = [v for _, v in cols] + [sid]

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.capture_sessions SET {set_sql} WHERE id = %s::uuid",
                vals,
            )
            return cur.rowcount > 0

    try:
        return bool(_with_conn(run))
    except Exception as e:
        logger.error("pg_update_session failed: %s", str(e)[:300])
        return False


def pg_update_room(room_id: str, fields: dict, *, session_id: Optional[str] = None) -> bool:
    rid = str(room_id or "").strip()
    if not rid or not fields:
        return False
    allowed = {"frames", "tone", "name", "position"}
    cols = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return False
    set_sql = ", ".join(f"{c} = %s" for c, _ in cols)
    vals: list[Any] = [v for _, v in cols]
    where = "id = %s::uuid"
    vals.append(rid)
    if session_id:
        where += " AND session_id = %s::uuid"
        vals.append(str(session_id))

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.capture_rooms SET {set_sql} WHERE {where}",
                vals,
            )
            return cur.rowcount > 0

    try:
        return bool(_with_conn(run))
    except Exception as e:
        logger.error("pg_update_room failed: %s", str(e)[:300])
        return False


def pg_update_pipeline(
    session_id: str,
    title: str,
    fields: dict,
) -> bool:
    sid = str(session_id or "").strip()
    title = str(title or "").strip()
    if not sid or not title or not fields:
        return False
    allowed = {"status", "detail", "title"}
    cols = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return False
    set_sql = ", ".join(f"{c} = %s" for c, _ in cols)
    vals = [v for _, v in cols] + [sid, title]

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE public.capture_pipeline
                SET {set_sql}
                WHERE session_id = %s::uuid AND title = %s
                """,
                vals,
            )
            return cur.rowcount > 0

    try:
        return bool(_with_conn(run))
    except Exception as e:
        logger.error("pg_update_pipeline failed: %s", str(e)[:300])
        return False


def pg_count_delivered(job_id: str, kind: str) -> int:
    jid = str(job_id or "").strip()
    k = str(kind or "").strip()
    if not jid:
        return 0

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM public.capture_sessions
                WHERE job_id = %s AND kind = %s AND status = 'delivered'
                """,
                (jid, k),
            )
            row = cur.fetchone()
            return int((row or [0])[0] or 0)

    try:
        return int(_with_conn(run) or 0)
    except Exception as e:
        logger.error("pg_count_delivered failed: %s", str(e)[:300])
        return 0


def pg_upsert_media_file(
    booking_id: str,
    storage_path: str,
    file_url: str,
    file_name: str,
    *,
    label: str = "Floor plan",
    file_type: str = "floor_plan",
    ai_provider: str = "bricks",
) -> bool:
    """INSERT or UPDATE public.media_files by storage_path when PostgREST is 402.

    Returns True on success, False on failure / missing args. Never raises.
    """
    bid = str(booking_id or "").strip()
    sp = str(storage_path or "").strip()
    if not bid or not sp:
        return False
    url = str(file_url or sp).strip() or sp
    name = str(file_name or "").strip() or sp.rsplit("/", 1)[-1]
    lab = str(label or "Floor plan")
    ftype = str(file_type or "floor_plan")
    provider = str(ai_provider or "bricks")

    def run(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM public.media_files
                WHERE storage_path = %s
                LIMIT 1
                """,
                (sp,),
            )
            row = cur.fetchone()
            if row:
                mid = row[0]
                cur.execute(
                    """
                    UPDATE public.media_files
                    SET file_url = %s, file_name = %s, label = %s
                    WHERE id = %s
                    """,
                    (url, name, lab, mid),
                )
                logger.warning(
                    "pg_capture media_files update ok id=%s path=%s", mid, sp[:80]
                )
                return True
            cur.execute(
                """
                INSERT INTO public.media_files (
                  booking_id, file_type, file_name, file_url,
                  storage_path, label, ai_provider
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (bid, ftype, name, url, sp, lab, provider),
            )
            logger.warning(
                "pg_capture media_files insert ok booking=%s path=%s", bid, sp[:80]
            )
            return True

    try:
        return bool(_with_conn(run))
    except Exception as e:
        logger.error("pg_upsert_media_file failed: %s", str(e)[:400])
        return False
