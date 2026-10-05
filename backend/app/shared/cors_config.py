"""Browser CORS allow-list for Dispatch.

Starlette runs the last middleware added first. RateLimitMiddleware returns
429 without calling downstream, so CORS must be added after the rate limiter.
Otherwise the browser hides the 429 (no Access-Control-Allow-Origin) and GO
treats the failed auth check as logged out.
"""
import os

from starlette.middleware.cors import CORSMiddleware

from app.shared.rate_limit import RateLimitMiddleware

DEFAULT_ALLOWED_ORIGINS = (
    "https://brokerage.brxs.ca,https://dispatch-api.brxs.ca,https://www.brxs.ca,"
    "https://brxs.ca,https://bricks-dispatch-web.vercel.app,https://app.brxs.ca,"
    "https://go.brxs.ca"
)

_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def install_browser_middleware(app) -> None:
    """Rate limit inside CORS. Do not add another short-circuit outside CORS."""
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=_CORS_METHODS,
        allow_headers=["*"],
    )
