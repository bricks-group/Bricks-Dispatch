import os
from pathlib import Path
from dotenv import load_dotenv
from supabase.client import create_async_client, AsyncClient

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", ""))

if not url or not key:
    print("Warning: SUPABASE_URL or SUPABASE_KEY not set")

_db: AsyncClient = None

async def get_db() -> AsyncClient:
    global _db
    if _db is None:
        if not url or not key:
            raise RuntimeError("Supabase not configured: SUPABASE_URL and SUPABASE_KEY required")
        _db = await create_async_client(url, key)
    return _db

def maybe_single_row(res) -> dict:
    """postgrest maybe_single().execute() is None when there is no row.

    Reading .data on that None is AttributeError (CORS-less HTTP 500). Callers
    that need a missing-row signal use ``row or None`` / ``if not row``.
    """
    data = getattr(res, "data", None) if res is not None else None
    return data if isinstance(data, dict) else {}

