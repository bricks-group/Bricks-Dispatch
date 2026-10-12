"""Server-side portfolio link check for vendor applications.

Before this existed the owner email said "The portfolio looks like a fit for
this work." whenever a text-only model decided the *link string* was not a
poor fit. Nobody ever opened the link, so a dead site (NXDOMAIN) still read as
a fit. This module actually fetches each public link with a short timeout and
reports only what it saw. There is no image or vision model here, so a page
that opens is reported as "opened, work not reviewed" and never as a fit.

Never raises. Every failure is a status, not an exception.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin, urlsplit

import httpx

FETCH_TIMEOUT_SECONDS = 5.0
MAX_LINKS = 4
MAX_REDIRECTS = 5
MAX_BYTES = 256 * 1024
USER_AGENT = (
    "Mozilla/5.0 (compatible; BricksPortfolioCheck/1.0; +https://brxs.ca)"
)

OK_PAGE = "ok_page"
OK_IMAGE = "ok_image"
INSTAGRAM = "instagram"
FAILED = "failed"

_INSTAGRAM_HOSTS = ("instagram.com", "instagr.am")

# Registrar / parking / for-sale pages. Any of these means parked.
_PARKED_MARKERS = (
    "this domain is for sale",
    "domain is for sale",
    "buy this domain",
    "domain may be for sale",
    "make an offer on this domain",
    "domain parking",
    "parked domain",
    "this domain is parked",
    "this domain has been registered",
    "this domain name has been registered",
    "hugedomains",
    "sedoparking",
    "afternic",
    "parkingcrew",
    "domain not configured",
    "there isn't a github pages site here",
    "account suspended",
    "website expired",
    "this site is currently unavailable",
)
# Placeholder copy. Only counts when the page is also small with no images,
# because a real portfolio can say "coming soon" somewhere.
_PLACEHOLDER_MARKERS = (
    "coming soon",
    "under construction",
    "site not found",
    "page not found",
    "launching soon",
    "website is under maintenance",
)

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img\b|<picture\b|og:image|background-image", re.I)


@dataclass(frozen=True)
class LinkCheck:
    url: str
    status: str  # ok_page | ok_image | instagram | failed
    detail: str = ""  # dns | http_404 | timeout | parked | empty | blocked | error


@dataclass(frozen=True)
class PortfolioCheck:
    links: tuple = ()

    @property
    def opened(self) -> list:
        return [c for c in self.links if c.status in (OK_PAGE, OK_IMAGE)]

    @property
    def failed(self) -> list:
        return [c for c in self.links if c.status == FAILED]

    @property
    def instagram(self) -> list:
        return [c for c in self.links if c.status == INSTAGRAM]

    @property
    def needs_review(self) -> bool:
        """True when nothing opened. Applicant is not a fit by default."""
        return not self.opened


def is_instagram(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _INSTAGRAM_HOSTS)


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_global and not addr.is_multicast


async def _host_is_public(host: str, port: int) -> Optional[bool]:
    """None on DNS failure, False if any address is private, True otherwise."""
    if not host:
        return None
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception:
        return None
    ips = {info[4][0] for info in infos}
    if not ips:
        return None
    return all(_ip_is_public(ip) for ip in ips)


def _visible_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def classify_html(html: str) -> str:
    """'parked', 'empty', or '' when the page looks like a real site."""
    low = (html or "").lower()
    text = _visible_text(low)
    has_images = bool(_IMG_RE.search(low))
    if any(marker in text for marker in _PARKED_MARKERS):
        return "parked"
    if not has_images and len(text) < 1500 and any(m in text for m in _PLACEHOLDER_MARKERS):
        return "parked"
    # A JS-only page (React/Wix shell) can have no text or <img> in the HTML.
    # Do not call that empty; only a page with nothing at all.
    if not has_images and len(text) < 40 and "<script" not in low:
        return "empty"
    return ""


async def _fetch(client: httpx.AsyncClient, url: str) -> LinkCheck:
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return LinkCheck(url, FAILED, "bad_url")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        public = await _host_is_public(parts.hostname, port)
        if public is None:
            return LinkCheck(url, FAILED, "dns")
        if public is False:
            return LinkCheck(url, FAILED, "blocked")
        try:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return LinkCheck(url, FAILED, f"http_{resp.status_code}")
                    current = urljoin(current, location)
                    continue
                if not (200 <= resp.status_code < 300):
                    return LinkCheck(url, FAILED, f"http_{resp.status_code}")
                ctype = (resp.headers.get("content-type") or "").lower()
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
        except httpx.TimeoutException:
            return LinkCheck(url, FAILED, "timeout")
        except httpx.ConnectError as exc:
            msg = str(exc).lower()
            if "name or service" in msg or "nodename" in msg or "getaddrinfo" in msg:
                return LinkCheck(url, FAILED, "dns")
            return LinkCheck(url, FAILED, "connect")
        except Exception:
            return LinkCheck(url, FAILED, "error")
        if ctype.startswith("image/"):
            return LinkCheck(url, OK_IMAGE if body else FAILED, "" if body else "empty")
        if not body.strip():
            return LinkCheck(url, FAILED, "empty")
        if "html" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            verdict = classify_html(body.decode("utf-8", errors="ignore"))
            if verdict:
                return LinkCheck(url, FAILED, verdict)
        return LinkCheck(url, OK_PAGE)
    return LinkCheck(url, FAILED, "redirects")


async def check_link(url: str, client: Optional[httpx.AsyncClient] = None) -> LinkCheck:
    if is_instagram(url):
        return LinkCheck(url, INSTAGRAM)
    try:
        if client is not None:
            return await asyncio.wait_for(_fetch(client, url), FETCH_TIMEOUT_SECONDS + 1)
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,image/*;q=0.9,*/*;q=0.5"},
        ) as own:
            return await asyncio.wait_for(_fetch(own, url), FETCH_TIMEOUT_SECONDS + 1)
    except asyncio.TimeoutError:
        return LinkCheck(url, FAILED, "timeout")
    except Exception:
        return LinkCheck(url, FAILED, "error")


async def check_portfolio(urls: Iterable[str]) -> PortfolioCheck:
    """Check up to MAX_LINKS public URLs in parallel. Never raises."""
    picked = []
    for url in urls or ():
        if url not in picked:
            picked.append(url)
    picked = picked[:MAX_LINKS]
    if not picked:
        return PortfolioCheck(())
    results = await asyncio.gather(*(check_link(u) for u in picked), return_exceptions=True)
    out = []
    for url, res in zip(picked, results):
        out.append(res if isinstance(res, LinkCheck) else LinkCheck(url, FAILED, "error"))
    return PortfolioCheck(tuple(out))


# ---------------------------------------------------------------------------
# Image scrape for the applicant review. Same SSRF guard as the link check:
# every hop's host must resolve to public addresses only.
# ---------------------------------------------------------------------------
PAGE_MAX_BYTES = 1024 * 1024
IMAGE_MAX_BYTES = 8 * 1024 * 1024
_IMG_SRC_RE = re.compile(
    r"""(?:<img\b[^>]*?\b(?:data-src|data-lazy-src|src)\s*=\s*["']([^"']+)["'])"""
    r"""|(?:<meta\b[^>]*?property\s*=\s*["']og:image["'][^>]*?content\s*=\s*["']([^"']+)["'])"""
    r"""|(?:<meta\b[^>]*?content\s*=\s*["']([^"']+)["'][^>]*?property\s*=\s*["']og:image["'])""",
    re.I,
)
_SRCSET_RE = re.compile(r"""\b(?:data-srcset|srcset)\s*=\s*["']([^"']+)["']""", re.I)
_SKIP_IMG = re.compile(r"(logo|icon|favicon|sprite|avatar|badge|placeholder|spinner|\.svg)(\b|$|\?)", re.I)
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com")


def is_video_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


async def _guarded_get(client: httpx.AsyncClient, url: str, cap: int) -> tuple:
    """(final_url, content_type, body) or (url, '', b'') on any failure."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return url, "", b""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if await _host_is_public(parts.hostname, port) is not True:
            return url, "", b""
        try:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        return url, "", b""
                    current = urljoin(current, loc)
                    continue
                if not (200 <= resp.status_code < 300):
                    return url, "", b""
                ctype = (resp.headers.get("content-type") or "").lower()
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > cap:
                        return current, ctype, b""  # too big: skip, never truncate an image
                return current, ctype, body
        except Exception:
            return url, "", b""
    return url, "", b""


def extract_image_urls(html: str, base_url: str, limit: int = 30) -> list:
    """Candidate photo URLs on a page, largest srcset entry first, no logos/icons."""
    out: list = []

    def _add(u: str):
        u = (u or "").strip()
        if not u or u.startswith("data:"):
            return
        full = urljoin(base_url, u)
        if not full.lower().startswith(("http://", "https://")) or _SKIP_IMG.search(full):
            return
        if full not in out:
            out.append(full)

    for m in _SRCSET_RE.finditer(html or ""):
        best, best_w = "", -1
        for part in m.group(1).split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = 0
            if len(bits) > 1 and bits[1].endswith("w"):
                try:
                    w = int(bits[1][:-1])
                except ValueError:
                    w = 0
            if w > best_w:
                best, best_w = bits[0], w
        _add(best)
    for m in _IMG_SRC_RE.finditer(html or ""):
        _add(m.group(1) or m.group(2) or m.group(3) or "")
    return out[:limit]


async def scrape_page_images(url: str, max_images: int = 6, min_side: int = 700) -> list:
    """Download up to max_images real photos from a public page. Never raises.

    Returns [(image_url, bytes)]. Tiny images (thumbnails, logos) are skipped.
    """
    from PIL import Image
    import io

    found: list = []
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,image/*;q=0.9,*/*;q=0.5"},
        ) as client:
            final, ctype, body = await _guarded_get(client, url, PAGE_MAX_BYTES)
            if not body:
                return []
            if ctype.startswith("image/"):
                candidates = [final]
                cache = {final: body}
            else:
                candidates = extract_image_urls(body.decode("utf-8", errors="ignore"), final)
                cache = {}
            sem = asyncio.Semaphore(4)

            async def _one(u):
                async with sem:
                    if u in cache:
                        return u, cache[u]
                    _f, ct, data = await _guarded_get(client, u, IMAGE_MAX_BYTES)
                    if not data or (ct and not ct.startswith("image/")):
                        return u, b""
                    return u, data

            results = await asyncio.gather(*(_one(u) for u in candidates[:18]))
            for u, data in results:
                if not data:
                    continue
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        if max(im.size) < min_side:
                            continue
                except Exception:
                    continue
                found.append((u, data))
                if len(found) >= max_images:
                    break
    except Exception:
        return found
    return found
