"""Data API exposure of no-show and ops escalation tables.

These tests reproduce the request PostgREST 14.18 serves for
``GET|POST|PATCH|DELETE /rest/v1/<table>`` after it switches into the JWT
role. They fail while ``anon`` and ``authenticated`` can read or change
``public.agent_noshow_escalations``, ``public.reconciliation_events``, and
``public.ops_escalations``. They pass once those roles can no longer read or
change the rows and ``service_role`` still can.

The role switch and the SQL are the statements PostgREST emits
(``set_config('role', <jwt role>, true)`` in one transaction, then
SELECT / INSERT / UPDATE / DELETE). The platform grants this prelude copies
are the ones a Supabase database applies to tables created by ``postgres``:
``supabase/postgres`` tag 17.6.1.136,
``migrations/db/init-scripts/00000000000000-initial-schema.sql`` lines 29-40,
which the image's own catalog test records as default privileges for the
``postgres`` role (``nix/tests/expected/roles.out``).

Run (PostgreSQL and PostgREST 14.18 must be on PATH):

    pytest backend/tests/test_escalation_tables_rls.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TABLES = (
    "agent_noshow_escalations",
    "reconciliation_events",
    "ops_escalations",
)
# Placeholder only. Not a project credential.
JWT_SECRET = "test-jwt-secret-placeholder-32b!"
SENTINELS = {
    "agent_noshow_escalations": "555-0100-sentinel",
    "reconciliation_events": "sentinel-recon",
    "ops_escalations": "sentinel-ops",
}

# Same grants the init script applies for whichever role runs it. The SQL
# editor and dashboard apply migrations as ``postgres``, and the image catalog
# test expects these privileges on objects that role creates.
PLATFORM_PRELUDE = """
create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
create user authenticator noinherit;
grant anon to authenticator;
grant authenticated to authenticator;
grant service_role to authenticator;
grant usage on schema public to postgres, anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to postgres, anon, authenticated, service_role;
"""

SEEDS = {
    "agent_noshow_escalations": """
        truncate public.agent_noshow_escalations;
        insert into public.agent_noshow_escalations (job_id, agent_phone)
        values ('11111111-1111-1111-1111-111111111111', '555-0100-sentinel');
    """,
    "reconciliation_events": """
        truncate public.reconciliation_events;
        insert into public.reconciliation_events (event_type, note)
        values ('fee_without_retention', 'sentinel-recon');
    """,
    "ops_escalations": """
        truncate public.ops_escalations;
        insert into public.ops_escalations (escalation_type, error_message)
        values ('no_vendor', 'sentinel-ops');
    """,
}

READ_SQL = {
    "agent_noshow_escalations": "select agent_phone from public.agent_noshow_escalations",
    "reconciliation_events": "select note from public.reconciliation_events",
    "ops_escalations": "select error_message from public.ops_escalations",
}


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    matches = sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"))
    if matches:
        return str(matches[-1])
    pytest.fail(f"{name} is not installed; PostgreSQL and PostgREST 14.18 must be on PATH")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(role: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"role": role, "exp": int(time.time()) + 3600}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _psql(cluster: dict, user: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            cluster["psql"],
            "-h",
            cluster["sock"],
            "-p",
            str(cluster["pg_port"]),
            "-U",
            user,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-X",
            "-A",
            "-t",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )


def _psql_ok(cluster: dict, user: str, sql: str) -> str:
    result = _psql(cluster, user, sql)
    if result.returncode != 0:
        pytest.fail(f"psql as {user} failed:\n{result.stderr}\n{sql}")
    return result.stdout


def _migration_files() -> list[Path]:
    found = []
    for path in REPO.rglob("*.sql"):
        if "node_modules" in path.parts:
            continue
        text = path.read_text()
        if any(name in text for name in TABLES):
            found.append(path)
    return sorted(found)


def _http(cluster: dict, role: str, method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{cluster['http_port']}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_jwt(role)}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


@pytest.fixture(scope="module")
def cluster():
    base = Path(f"/tmp/escalation-rls-{os.getpid()}")
    if base.exists():
        shutil.rmtree(base)
    data = base / "data"
    sock = base / "sock"
    sock.mkdir(parents=True)
    pg_port = _free_port()
    http_port = _free_port()
    initdb = _bin("initdb")
    pg_ctl = _bin("pg_ctl")
    psql = _bin("psql")
    postgrest = _bin("postgrest")
    subprocess.run(
        [initdb, "-D", str(data), "-U", "postgres", "--auth-local=trust", "--auth-host=reject"],
        check=True,
        capture_output=True,
        text=True,
    )
    log = base / "postgres.log"
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(log),
            "-w",
            "-o",
            f"-p {pg_port} -k {sock} -c listen_addresses=",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = {
        "psql": psql,
        "pg_ctl": pg_ctl,
        "data": data,
        "sock": str(sock),
        "pg_port": pg_port,
        "http_port": http_port,
        "base": base,
    }
    try:
        _psql_ok(info, "postgres", PLATFORM_PRELUDE)
        files = _migration_files()
        names = [path.name for path in files]
        if "20260601_agent_noshow_escalations.sql" not in names:
            pytest.fail(f"noshow migration was not discovered; found {names}")
        if "20260601_ops_escalations.sql" not in names:
            pytest.fail(f"ops escalation migration was not discovered; found {names}")
        for path in files:
            applied = _psql(
                info,
                "postgres",
                f"set search_path to public;\n\\i {path}\n",
            )
            if applied.returncode != 0:
                pytest.fail(f"applying {path} failed:\n{applied.stderr}")
        nonce = REPO / "migrations" / "0024_calendar_oauth_nonces.sql"
        applied = _psql(info, "postgres", f"set search_path to public;\n\\i {nonce}\n")
        if applied.returncode != 0:
            pytest.fail(f"applying {nonce} failed:\n{applied.stderr}")
        schema = _psql_ok(
            info,
            "postgres",
            """
            select n.nspname
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where c.relname = 'agent_noshow_escalations';
            """,
        ).strip()
        if schema != "public":
            pytest.fail(f"agent_noshow_escalations landed in schema {schema!r}, not public")
        env = os.environ.copy()
        env.update(
            {
                "PGRST_DB_URI": (
                    f"postgresql://authenticator@/postgres?host={sock}&port={pg_port}"
                ),
                "PGRST_DB_SCHEMAS": "public",
                "PGRST_DB_ANON_ROLE": "anon",
                "PGRST_JWT_SECRET": JWT_SECRET,
                "PGRST_SERVER_HOST": "127.0.0.1",
                "PGRST_SERVER_PORT": str(http_port),
                "PGRST_DB_CHANNEL_ENABLED": "false",
                "PGRST_LOG_LEVEL": "error",
            }
        )
        proc = subprocess.Popen(
            [postgrest],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        info["postgrest"] = proc
        deadline = time.time() + 10
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"postgrest exited {proc.returncode}:\n{output}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{http_port}/", timeout=0.5
                ) as response:
                    if response.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)
        if not ready:
            pytest.fail("postgrest did not open its port")
        yield info
    finally:
        proc = info.get("postgrest")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        subprocess.run(
            [pg_ctl, "-D", str(data), "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(base, ignore_errors=True)


def _reseed(cluster: dict, table: str) -> None:
    _psql_ok(cluster, "postgres", SEEDS[table])


def _scalar(cluster: dict, sql: str) -> str:
    return _psql_ok(cluster, "postgres", sql).strip()


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_role_cannot_read_escalation_rows(cluster, role, table):
    _reseed(cluster, table)
    status, body = _http(cluster, role, "GET", f"/{table}?select=*")
    assert SENTINELS[table] not in body, (
        f"{role} read {table} through PostgREST (HTTP {status}): {body}"
    )
    assert _scalar(cluster, READ_SQL[table]) == SENTINELS[table]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_role_cannot_insert_escalation_rows(cluster, role, table):
    _reseed(cluster, table)
    payloads = {
        "agent_noshow_escalations": {
            "job_id": "22222222-2222-2222-2222-222222222222",
            "agent_phone": "555-0199-attacker",
        },
        "reconciliation_events": {
            "event_type": "booking_missing",
            "note": "attacker-recon",
        },
        "ops_escalations": {
            "escalation_type": "attacker",
            "error_message": "attacker-ops",
        },
    }
    _http(cluster, role, "POST", f"/{table}", payloads[table])
    checks = {
        "agent_noshow_escalations": "select count(*) from public.agent_noshow_escalations where agent_phone = '555-0199-attacker'",
        "reconciliation_events": "select count(*) from public.reconciliation_events where note = 'attacker-recon'",
        "ops_escalations": "select count(*) from public.ops_escalations where error_message = 'attacker-ops'",
    }
    assert _scalar(cluster, checks[table]) == "0", f"{role} inserted into {table}"


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_role_cannot_update_escalation_rows(cluster, role, table):
    _reseed(cluster, table)
    requests = {
        "agent_noshow_escalations": (
            "/agent_noshow_escalations?agent_phone=eq.555-0100-sentinel",
            {"agent_phone": "555-0199-patched"},
            "select count(*) from public.agent_noshow_escalations where agent_phone = '555-0199-patched'",
        ),
        "reconciliation_events": (
            "/reconciliation_events?note=eq.sentinel-recon",
            {"resolved": True, "note": "attacker-recon"},
            "select count(*) from public.reconciliation_events where note = 'attacker-recon' or resolved = true",
        ),
        "ops_escalations": (
            "/ops_escalations?error_message=eq.sentinel-ops",
            {"status": "resolved", "error_message": "attacker-ops"},
            "select count(*) from public.ops_escalations where status = 'resolved' or error_message = 'attacker-ops'",
        ),
    }
    path, body, check = requests[table]
    _http(cluster, role, "PATCH", path, body)
    assert _scalar(cluster, check) == "0", f"{role} updated {table}"
    assert _scalar(cluster, READ_SQL[table]) == SENTINELS[table]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_role_cannot_delete_escalation_rows(cluster, role, table):
    _reseed(cluster, table)
    filters = {
        "agent_noshow_escalations": "agent_phone=eq.555-0100-sentinel",
        "reconciliation_events": "note=eq.sentinel-recon",
        "ops_escalations": "error_message=eq.sentinel-ops",
    }
    _http(cluster, role, "DELETE", f"/{table}?{filters[table]}")
    assert _scalar(cluster, READ_SQL[table]) == SENTINELS[table], f"{role} deleted from {table}"


@pytest.mark.parametrize("table", TABLES)
def test_service_role_can_still_read_escalation_rows(cluster, table):
    _reseed(cluster, table)
    status, body = _http(cluster, "service_role", "GET", f"/{table}?select=*")
    assert status == 200, body
    assert SENTINELS[table] in body


def test_calendar_oauth_nonces_stay_hidden_from_anon(cluster):
    """Control: a table created with the same default grants, then locked, is not readable."""
    _psql_ok(
        cluster,
        "postgres",
        """
        truncate public.calendar_oauth_nonces;
        insert into public.calendar_oauth_nonces (nonce_sha256, member_id, provider)
        values ('sentinel-nonce', 'member-1', 'google');
        """,
    )
    status, body = _http(cluster, "anon", "GET", "/calendar_oauth_nonces?select=*")
    assert "sentinel-nonce" not in body, f"anon read calendar_oauth_nonces (HTTP {status}): {body}"
    assert _scalar(
        cluster,
        "select nonce_sha256 from public.calendar_oauth_nonces",
    ) == "sentinel-nonce"
