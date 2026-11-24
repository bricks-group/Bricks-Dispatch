"""Cross-tenant access through the bookings row-level policy.

Applies the up section of migrations/004_create_bookings_table.sql on a local
PostgreSQL cluster, then opens a transaction the way PostgREST v14.18 does
for an authenticated JWT: set_config('role', ...) and
set_config('request.jwt.claims', ..., true). auth.role() / auth.uid() match
the GoTrue definitions that read those settings.

These tests fail while that migration's FOR ALL policy keys only off
auth.role() = 'authenticated'. They pass once an authenticated session cannot
read or change another user's booking, and cannot insert a booking, while
service_role still can. Dropping the open policy without replacing it with an
owner select (or revoking anon/authenticated grants) is enough for the
cross-tenant cases; leaving the open policy in place beside another
permissive policy is not.

Run:
  python3 backend/tests/test_bookings_authenticated_rls.py
Requires local PostgreSQL reachable as the postgres superuser via sudo.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "migrations" / "004_create_bookings_table.sql"
DB = f"bookings_rls_{os.getpid()}"

VICTIM = "11111111-1111-4111-8111-111111111111"
ATTACKER = "22222222-2222-4222-8222-222222222222"
SERVICE = "33333333-3333-4333-8333-333333333333"

VICTIM_ADDRESS = "10 Victim Street"
VICTIM_NOTES = "gate code 1234"
VICTIM_PRICE = "2499"
VICTIM_DATE = "2026-12-01"
VICTIM_TIME = "09:00"
VICTIM_JOB = "job-victim"
VICTIM_CONTACT = "+14165550100"
VICTIM_GUEST = "+14165550101"


def _psql(database: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-d",
            database,
            "-X",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )


def _drop_probe_databases() -> None:
    proc = _psql(
        "postgres",
        "SELECT datname FROM pg_database WHERE datname LIKE 'bookings_rls_%';\n",
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    for name in proc.stdout.splitlines():
        name = name.strip()
        if name.startswith("bookings_rls_") and name[13:].isdigit():
            _ok("postgres", f"DROP DATABASE IF EXISTS {name};\n")


def _reset_cluster(before_sql: str = "", after_sql: str = "") -> None:
    """Roles are cluster-wide. Drop probe databases first, then leftover roles."""
    _drop_probe_databases()
    _ok(
        "postgres",
        before_sql
        + """
        DO $$
        DECLARE
          r text;
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') THEN
            FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
            LOOP
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE %I FROM authenticator', r);
              END IF;
            END LOOP;
            DROP ROLE authenticator;
          END IF;
          FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
          LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format('DROP ROLE %I', r);
            END IF;
          END LOOP;
        END
        $$;
        """
        + after_sql,
    )


def _ok(database: str, sql: str) -> str:
    proc = _psql(database, sql)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout


def _up_migration() -> str:
    text = MIGRATION.read_text()
    marker = "-- Down migration"
    if marker not in text:
        raise AssertionError(f"{MIGRATION} has no up/down split")
    return text.split(marker, 1)[0]


def _claims(role: str, sub: str) -> str:
    # Dollar-quoted so the JSON reaches set_config unchanged.
    return (
        "SELECT set_config('role', '%s', true);\n"
        "SELECT set_config('request.jwt.claims', $claims${"
        '"role":"%s","sub":"%s"'
        "}$claims$, true);\n"
    ) % (role, role, sub)


def _session(role: str, sub: str, body: str) -> subprocess.CompletedProcess[str]:
    sql = "BEGIN;\n" + _claims(role, sub) + body + "\nROLLBACK;\n"
    return _psql(DB, sql)


def _denied(proc: subprocess.CompletedProcess[str]) -> bool:
    blob = (proc.stderr or "") + (proc.stdout or "")
    return proc.returncode != 0 and (
        "row-level security" in blob or "permission denied" in blob
    )


class BookingsAuthenticatedRlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _reset_cluster(after_sql=f"CREATE DATABASE {DB};\n")
        platform = """
        CREATE SCHEMA IF NOT EXISTS extensions;
        CREATE SCHEMA IF NOT EXISTS auth;
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
        CREATE ROLE anon NOLOGIN NOINHERIT;
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
        CREATE ROLE authenticator NOINHERIT LOGIN;
        GRANT anon TO authenticator;
        GRANT authenticated TO authenticator;
        GRANT service_role TO authenticator;
        GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
        GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;
        GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
          GRANT ALL ON TABLES TO anon, authenticated, service_role;
        CREATE OR REPLACE FUNCTION auth.role() RETURNS text
        LANGUAGE sql STABLE AS $$
          SELECT coalesce(
            nullif(current_setting('request.jwt.claim.role', true), ''),
            (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
          )::text
        $$;
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT coalesce(
            nullif(current_setting('request.jwt.claim.sub', true), ''),
            (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
          )::uuid
        $$;
        """
        _ok(DB, platform)
        _ok(DB, "SET search_path TO public, extensions;\n" + _up_migration())
        # The app reads these columns from public.bookings. The canonical
        # migration does not declare them; the row policy still covers every
        # column on the row, so they are part of the same check.
        _ok(
            DB,
            """
            ALTER TABLE public.bookings
              ADD COLUMN contact_phone text,
              ADD COLUMN guest_phone text;
            INSERT INTO public.bookings (
              booking_id, user_id, address, notes, package_price,
              scheduled_date, scheduled_time, dispatch_job_id,
              contact_phone, guest_phone
            ) VALUES (
              'victim-booking', '%s', '%s', '%s', %s,
              '%s', '%s', '%s', '%s', '%s'
            ), (
              'attacker-booking', '%s', '20 Attacker Street', 'own note', 100,
              '2026-12-02', '10:00', 'job-own', '+14165550102', NULL
            );
            """
            % (
                VICTIM,
                VICTIM_ADDRESS,
                VICTIM_NOTES,
                VICTIM_PRICE,
                VICTIM_DATE,
                VICTIM_TIME,
                VICTIM_JOB,
                VICTIM_CONTACT,
                VICTIM_GUEST,
                ATTACKER,
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        _reset_cluster()

    def test_authenticated_session_cannot_read_another_users_booking(self) -> None:
        proc = _session(
            "authenticated",
            ATTACKER,
            """
            SELECT 'LEAK|' || booking_id || '|' || address || '|' || coalesce(notes, '')
              || '|' || package_price::text || '|' || scheduled_date || '|' || scheduled_time
              || '|' || coalesce(dispatch_job_id, '')
              || '|' || coalesce(contact_phone, '') || '|' || coalesce(guest_phone, '')
            FROM public.bookings
            ORDER BY booking_id;
            """,
        )
        if _denied(proc):
            return
        self.assertEqual(proc.returncode, 0, proc.stderr)
        leaked = [line for line in proc.stdout.splitlines() if line.startswith("LEAK|")]
        blob = "\n".join(leaked)
        self.assertNotIn("victim-booking", blob)
        self.assertNotIn(VICTIM_ADDRESS, blob)
        self.assertNotIn(VICTIM_NOTES, blob)
        self.assertNotIn(VICTIM_JOB, blob)
        self.assertNotIn(VICTIM_CONTACT, blob)
        self.assertNotIn(VICTIM_GUEST, blob)
        self.assertNotIn(f"|{VICTIM_PRICE}|", blob)
        self.assertNotIn(VICTIM_DATE, blob)
        self.assertNotIn(VICTIM_TIME, blob)

    def test_authenticated_session_cannot_update_another_users_booking(self) -> None:
        proc = _session(
            "authenticated",
            ATTACKER,
            """
            UPDATE public.bookings
               SET address = 'TAMPERED',
                   notes = 'TAMPERED',
                   package_price = 1,
                   scheduled_date = '1999-01-01',
                   scheduled_time = '00:00',
                   dispatch_job_id = 'TAMPERED',
                   contact_phone = '+10000000000',
                   guest_phone = '+10000000001'
             WHERE booking_id = 'victim-booking';
            RESET ROLE;
            SELECT 'AFTER|' || address || '|' || notes || '|' || package_price::text
              || '|' || scheduled_date || '|' || scheduled_time
              || '|' || coalesce(dispatch_job_id, '')
              || '|' || coalesce(contact_phone, '') || '|' || coalesce(guest_phone, '')
            FROM public.bookings WHERE booking_id = 'victim-booking';
            """,
        )
        if _denied(proc):
            return
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = [line for line in proc.stdout.splitlines() if line.startswith("AFTER|")]
        self.assertEqual(
            after,
            [
                "AFTER|%s|%s|%s|%s|%s|%s|%s|%s"
                % (
                    VICTIM_ADDRESS,
                    VICTIM_NOTES,
                    VICTIM_PRICE,
                    VICTIM_DATE,
                    VICTIM_TIME,
                    VICTIM_JOB,
                    VICTIM_CONTACT,
                    VICTIM_GUEST,
                )
            ],
        )

    def test_authenticated_session_cannot_delete_another_users_booking(self) -> None:
        proc = _session(
            "authenticated",
            ATTACKER,
            """
            DELETE FROM public.bookings WHERE booking_id = 'victim-booking';
            RESET ROLE;
            SELECT 'COUNT|' || count(*)::text
            FROM public.bookings WHERE booking_id = 'victim-booking';
            """,
        )
        if _denied(proc):
            return
        self.assertEqual(proc.returncode, 0, proc.stderr)
        counts = [line for line in proc.stdout.splitlines() if line.startswith("COUNT|")]
        self.assertEqual(counts, ["COUNT|1"])

    def test_authenticated_session_cannot_insert_a_booking(self) -> None:
        proc = _session(
            "authenticated",
            ATTACKER,
            """
            INSERT INTO public.bookings (
              booking_id, user_id, address, notes, package_price,
              scheduled_date, scheduled_time, dispatch_job_id,
              contact_phone, guest_phone
            ) VALUES (
              'injected-by-attacker', '%s', '99 Injected Avenue', 'pwn', 1,
              '2026-01-01', '01:00', 'job-injected',
              '+14165550198', '+14165550197'
            );
            RESET ROLE;
            SELECT 'COUNT|' || count(*)::text
            FROM public.bookings WHERE booking_id = 'injected-by-attacker';
            """
            % VICTIM,
        )
        if _denied(proc):
            return
        self.assertEqual(proc.returncode, 0, proc.stderr)
        counts = [line for line in proc.stdout.splitlines() if line.startswith("COUNT|")]
        self.assertEqual(counts, ["COUNT|0"])

    def test_anon_jwt_cannot_read_or_change_bookings(self) -> None:
        read = _session(
            "anon",
            ATTACKER,
            """
            SELECT 'LEAK|' || booking_id || '|' || address || '|' || coalesce(contact_phone, '')
            FROM public.bookings;
            """,
        )
        if not _denied(read):
            self.assertEqual(read.returncode, 0, read.stderr)
            leaked = [line for line in read.stdout.splitlines() if line.startswith("LEAK|")]
            self.assertEqual(leaked, [])

        deleted = _session(
            "anon",
            ATTACKER,
            """
            DELETE FROM public.bookings WHERE booking_id = 'victim-booking';
            RESET ROLE;
            SELECT 'COUNT|' || count(*)::text
            FROM public.bookings WHERE booking_id = 'victim-booking';
            """,
        )
        if _denied(deleted):
            return
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        counts = [line for line in deleted.stdout.splitlines() if line.startswith("COUNT|")]
        self.assertEqual(counts, ["COUNT|1"])

    def test_service_role_can_read_and_update_a_booking(self) -> None:
        proc = _session(
            "service_role",
            SERVICE,
            """
            UPDATE public.bookings
               SET notes = 'service-role-write'
             WHERE booking_id = 'victim-booking';
            RESET ROLE;
            SELECT 'NOTES|' || notes FROM public.bookings WHERE booking_id = 'victim-booking';
            """,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        notes = [line for line in proc.stdout.splitlines() if line.startswith("NOTES|")]
        self.assertEqual(notes, ["NOTES|service-role-write"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
