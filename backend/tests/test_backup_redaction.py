"""Nightly backup never writes secrets (Sentinel, 2026-11-02).

Every public table is dumped with an explicit column list; secret columns are
replaced in SQL by '[redacted]' (NULL stays NULL). Explicit per-table denylist
plus a fail-closed name rule: any column named like token / secret / password /
hash / credential / key / sealed is redacted unless reviewed onto the keep-list.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services import ops_autopilot as oa

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "backup_public_columns.json").read_text())

REQUIRED = {
    "public.vendor_calendar_connections": {"access_token", "refresh_token", "caldav_password"},
    "public.connected_tools": {"credential_secret"},
    "public.password_resets": {"token"},
    "public.signature_recipients": {"token"},
    "public.push_tokens": {"token"},
    "public.vendor_members": {"application_token"},
    "public.property_visits": {"password_hash"},
    "public.dev_progress_reports": {"share_token"},
    "public.dev_project_share_links": {"token"},
    "public.site_monitor_share_tokens": {"token"},
    "public.app_secrets": {"value"},
    "public.contractors": {"ical_url"},
    "public.company_calendar_settings": {"ical_url"},
}
REQUIRED["public.vendor_calendar_connections"] |= {"ics_url", "caldav_url"}
NAME_RULE = r"(token|secret|password|passwd|hash)"


def _sensitive(col):
    import re
    return re.search(NAME_RULE, col, re.I) or oa.SENSITIVE_COLUMN_RE.search(col)


def test_required_secret_columns_are_redacted():
    for table, cols in REQUIRED.items():
        assert cols <= set(SNAPSHOT[table]), (table, "snapshot out of date")
        red = oa.backup_redacted_columns(table, SNAPSHOT[table])
        assert cols <= red, (table, cols - red)
        sql = oa._backup_select(table, SNAPSHOT[table])
        for c in cols:
            assert f"else '[redacted]' end as \"{c}\"" in sql


def test_every_secret_named_column_in_schema_is_redacted_or_reviewed():
    """Fails when a column named like token/secret/password/hash is neither
    redacted nor on the reviewed BACKUP_KEEP_COLUMNS list."""
    leaks = []
    for table, cols in SNAPSHOT.items():
        red = oa.backup_redacted_columns(table, cols)
        keep = oa.BACKUP_KEEP_COLUMNS.get(table, frozenset())
        for c in cols:
            if _sensitive(c) and c not in red and c not in keep:
                leaks.append(f"{table}.{c}")
    assert leaks == []


@pytest.mark.parametrize("col", ["webhook_secret", "api_token", "pin_hash", "new_password", "client_credentials",
                                  "share_token", "API_KEY", "refresh_token", "otp", "otp_code", "token_sealed",
                                  "ical_url", "ics_url", "caldav_url", "team_ical_url", "webcal_url", "icsurl"])
def test_new_secret_named_column_is_redacted_automatically(col):
    cols = ["id", "created_at", col]
    assert oa.backup_redacted_columns("public.brand_new_table", cols) == {col}
    assert f"else '[redacted]' end as \"{col}\"" in oa._backup_select("public.brand_new_table", cols)


def test_keep_list_is_small_and_not_secrets():
    assert sum(len(v) for v in oa.BACKUP_KEEP_COLUMNS.values()) <= 6
    for table, cols in oa.BACKUP_KEEP_COLUMNS.items():
        assert not (cols & oa.BACKUP_REDACT_COLUMNS.get(table, frozenset()))
        for c in cols:
            assert "secret" not in c and "password" not in c and c not in ("token", "access_token", "refresh_token")


def test_ordinary_columns_untouched():
    cols = ["id", "email", "postal_code", "session_id", "invite_code", "created_at"]
    assert oa.backup_redacted_columns("public.jobs", cols) == set()
    sql = oa._backup_select("public.jobs", cols)
    assert "[redacted]" not in sql and '"public"."jobs"' in sql


def test_select_without_columns_fails_closed():
    with pytest.raises(ValueError):
        oa._backup_select("public.push_tokens")
    with pytest.raises(ValueError):
        oa._backup_select("public.push_tokens", [])


def test_identifier_quoting():
    sql = oa._backup_select('public.we"ird', ['a"b', "token"])
    assert '"we""ird"' in sql and '"a""b"' in sql


def test_python_mirror_redacts_rows():
    row = {"id": "1", "token": "abc", "platform": "ios", "user_id": "u"}
    assert oa.redact_backup_row("public.push_tokens", row) == {"id": "1", "token": "[redacted]", "platform": "ios", "user_id": "u"}
    assert oa.redact_backup_row("public.push_tokens", {"token": None})["token"] is None


class _Cur:
    def __init__(self, tables, columns, rows):
        self.tables, self.columns, self.rows, self.sql, self._out = tables, columns, rows, [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.tables" in sql:
            self._out = [(t,) for t in self.tables]
        elif "information_schema.columns" in sql:
            self._out = [(t, c) for t, cs in self.columns.items() for c in cs]
        else:
            self._out = [(json.dumps(r),) for r in self.rows]

    def fetchall(self):
        return list(self._out)

    def fetchmany(self, n):
        out, self._out = self._out[:n], self._out[n:]
        return out


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def rollback(self):
        pass

    def close(self):
        pass


def test_dump_database_uses_redacting_select(monkeypatch):
    import gzip
    from app.shared import pg_fallback
    cur = _Cur(["public.push_tokens", "public.no_cols"], {"public.push_tokens": ["id", "token"]}, [{"id": 1}])
    monkeypatch.setattr(pg_fallback, "_pg_connect", lambda: _Conn(cur))
    data, counts = oa.dump_database()
    selects = [s for s in cur.sql if "row_to_json" in s]
    assert any("else '[redacted]' end as \"token\"" in s and '"push_tokens"' in s for s in selects)
    assert counts["public.no_cols"] == -1  # fail closed: no column list -> skipped, not dumped raw
    assert all('from "public"."push_tokens" x' not in s for s in selects)
    assert gzip.decompress(data)


@pytest.mark.skipif(not os.environ.get("BACKUP_SCHEMA_LIVE_CHECK"), reason="live schema check opt-in")
def test_live_schema_has_no_unreviewed_secret_columns():
    from app.shared.pg_fallback import _pg_connect
    conn = _pg_connect()
    with conn.cursor() as cur:
        cur.execute("select table_schema||'.'||table_name, column_name from information_schema.columns where table_schema='public'")
        rows = cur.fetchall()
    conn.close()
    cols: dict = {}
    for t, c in rows:
        cols.setdefault(t, []).append(c)
    leaks = [f"{t}.{c}" for t, cs in cols.items() for c in cs
             if _sensitive(c) and c not in oa.backup_redacted_columns(t, cs)
             and c not in oa.BACKUP_KEEP_COLUMNS.get(t, frozenset())]
    assert leaks == []


class _SqlCur(_Cur):
    """Emulates Postgres applying the generated SELECT: columns the SQL
    redacts come back as '[redacted]', everything else raw."""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema" in sql:
            return super().execute(sql, params)
        import re
        red = set(re.findall(r"else '\[redacted\]' end as \"([^\"]+)\"", sql))
        sel = sql.split("(select ", 1)[1].split(" from ", 1)[0] if "(select " in sql else "*"
        out = []
        for r in (self.rows if '"public"."app_secrets"' in sql else []):
            if sel.strip() == "*":
                out.append(r)
            else:
                out.append({k: ("[redacted]" if k in red and v is not None else v) for k, v in r.items()})
        self._out = [(json.dumps(r),) for r in out]


def test_app_secrets_values_never_appear_in_dump(monkeypatch):
    import gzip
    from app.shared import pg_fallback
    secrets = {"INTERNAL_API_KEY": "ik_live_SENTINEL_9f8e7d", "STRIPE_WEBHOOK_SECRET": "whsec_SENTINEL_abc123"}
    rows = [{"key": k, "value": v} for k, v in secrets.items()]
    cur = _SqlCur(["public.app_secrets"], {"public.app_secrets": ["key", "value"]}, rows)
    monkeypatch.setattr(pg_fallback, "_pg_connect", lambda: _Conn(cur))
    data, counts = oa.dump_database()
    text = gzip.decompress(data).decode()
    assert counts["public.app_secrets"] == 2
    for v in secrets.values():
        assert v not in text
    assert "INTERNAL_API_KEY" in text  # key names kept so a restore knows what to refill
