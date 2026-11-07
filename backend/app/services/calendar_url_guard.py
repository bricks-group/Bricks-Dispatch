"""SSRF guard for vendor-supplied calendar URLs (ICS feeds, Apple CalDAV).

Launch security audit 2026-11-07: ICS fetches used ``follow_redirects=True`` with
no private-network guard. This module:
- allows only https (webcal:// is rewritten to https://), default port or 443,
  no userinfo in the URL;
- resolves the host and rejects any non-global address (private, loopback,
  link-local incl. 169.254.169.254, CGNAT, reserved, multicast, unspecified);
- pins the connection to the vetted IP (Host header + TLS SNI = hostname), so DNS
  rebinding between check and connect cannot redirect it;
- follows at most 3 redirects manually, re-vetting every hop;
- streams the body and stops at MAX_BYTES (never reads past the limit).
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3


class UnsafeCalendarURL(ValueError):
    pass


def normalize_calendar_url(url: str) -> str:
    u = str(url or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[len("webcal://"):]
    parts = urlsplit(u)
    if parts.scheme.lower() != "https":
        raise UnsafeCalendarURL("Calendar URL must use https:// (or webcal://)")
    if not parts.hostname:
        raise UnsafeCalendarURL("Calendar URL has no host")
    if parts.username or parts.password:
        raise UnsafeCalendarURL("Calendar URL must not contain credentials")
    if parts.port not in (None, 443):
        raise UnsafeCalendarURL("Calendar URL must use the default https port")
    return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


def _vet_ips(ips: Iterable[str]) -> str:
    ips = list(ips)
    if not ips:
        raise UnsafeCalendarURL("Calendar host did not resolve")
    for raw in ips:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UnsafeCalendarURL("Calendar URL points at a private or reserved address")
    return ips[0]


def _parent(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    return ".".join(labels[-2:])


def vet_url_for_save(url: str) -> str:
    """Syntactic check at save time (no DNS, so a resolver hiccup never blocks a
    save). IP-literal hosts are vetted here; every fetch re-vets with DNS."""
    norm = normalize_calendar_url(url)
    host = urlsplit(norm).hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host.lower() in ("localhost",) or host.lower().endswith((".localhost", ".internal", ".local")):
            raise UnsafeCalendarURL("Calendar URL points at a private or reserved address")
        return norm
    _vet_ips([host])
    return norm


def vet_url(url: str, resolver: Optional[Callable[[str, int], list[str]]] = None) -> tuple[str, str, str]:
    """Return (normalized_url, hostname, pinned_ip) or raise UnsafeCalendarURL."""
    norm = normalize_calendar_url(url)
    host = urlsplit(norm).hostname or ""
    try:
        ips = (resolver or _default_resolver)(host, 443)
    except UnsafeCalendarURL:
        raise
    except Exception as e:
        raise UnsafeCalendarURL("Calendar host did not resolve") from e
    return norm, host, _vet_ips(ips)


async def guarded_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    content: Optional[bytes | str] = None,
    auth: Optional[tuple[str, str]] = None,
    timeout: float = 10.0,
    resolver: Optional[Callable[[str, int], list[str]]] = None,
    max_bytes: int = MAX_BYTES,
) -> tuple[int, str]:
    """Fetch a vetted calendar URL. Returns (status_code, text)."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        norm, host, ip = vet_url(current, resolver)
        parts = urlsplit(norm)
        ip_host = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit(("https", ip_host, parts.path or "/", parts.query, ""))
        req_headers = dict(headers or {})
        req_headers["Host"] = host
        # identity: the size limit applies to what we actually buffer (no bombs).
        req_headers["Accept-Encoding"] = "identity"
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            req = client.build_request(
                method, pinned, headers=req_headers, content=content,
                extensions={"sni_hostname": host},
            )
            resp = await client.send(req, stream=True, auth=auth)
            try:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = urljoin(norm, resp.headers["location"])
                    if resp.status_code == 303:
                        method, content = "GET", None
                    # Only forward credentials within the same parent domain
                    # (e.g. caldav.icloud.com -> p01-caldav.icloud.com).
                    if auth and _parent(urlsplit(normalize_calendar_url(current)).hostname or "") != _parent(host):
                        auth = None
                    continue
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise UnsafeCalendarURL("Calendar response is too large")
                return resp.status_code, bytes(buf).decode(resp.encoding or "utf-8", errors="replace")
            finally:
                await resp.aclose()
    raise UnsafeCalendarURL("Too many redirects")
