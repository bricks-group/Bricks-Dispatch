import os
from pathlib import Path
from dotenv import load_dotenv
from supabase.client import create_async_client, AsyncClient
from supabase.lib.client_options import AsyncClientOptions

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

if not url or not key:
    print("Warning: SUPABASE_URL or SUPABASE_KEY not set")

_db: AsyncClient = None

def _service_header_intact(client) -> bool:
    try:
        return client.options.headers.get("Authorization") == f"Bearer {key}"
    except Exception:
        return True


async def get_db() -> AsyncClient:
    """Shared service-role client. Never sign a user in on it: supabase-py then
    sends that user's JWT on every table call (RLS). If that ever happened,
    drop it and make a fresh service-role client."""
    global _db
    if _db is not None and not _service_header_intact(_db):
        _db = None
    if _db is None:
        if not url or not key:
            raise RuntimeError("Supabase not configured: SUPABASE_URL and SUPABASE_KEY required")
        _db = await create_async_client(url, key)
    return _db


async def new_auth_client() -> AsyncClient:
    """Throwaway client for user auth calls (verify_otp, sign-in). Its session
    never touches the shared service-role client."""
    if not url or not key:
        raise RuntimeError("Supabase not configured: SUPABASE_URL and SUPABASE_KEY required")
    return await create_async_client(
        url, key, options=AsyncClientOptions(persist_session=False, auto_refresh_token=False)
    )

def maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row.

    Reading .data on that None is AttributeError (CORS-less HTTP 500). Callers
    that need a missing-row signal use ``row or None`` / ``if not row``.
    """
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}

