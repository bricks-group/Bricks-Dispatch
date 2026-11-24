"""Anon and authenticated roles must not read or write vendor calendar credentials.

The table is created in supabase/migrations/0008_vendor_calendars_and_job_slots.sql
and later migrations never enable row level security or revoke API-role grants.
Supabase Postgres image 17.6.1.136 applies, as the postgres role
(migrations/db/migrate.sh lines 41-44), the default privileges in
migrations/db/init-scripts/00000000000000-initial-schema.sql lines 29-42 and
51-60: new public tables are granted to anon, authenticated, and service_role.
PostgREST v14.18 (the image pinned in supabase/supabase docker-compose.yml)
then switches to the JWT role claim and runs SELECT/INSERT/UPDATE/DELETE
with no extra ownership filter.

These tests apply that grant setup, apply this repo's migrations, and issue
the same REST calls an anon or authenticated JWT can make. They fail while
the credentials are exposed and pass once a migration enables row level
security on public.vendor_calendar_connections and revokes every privilege
on it from anon and authenticated. service_role keeps access, which is how
the Dispatch API reads the table.

Not covered here: GraphQL (/graphql/v1) and Realtime. Both would use the
same table privileges, but this harness does not start those services.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DB = "calendar_rls_spec"
# Local stand-in only. Not a project credential.
DB_PASSWORD = "placeholder-not-a-secret"
JWT_SECRET = "calendar-rls-spec-jwt-secret-32b"
PORT = 3218

# Obvious placeholders. Not real calendar credentials.
APPLE_MEMBER = "placeholder-member-apple"
ICS_MEMBER = "placeholder-member-ics"
GOOGLE_MEMBER = "placeholder-member-google"
APPLE_PASSWORD = "placeholder-caldav-app-password"
APPLE_USER = "placeholder-caldav-user"
APPLE_URL = "https://caldav.example.invalid/placeholder"
ICS_URL = "https://ics.example.invalid/placeholder.ics"
GOOGLE_ACCESS = "placeholder-oauth-access"
GOOGLE_REFRESH = "placeholder-oauth-refresh"
ATTACK_INSERT_MEMBER = "placeholder-attacker-insert"
ATTACK_ICS = "https://ics.example.invalid/attacker.ics"
REPLACED_ICS = "https://ics.example.invalid/replaced.ics"

# supabase/postgres tag 17.6.1.136
# migrations/db/init-scripts/00000000000000-initial-schema.sql
# Run as postgres, matching migrations/db/migrate.sh lines 41-44.
SUPABASE_API_GRANTS = """
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'supabase_admin') then
    create role supabase_admin superuser login;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create user authenticator noinherit;
  end if;
end
$$;

create schema if not exists extensions;

grant anon to authenticator;
grant authenticated to authenticator;
grant service_role to authenticator;
grant supabase_admin to authenticator;

grant usage on schema public to postgres, anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to postgres, anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to postgres, anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to postgres, anon, authenticated, service_role;

alter default privileges for user supabase_admin in schema public grant all
    on sequences to postgres, anon, authenticated, service_role;
alter default privileges for user supabase_admin in schema public grant all
    on tables to postgres, anon, authenticated, service_role;
alter default privileges for user supabase_admin in schema public grant all
    on functions to postgres, anon, authenticated, service_role;

alter role anon set statement_timeout = '3s';
alter role authenticated set statement_timeout = '8s';
alter user authenticator with login password 'placeholder-not-a-secret';
"""


def _psql(sql: str, database: str = DB, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", database, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        check=check,
        capture_output=True,
        text=True,
    )


def _psql_file(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-d", DB, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def _sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    tag = "v"
    while f"${tag}$" in value:
        tag += "x"
    return f"${tag}${value}${tag}$"


def _migration_files() -> list[Path]:
    groups = [
        REPO / "supabase" / "migrations",
        REPO / "migrations",
        REPO / "backend" / "migrations",
    ]
    files: list[Path] = []
    seen: set[str] = set()
    for directory in groups:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.sql")):
            if path.name in seen:
                continue
            seen.add(path.name)
            files.append(path)
    return files


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(role: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps({"role": role, "iat": now, "exp": now + 3600}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _postgrest_bin() -> str:
    override = os.environ.get("POSTGREST_BIN")
    if override:
        return override
    found = subprocess.run(["bash", "-lc", "command -v postgrest"], capture_output=True, text=True)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip()
    fallback = "/tmp/postgrest-bin/postgrest"
    if Path(fallback).is_file():
        return fallback
    pytest.fail("postgrest v14.18 is not installed; set POSTGREST_BIN")


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"postgrest did not listen on {port}")


def _rest(role: str, method: str, path: str, body: dict | None = None, prefer: str | None = None):
    token = _jwt(role)
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "apikey": token,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _seed_rows() -> None:
    import sys

    sys.path.insert(0, str(REPO / "backend"))
    os.environ.pop("CALENDAR_TOKEN_ENCRYPTION_KEY", None)
    from app.services.calendar_token_crypto import encrypt_connection_secrets

    # Same helper the Apple/Google persist paths call before upsert.
    apple = encrypt_connection_secrets(
        {
            "member_id": APPLE_MEMBER,
            "vendor_id": "default_vendor",
            "provider": "apple",
            "caldav_url": APPLE_URL,
            "caldav_username": APPLE_USER,
            "caldav_password": APPLE_PASSWORD,
        }
    )
    google = encrypt_connection_secrets(
        {
            "member_id": GOOGLE_MEMBER,
            "vendor_id": "default_vendor",
            "provider": "google",
            "access_token": GOOGLE_ACCESS,
            "refresh_token": GOOGLE_REFRESH,
        }
    )
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = "placeholder-encryption-key"
    try:
        from app.services.calendar_token_crypto import encrypt_connection_secrets as encrypt_again

        ics = encrypt_again(
            {
                "member_id": ICS_MEMBER,
                "vendor_id": "default_vendor",
                "provider": "ics",
                "ics_url": ICS_URL,
                "caldav_username": APPLE_USER,
            }
        )
    finally:
        os.environ.pop("CALENDAR_TOKEN_ENCRYPTION_KEY", None)

    _psql("delete from public.vendor_calendar_connections")
    for row in (apple, google, ics):
        columns = [
            "member_id",
            "vendor_id",
            "provider",
            "access_token",
            "refresh_token",
            "caldav_url",
            "caldav_username",
            "caldav_password",
            "ics_url",
        ]
        values = ", ".join(_sql_literal(row.get(column)) for column in columns)
        names = ", ".join(columns)
        _psql(
            f"insert into public.vendor_calendar_connections ({names}) values ({values})"
        )
    _psql(
        "insert into public.vendor_busy_blocks (member_id, provider, starts_at, ends_at) "
        "values ('placeholder-member-busy', 'ics', now(), now() + interval '1 hour')"
    )


def _column(member_id: str, column: str) -> str:
    found = _psql(
        "select coalesce("
        f"{column}, '') from public.vendor_calendar_connections "
        f"where member_id = {_sql_literal(member_id)}"
    )
    return found.stdout.strip()


def _count(member_id: str) -> int:
    found = _psql(
        "select count(*) from public.vendor_calendar_connections "
        f"where member_id = {_sql_literal(member_id)}"
    )
    return int(found.stdout.strip() or "0")


@pytest.fixture(scope="module")
def api():
    if subprocess.run(["sudo", "-u", "postgres", "psql", "-c", "select 1"], capture_output=True).returncode:
        pytest.fail("local postgres is not reachable via sudo -u postgres")

    _psql(
        "select pg_terminate_backend(pid) from pg_stat_activity "
        f"where datname = {_sql_literal(DB)} and pid <> pg_backend_pid()",
        database="postgres",
        check=False,
    )
    _psql(f"drop database if exists {DB}", database="postgres")
    _psql(f"create database {DB}", database="postgres")
    _psql(SUPABASE_API_GRANTS)
    _psql(
        "create table if not exists public.jobs (id uuid primary key default gen_random_uuid())"
    )

    for path in _migration_files():
        result = _psql_file(path)
        if result.returncode != 0 and "vendor_calendar_connections" in path.read_text():
            pytest.fail(f"{path} mentions vendor_calendar_connections and failed:\n{result.stderr}")

    present = _psql(
        "select to_regclass('public.vendor_calendar_connections') is not null"
    ).stdout.strip()
    if present != "t":
        pytest.fail("migrations did not create public.vendor_calendar_connections")

    env = os.environ.copy()
    env.update(
        {
            "PGRST_DB_URI": f"postgres://authenticator:{DB_PASSWORD}@127.0.0.1:5432/{DB}",
            "PGRST_DB_SCHEMAS": "public",
            "PGRST_DB_ANON_ROLE": "anon",
            "PGRST_DB_EXTRA_SEARCH_PATH": "public",
            "PGRST_JWT_SECRET": JWT_SECRET,
            "PGRST_SERVER_PORT": str(PORT),
            "PGRST_SERVER_HOST": "127.0.0.1",
            "PGRST_DB_POOL": "2",
        }
    )
    log = open("/tmp/calendar-rls-postgrest.log", "w")
    process = subprocess.Popen(
        [_postgrest_bin()],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port(PORT)
    except Exception:
        process.kill()
        log.close()
        details = Path("/tmp/calendar-rls-postgrest.log").read_text()[-2000:]
        pytest.fail(f"postgrest failed to start:\n{details}")
    yield process
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    log.close()


@pytest.fixture(autouse=True)
def _seed(api):
    _seed_rows()
    yield


def _assert_secrets_hidden(body: str) -> None:
    leaked = [
        needle
        for needle in (
            APPLE_MEMBER,
            ICS_MEMBER,
            GOOGLE_MEMBER,
            APPLE_PASSWORD,
            APPLE_USER,
            APPLE_URL,
            ICS_URL,
            GOOGLE_ACCESS,
            GOOGLE_REFRESH,
        )
        if needle in body
    ]
    assert not leaked, f"calendar credentials were returned: {leaked}"


def test_busy_blocks_stay_hidden_from_anon(api):
    """Control: a table created the same way, with RLS enabled, is not readable."""
    status, body = _rest("anon", "GET", "/vendor_busy_blocks?select=*")
    assert "placeholder-member-busy" not in body
    assert status in (200, 401, 403)


def test_anon_jwt_cannot_read_calendar_credentials(api):
    status, body = _rest("anon", "GET", "/vendor_calendar_connections?select=*")
    _assert_secrets_hidden(body)
    status, projected = _rest(
        "anon",
        "GET",
        "/vendor_calendar_connections?select=access_token,refresh_token,caldav_url,caldav_username,caldav_password,ics_url",
    )
    _assert_secrets_hidden(projected)
    assert status in (200, 401, 403)


def test_authenticated_jwt_cannot_read_calendar_credentials(api):
    status, body = _rest("authenticated", "GET", "/vendor_calendar_connections?select=*")
    _assert_secrets_hidden(body)
    assert status in (200, 401, 403)


def test_anon_jwt_cannot_insert_calendar_credentials(api):
    _rest(
        "anon",
        "POST",
        "/vendor_calendar_connections",
        {
            "member_id": ATTACK_INSERT_MEMBER,
            "vendor_id": "default_vendor",
            "provider": "ics",
            "ics_url": ATTACK_ICS,
        },
        prefer="return=representation",
    )
    assert _count(ATTACK_INSERT_MEMBER) == 0


def test_authenticated_jwt_cannot_insert_calendar_credentials(api):
    _rest(
        "authenticated",
        "POST",
        "/vendor_calendar_connections",
        {
            "member_id": ATTACK_INSERT_MEMBER,
            "vendor_id": "default_vendor",
            "provider": "ics",
            "ics_url": ATTACK_ICS,
        },
        prefer="return=representation",
    )
    assert _count(ATTACK_INSERT_MEMBER) == 0


def test_anon_jwt_cannot_update_calendar_credentials(api):
    _rest(
        "anon",
        "PATCH",
        f"/vendor_calendar_connections?member_id=eq.{ICS_MEMBER}",
        {"ics_url": REPLACED_ICS},
        prefer="return=representation",
    )
    assert _column(ICS_MEMBER, "ics_url") == ICS_URL


def test_authenticated_jwt_cannot_update_calendar_credentials(api):
    _rest(
        "authenticated",
        "PATCH",
        f"/vendor_calendar_connections?member_id=eq.{ICS_MEMBER}",
        {"ics_url": REPLACED_ICS},
        prefer="return=representation",
    )
    assert _column(ICS_MEMBER, "ics_url") == ICS_URL


def test_anon_jwt_cannot_delete_calendar_credentials(api):
    _rest("anon", "DELETE", f"/vendor_calendar_connections?member_id=eq.{APPLE_MEMBER}")
    assert _count(APPLE_MEMBER) == 1


def test_authenticated_jwt_cannot_delete_calendar_credentials(api):
    _rest(
        "authenticated",
        "DELETE",
        f"/vendor_calendar_connections?member_id=eq.{APPLE_MEMBER}",
    )
    assert _count(APPLE_MEMBER) == 1


def test_rls_is_on_and_api_roles_have_no_table_privileges(api):
    flags = _psql(
        "select c.relrowsecurity::text from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relname = 'vendor_calendar_connections'"
    ).stdout.strip()
    assert flags == "true"
    for role in ("anon", "authenticated"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            allowed = _psql(
                "select has_table_privilege("
                f"{_sql_literal(role)}, 'public.vendor_calendar_connections', {_sql_literal(privilege)})"
            ).stdout.strip()
            assert allowed == "f", f"{role} still has {privilege}"


def test_service_role_can_still_read_calendar_credentials(api):
    status, body = _rest("service_role", "GET", "/vendor_calendar_connections?select=member_id")
    assert status == 200
    assert APPLE_MEMBER in body
    assert ICS_MEMBER in body
    assert GOOGLE_MEMBER in body
