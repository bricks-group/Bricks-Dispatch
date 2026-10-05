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

# In-memory store: ip -> [(timestamp, ...)]
_hits: dict[str, list[float]] = defaultdict(list)


def _clean(ip: str, window: int):
    now = time.time()
    cutoff = now - window
    _hits[ip] = [t for t in _hits[ip] if t > cutoff]
    if not _hits[ip]:
        del _hits[ip]


def _get_tier(path: str) -> tuple[int, int]:
    for prefix, max_r, window in _TIERS:
        if path.startswith(prefix):
            return max_r, window
    return _DEFAULT_LIMIT


def _rate_limit_key(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Path-based sliding window rate limiter."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip health and seed endpoints
        if request.url.path in ("/health", "/health/ready") or request.url.path.startswith("/api/seed"):
            return await call_next(request)

        ip = _rate_limit_key(request)
        max_r, window = _get_tier(request.url.path)

        _clean(ip, window)
        now = time.time()

        if len(_hits[ip]) >= max_r:
            oldest = _hits[ip][0]
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

        _hits[ip].append(now)
        remaining = max_r - len(_hits[ip])

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_r)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        return response
