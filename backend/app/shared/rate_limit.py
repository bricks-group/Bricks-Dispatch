"""
Custom in-memory rate limiter for Bricks GO.
Path-based tiered rate limiting with sliding window.
Distinct from the decorator-based pattern used by Booking/Brokerage.
"""
import time
import logging
from collections import defaultdict
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable

logger = logging.getLogger(__name__)

# Tier definitions: path prefix -> max requests per window (seconds)
_TIERS: list[tuple[str, int, int]] = [
    # (prefix, max_requests, window_seconds)
    # Auth (vendors + onboarding submit)
    ("/api/vendor-os/auth/", 10, 60),               # All auth endpoints - 10/min
    ("/api/onboarding/apply", 10, 60),              # Onboarding submission - 10/min
    # Job intake (booking -> dispatch)
    ("/api/v1/dispatch/jobs", 30, 60),             # Job intake - 30/min
    # Job mutations (auto-assign, cancel, reschedule, status changes)
    ("/api/jobs/", 30, 60),                         # All job endpoints - 30/min
    # Notifications
    ("/api/notifications", 30, 60),                 # Notifications - 30/min
    # Push tokens
    ("/api/push-tokens", 30, 60),                  # Push tokens - 30/min
    # Uploads
    ("/api/uploads", 30, 60),                      # Uploads - 30/min
]

# Default limit
_DEFAULT_LIMIT = (100, 60)  # 100 requests per 60 seconds

# In-memory store: (ip, tier prefix) -> timestamps inside that tier's window.
# Each prefix has its own counter. Auth is 10/min and must not share a list
# with uploads or other routes, or a job screen of non-auth calls spends the
# auth budget and GO looks logged out.
_DEFAULT_BUCKET = "*"
_hits: dict[tuple[str, str], list[float]] = defaultdict(list)


def _clean(key: tuple[str, str], window: int):
    now = time.time()
    cutoff = now - window
    _hits[key] = [t for t in _hits[key] if t > cutoff]
    if not _hits[key]:
        del _hits[key]


def _get_tier(path: str) -> tuple[str, int, int]:
    """Return (bucket, max_requests, window_seconds) for this path."""
    for prefix, max_r, window in _TIERS:
        if path.startswith(prefix):
            return prefix, max_r, window
    return _DEFAULT_BUCKET, _DEFAULT_LIMIT[0], _DEFAULT_LIMIT[1]


def _rate_limit_key(request: Request) -> str:
    # X-Forwarded-For's first hop is client-controlled (spoofable). Vercel sets
    # x-real-ip to the connecting client, so key on that, then the socket peer.
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "127.0.0.1"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Path-based sliding window rate limiter."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip health and seed endpoints
        if request.url.path in ("/health", "/health/ready") or request.url.path.startswith("/api/seed"):
            return await call_next(request)

        ip = _rate_limit_key(request)
        bucket, max_r, window = _get_tier(request.url.path)
        key = (ip, bucket)

        _clean(key, window)
        now = time.time()

        if len(_hits[key]) >= max_r:
            oldest = _hits[key][0]
            retry_after = int(window - (now - oldest)) + 1
            logger.warning(f"Rate limit hit: {request.url.path} from {ip}")
            # This return never reaches inner middleware. CORS must wrap this
            # middleware (see install_browser_middleware) or the browser drops the 429.
            return Response(
                status_code=429,
                content='{"error":"rate_limited","detail":"Too many requests","retry_after":%d}' % retry_after,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_r),
                    "X-RateLimit-Remaining": "0",
                },
            )

        _hits[key].append(now)
        remaining = max_r - len(_hits[key])

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_r)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        return response
