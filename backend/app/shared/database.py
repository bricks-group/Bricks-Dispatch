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

db: AsyncClient = create_async_client(url, key)
