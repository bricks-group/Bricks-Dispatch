"""Free/busy sync must not refetch a stored calendar URL with an unguarded client.

Save-time checks (``vet_url_for_save`` + ``guarded_request``) are not enough: a feed
that was public HTTPS when it was saved can later redirect, or its DNS can point at
a non-global address, before ``ics_busy_intervals`` / ``caldav_busy_intervals`` run.

These tests drive the real httpx 0.28.1 / httpcore 1.0.9 / anyio 3.7.1 stack. DNS for
the attacker hostname is answered by the test, and connections the client addresses
to that hostname's public answer are delivered to a local TLS listener (the test
does not own that public address). Connections to any other address, including
loopback, are real.

The SSRF cases fail while sync still uses a raw client. They pass once every refetch
goes through ``calendar_url_guard.guarded_request`` (DNS re-checked, IP pinned, each
redirect hop re-checked). ``test_public_https_feed_still_returns_busy_blocks`` is the
matching non-regression: a stable public feed must still parse.

Run (conftest replaces httpx with a mock, so this file restores the real package
when it is imported alone):

    cd backend && python -m pytest --noconftest tests/test_calendar_sync_ssrf.py -q
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

# The suite conftest stubs httpx before collection. Drop that stub before this
# process imports the calendar modules, so their ``import httpx`` is the real one.
_mocked_httpx = [
    name for name in list(sys.modules)
    if name == "httpx" or name.startswith("httpx.")
]
if any(isinstance(sys.modules[name], MagicMock) for name in _mocked_httpx):
    for name in _mocked_httpx:
        del sys.modules[name]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            mod = sys.modules[name]
            if isinstance(getattr(mod, "httpx", None), MagicMock):
                del sys.modules[name]

import httpx  # noqa: E402

if isinstance(httpx, MagicMock):  # pragma: no cover
    raise RuntimeError("real httpx is not importable; install httpx==0.28.1")

import pytest  # noqa: E402

from app.routers import vendor_os  # noqa: E402
from app.services import vendor_busy_sync as busy  # noqa: E402
from app.services.vendor_calendars import (  # noqa: E402
    persist_apple_caldav_if_verified,
    persist_ics_if_verified,
)

HOST = "cal.example"
# Public, global address (ipaddress.ip_address(...).is_global). The client is
# told this is the feed's A record; connect() to it is handed to the local
# attacker listener below. Nothing is sent to this address on the network.
PUBLIC = "93.184.216.34"
MEMBER = "member-applicant"
VENDOR = "vendor-1"
# Obvious placeholder. Not a credential for any real account.
CALDAV_PASSWORD = "placeholder-caldav-password"
CALDAV_USER = "calendar-user"
CRON_SECRET = "placeholder-cron-secret"
ICS_URL = f"https://{HOST}/cal.ics"
CALDAV_URL = f"https://{HOST}/calendars/user/"

_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CONNECT = socket.socket.connect


def _event_day() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d")


def _ics_bytes() -> bytes:
    day = _event_day()
    return (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{day}T150000Z\r\n"
        f"DTEND:{day}T170000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode()


def _report_bytes() -> bytes:
    return (
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<D:multistatus xmlns:D='DAV:'>"
        b"<D:response><D:propstat><D:prop>"
        b"<C:calendar-data xmlns:C='urn:ietf:params:xml:ns:caldav'><![CDATA["
        + _ics_bytes()
        + b"]]></C:calendar-data></D:prop></D:propstat></D:response></D:multistatus>"
    )


def _read_http(conn: socket.socket) -> tuple[str, bool]:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    text = head.decode("iso-8859-1", "replace")
    lines = text.split("\r\n") if text else []
    length = 0
    for line in lines[1:]:
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                length = 0
    pending = length - len(rest)
    while pending > 0:
        chunk = conn.recv(min(4096, pending))
        if not chunk:
            break
        pending -= len(chunk)
    has_auth = any(line.lower().startswith("authorization:") for line in lines)
    return (lines[0] if lines else ""), has_auth


def _http_response(status: bytes, body: bytes) -> bytes:
    return (
        b"HTTP/1.1 " + status
        + b"\r\nContent-Length: " + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n" + body
    )


class _Query:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters: list[tuple[str, object]] = []
        self.op = "select"
        self.payload = None
        self.conflict = ""
        self.one = False

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, *_a, **_k):
        return self

    def lt(self, *_a, **_k):
        return self

    def gt(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.one = True
        return self

    def upsert(self, row, on_conflict=None):
        self.op = "upsert"
        self.payload = row
        self.conflict = on_conflict or ""
        return self

    def update(self, patch):
        self.op = "update"
        self.payload = patch
        return self

    def delete(self):
        self.op = "delete"
        return self

    def insert(self, rows):
        self.op = "insert"
        self.payload = rows
        return self

    def _match(self, row):
        return all(row.get(key) == value for key, value in self.filters)

    async def execute(self):
        if self.name in self.db.broken:
            raise RuntimeError("relation missing")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "select":
            found = [dict(row) for row in rows if self._match(row)]
            if self.one:
                return SimpleNamespace(data=found[0] if found else None)
            return SimpleNamespace(data=found)
        if self.op == "upsert":
            row = dict(self.payload)
            keys = [part.strip() for part in self.conflict.split(",") if part.strip()]
            for index, existing in enumerate(rows):
                if keys and all(existing.get(key) == row.get(key) for key in keys):
                    rows[index] = row
                    break
            else:
                rows.append(row)
            return SimpleNamespace(data=[row])
        if self.op == "update":
            for row in rows:
                if self._match(row):
                    row.update(self.payload)
            return SimpleNamespace(data=[])
        if self.op == "delete":
            self.db.tables[self.name] = [row for row in rows if not self._match(row)]
            return SimpleNamespace(data=[])
        payload = self.payload if isinstance(self.payload, list) else [self.payload]
        rows.extend(payload)
        return SimpleNamespace(data=payload)


class MemDB:
    def __init__(self):
        self.tables: dict[str, list] = {
            "vendor_calendar_connections": [],
            "vendor_busy_blocks": [],
            "vendor_members": [],
            "jobs": [],
        }
        self.broken: set[str] = set()

    def table(self, name):
        return _Query(self, name)


class Lab:
    def __init__(self):
        self.mode = "public"  # public | private | flip
        self.lookups = 0
        self.redirect = False
        self.forbidden: list[tuple] = []
        self.internal: list[str] = []
        self.db = MemDB()
        self.attacker_port = 0
        self.internal_port = 0
        self._attacker = None
        self._internal = None
        self._priv = None
        self.loop_log = ""
        self.ready = ""
        self._saved_env: dict[str, str | None] = {}

    def calendar_response(self, method: str) -> bytes:
        if self.redirect:
            loc = f"http://127.0.0.1:{self.internal_port}/secret".encode()
            return (
                b"HTTP/1.1 302 Found\r\nLocation: " + loc
                + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        if method == "REPORT":
            return _http_response(b"207 Multi-Status", _report_bytes())
        return _http_response(b"200 OK", _ics_bytes())

    def reset_observe(self):
        self.lookups = 0
        self.forbidden.clear()
        self.internal.clear()
        if self.loop_log and os.path.exists(self.loop_log):
            open(self.loop_log, "w").close()

    def loop_hits(self) -> list[dict]:
        if not self.loop_log or not os.path.exists(self.loop_log):
            return []
        rows = []
        for line in open(self.loop_log, encoding="utf-8"):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def arm(self, mode: str, redirect: bool):
        self.mode = mode
        self.redirect = redirect
        self.reset_observe()


def _host_text(host) -> str:
    if isinstance(host, bytes):
        return host.decode("ascii", "replace")
    return str(host or "")


def _install(lab: Lab):
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if _host_text(host) == HOST:
            lab.lookups += 1
            if lab.mode == "private" or (lab.mode == "flip" and lab.lookups > 1):
                ip = "127.0.0.1"
            else:
                ip = PUBLIC
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port)))]
        return _REAL_GETADDRINFO(host, port, family, type, proto, flags)

    def connect(sock, addr):
        ip = addr[0] if isinstance(addr, tuple) and addr else None
        if ip == PUBLIC:
            return _REAL_CONNECT(sock, ("127.0.0.1", lab.attacker_port))
        if isinstance(ip, str):
            try:
                parsed = ipaddress.ip_address(ip.split("%", 1)[0])
            except ValueError:
                parsed = None
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
                parsed = parsed.ipv4_mapped
            if parsed is not None and not parsed.is_global:
                lab.forbidden.append(tuple(addr))
        return _REAL_CONNECT(sock, addr)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect


def _restore_socket():
    socket.getaddrinfo = _REAL_GETADDRINFO
    socket.socket.connect = _REAL_CONNECT


def _serve_attacker(lab: Lab, ctx: ssl.SSLContext):
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw.bind(("127.0.0.1", 0))
    raw.listen(64)
    lab.attacker_port = raw.getsockname()[1]

    def handle(conn):
        try:
            secured = ctx.wrap_socket(conn, server_side=True)
            request, _auth = _read_http(secured)
            method = request.split(" ", 1)[0] if request else "GET"
            secured.sendall(lab.calendar_response(method))
            secured.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def loop():
        while True:
            try:
                conn, _addr = raw.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return raw


def _serve_internal(lab: Lab):
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    raw.bind(("127.0.0.1", 0))
    raw.listen(64)
    lab.internal_port = raw.getsockname()[1]

    def handle(conn):
        try:
            request, _auth = _read_http(conn)
            lab.internal.append(request)
            conn.sendall(_http_response(b"200 OK", _ics_bytes()))
            conn.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def loop():
        while True:
            try:
                conn, _addr = raw.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return raw


_LOOP_SERVER = r"""
import json, os, socket, ssl, sys
cert, key, log_path, ready_path, day = sys.argv[1:6]
ics = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:%sT150000Z\r\nDTEND:%sT170000Z\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n" % (day, day)
).encode()
report = (
    b"<?xml version='1.0' encoding='utf-8'?>"
    b"<D:multistatus xmlns:D='DAV:'><D:response><D:propstat><D:prop>"
    b"<C:calendar-data xmlns:C='urn:ietf:params:xml:ns:caldav'><![CDATA["
    + ics +
    b"]]></C:calendar-data></D:prop></D:propstat></D:response></D:multistatus>"
)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
try:
    ctx.set_alpn_protocols(["http/1.1"])
except Exception:
    pass
raw = socket.socket()
raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
raw.bind(("127.0.0.1", 443))
raw.listen(64)
with open(ready_path, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
while True:
    conn, _addr = raw.accept()
    try:
        secured = ctx.wrap_socket(conn, server_side=True)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = secured.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", "replace").split("\r\n") if head else []
        length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = 0
        pending = length - len(rest)
        while pending > 0:
            chunk = secured.recv(min(4096, pending))
            if not chunk:
                break
            pending -= len(chunk)
        request = lines[0] if lines else ""
        has_auth = any(line.lower().startswith("authorization:") for line in lines)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"request": request, "has_authorization": has_auth}) + "\n")
        method = request.split(" ", 1)[0] if request else "GET"
        body = report if method == "REPORT" else ics
        status = b"207 Multi-Status" if method == "REPORT" else b"200 OK"
        secured.sendall(
            b"HTTP/1.1 " + status + b"\r\nContent-Length: " + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n" + body
        )
        secured.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
"""


def _mint_cert(directory: str) -> tuple[str, str, str]:
    ca = os.path.join(directory, "ca.pem")
    key = os.path.join(directory, "srv.key")
    cert = os.path.join(directory, "srv.pem")
    serial = os.path.join(directory, "ca.srl")
    subprocess.check_call(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", os.path.join(directory, "ca.key"),
         "-out", ca, "-days", "2", "-nodes", "-subj", "/CN=Calendar Sync Test CA"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["openssl", "req", "-newkey", "rsa:2048", "-keyout", key, "-out", os.path.join(directory, "srv.csr"),
         "-nodes", "-subj", f"/CN={HOST}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ext = os.path.join(directory, "san.cnf")
    with open(ext, "w", encoding="utf-8") as handle:
        handle.write(f"subjectAltName=DNS:{HOST}\n")
    subprocess.check_call(
        ["openssl", "x509", "-req", "-in", os.path.join(directory, "srv.csr"), "-CA", ca,
         "-CAkey", os.path.join(directory, "ca.key"), "-CAserial", serial, "-CAcreateserial",
         "-out", cert, "-days", "2", "-extfile", ext],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return ca, cert, key


def _assert_quiet(lab: Lab, result=None):
    evidence = {
        "forbidden_connects": lab.forbidden,
        "redirect_target_requests": lab.internal,
        "loopback_443": lab.loop_hits(),
        "sync": result,
    }
    assert not lab.forbidden and not lab.internal and not lab.loop_hits(), evidence


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    work = tmp_path_factory.mktemp("calssrf")
    ca, cert, key = _mint_cert(str(work))
    box = Lab()
    box.loop_log = str(work / "loop443.log")
    box.ready = str(work / "loop443.ready")
    open(box.loop_log, "w").close()
    script = work / "loop443.py"
    script.write_text(_LOOP_SERVER, encoding="utf-8")
    proc = subprocess.Popen(
        ["sudo", "-n", "/usr/bin/python3", str(script), cert, key, box.loop_log, box.ready, _event_day()],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.time() + 5
    while time.time() < deadline and not os.path.exists(box.ready):
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", "replace")
            raise RuntimeError(f"loopback:443 listener failed to start: {err}")
        time.sleep(0.05)
    if not os.path.exists(box.ready):
        proc.kill()
        raise RuntimeError("loopback:443 listener did not become ready")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except Exception:
        pass
    attacker = _serve_attacker(box, ctx)
    internal = _serve_internal(box)
    _install(box)

    keys = (
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
        "CALENDAR_TOKEN_ENCRYPTION_KEY",
    )
    for name in keys:
        box._saved_env[name] = os.environ.get(name)
    os.environ["SSL_CERT_FILE"] = ca
    for name in keys:
        if name != "SSL_CERT_FILE":
            os.environ.pop(name, None)

    try:
        yield box
    finally:
        _restore_socket()
        for name, value in box._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        attacker.close()
        internal.close()
        pid = open(box.ready, encoding="utf-8").read().strip() if os.path.exists(box.ready) else ""
        if pid:
            subprocess.call(["sudo", "-n", "kill", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=5)


def _now():
    return datetime.now(timezone.utc)


def _window():
    start = _now()
    return start, start + timedelta(days=10)


async def _stage_ics(lab: Lab):
    lab.arm("public", redirect=False)
    lab.db = MemDB()
    saved = await persist_ics_if_verified(lab.db, member_id=MEMBER, vendor_id=VENDOR, ics_url=ICS_URL)
    assert saved.get("connected") is True, saved
    return saved


async def _stage_apple(lab: Lab):
    lab.arm("public", redirect=False)
    lab.db = MemDB()
    saved = await persist_apple_caldav_if_verified(
        lab.db, member_id=MEMBER, vendor_id=VENDOR, caldav_url=CALDAV_URL,
        password=CALDAV_PASSWORD, username=CALDAV_USER,
    )
    assert saved.get("connected") is True, saved
    return saved


def test_ics_redirect_after_save_is_not_followed(lab):
    """A feed that passed the save-time guard can 302 to loopback on the next sync.

    DNS flips after the first lookup of that sync, so a fix that checks the name
    and then connects to a fresh lookup still reaches loopback.
    """
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)

    async def run():
        return await busy.sync_member(lab.db, MEMBER, {"id": MEMBER, "email": "applicant@example.test"}, now=_now())

    result = asyncio.run(run())
    _assert_quiet(lab, result)


def test_ics_dns_rebind_is_not_contacted(lab):
    asyncio.run(_stage_ics(lab))
    lab.arm("private", redirect=False)
    start, end = _window()
    result = asyncio.run(busy.ics_busy_intervals(ICS_URL, start, end))
    _assert_quiet(lab, result)


def test_caldav_dns_rebind_does_not_send_report(lab):
    asyncio.run(_stage_apple(lab))
    lab.arm("private", redirect=False)

    async def run():
        return await busy.sync_member(lab.db, MEMBER, {"id": MEMBER, "email": CALDAV_USER}, now=_now())

    result = asyncio.run(run())
    _assert_quiet(lab, result)
    # If a request did land, it must not have carried the stored password.
    # Reached only once the connect itself is gone; kept so a partial fix that
    # still speaks HTTP to loopback cannot pass by emptying the connect log.
    for hit in lab.loop_hits():
        assert hit.get("has_authorization") is not True


def test_ics_second_lookup_after_a_public_check_is_not_used(lab):
    """Check-then-connect: first answer is global, the next answer is loopback.

    The unfixed client follows the redirect from the first answer. A fix that
    resolves, accepts the global address, then requests the hostname again
    connects to loopback directly.
    """
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)
    start, end = _window()
    result = asyncio.run(busy.ics_busy_intervals(ICS_URL, start, end))
    _assert_quiet(lab, result)


def test_caldav_second_lookup_after_a_public_check_is_not_used(lab):
    """CalDAV does not follow redirects. This catches a refetch that looks the
    name up again after a global answer. The unfixed client uses that first
    answer and does not open loopback; ``test_caldav_dns_rebind_does_not_send_report``
    is the case that fails on the unfixed client.
    """
    asyncio.run(_stage_apple(lab))
    lab.arm("flip", redirect=False)
    start, end = _window()
    result = asyncio.run(busy.caldav_busy_intervals(CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD, start, end))
    _assert_quiet(lab, result)


def test_applicant_sync_route_does_not_reach_loopback(lab):
    lab.arm("public", redirect=False)
    lab.db = MemDB()

    async def connect_then_sync():
        async def db():
            return lab.db

        async def actor(*_a, **_k):
            return MEMBER

        async def member(*_a, **_k):
            return {
                "id": MEMBER, "vendor_id": VENDOR, "role": "applicant",
                "email": "applicant@example.test", "onboarding_status": "submitted",
            }

        vendor_os.get_db = db
        vendor_os.resolve_vendor_actor_id = actor
        vendor_os.get_vendor_member = member
        saved = await vendor_os.connect_vendor_calendar(
            "ics", payload={"ics_url": ICS_URL}, authorization="Bearer session", x_user_id=None,
        )
        assert saved.get("connected") is True, saved
        lab.arm("flip", redirect=True)
        return await vendor_os.calendars_sync_self(authorization="Bearer session", x_user_id=None)

    result = asyncio.run(connect_then_sync())
    _assert_quiet(lab, result)


def test_cron_sync_does_not_reach_loopback(lab):
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)
    previous = os.environ.get("CRON_SECRET")
    os.environ["CRON_SECRET"] = CRON_SECRET

    async def run():
        async def db():
            return lab.db

        vendor_os.get_db = db
        return await vendor_os.calendars_sync_cron(
            authorization=f"Bearer {CRON_SECRET}", x_internal_key=None,
        )

    try:
        result = asyncio.run(run())
    finally:
        if previous is None:
            os.environ.pop("CRON_SECRET", None)
        else:
            os.environ["CRON_SECRET"] = previous
    _assert_quiet(lab, result)


def test_stale_sync_does_not_reach_loopback(lab):
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)
    asyncio.run(busy.sync_stale(lab.db, max_age_min=60, budget_s=6))
    _assert_quiet(lab, None)


def test_availability_read_does_not_reach_loopback(lab):
    """Public booking availability calls ``sync_stale`` before it answers."""
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)
    result = asyncio.run(
        busy.availability_days(lab.db, _now().date(), 3, "", "America/Toronto", now=_now())
    )
    _assert_quiet(lab, result)


def test_member_column_fallback_url_is_not_fetched_unguarded(lab):
    """If ``vendor_calendar_connections`` cannot be read, sync falls back to
    ``vendor_members.calendar_ics_url`` and still has to vet that URL."""
    lab.db = MemDB()
    lab.db.broken.add("vendor_calendar_connections")
    lab.db.tables["vendor_members"].append({
        "id": MEMBER,
        "calendar_ics_url": ICS_URL,
        "role": "applicant",
    })
    lab.arm("flip", redirect=True)
    result = asyncio.run(busy.sync_member(lab.db, MEMBER, {"id": MEMBER}, now=_now()))
    _assert_quiet(lab, result)


def test_sync_all_does_not_reach_loopback(lab):
    asyncio.run(_stage_ics(lab))
    lab.arm("flip", redirect=True)
    result = asyncio.run(busy.sync_all(lab.db))
    _assert_quiet(lab, result)


def test_public_https_feed_still_returns_busy_blocks(lab):
    """A feed that stays on its global address, with no redirect, still syncs.

    This passes before and after the fix. A change that stops every fetch
    would hide the SSRF cases without preserving calendar sync.
    """
    asyncio.run(_stage_ics(lab))
    lab.arm("public", redirect=False)
    result = asyncio.run(busy.sync_member(lab.db, MEMBER, {"id": MEMBER}, now=_now()))
    _assert_quiet(lab, result)
    assert result["providers"]["ics"]["status"] == "ok"
    assert result["providers"]["ics"]["blocks"] >= 1

    asyncio.run(_stage_apple(lab))
    lab.arm("public", redirect=False)
    result = asyncio.run(busy.sync_member(lab.db, MEMBER, {"id": MEMBER, "email": CALDAV_USER}, now=_now()))
    _assert_quiet(lab, result)
    assert result["providers"]["apple"]["status"] == "ok"
    assert result["providers"]["apple"]["blocks"] >= 1
