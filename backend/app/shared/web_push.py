"""Web Push for Bricks agent alerts (RFC 8030 delivery, RFC 8291 aes128gcm
payload encryption, RFC 8292 VAPID). Uses only `cryptography` + `httpx`, which
every Bricks API already ships, so no new dependency lands on Vercel.

Config (same values on bricks-os-api, bricks-dispatch-api, bricks-companion-api):
  VAPID_PUBLIC_KEY   base64url uncompressed P-256 point (65 bytes)
  VAPID_PRIVATE_KEY  base64url raw private scalar (32 bytes)
  VAPID_SUBJECT      mailto: contact for push services (default hello@brxs.ca)

Subscriptions live in public.push_subscriptions (created by Bricks OS when a
seat turns push on in Settings). Senders look devices up by the agent's email.
Every sender here fails quietly: a push problem never blocks a booking.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DEFAULT_SUBJECT = "mailto:hello@brxs.ca"
RECORD_SIZE = 4096
# Push services browsers actually hand out. Anything else is refused so the
# API never POSTs to an arbitrary URL a client supplied.
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)

SUBS_SQL = """
CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    endpoint text PRIMARY KEY,
    user_id uuid,
    email text,
    p256dh text NOT NULL,
    auth text NOT NULL,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_ok_at timestamptz
);
ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_email ON public.push_subscriptions (lower(email));
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON public.push_subscriptions (user_id);
"""


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_dec(s: str) -> bytes:
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def vapid_config(env=None) -> Optional[dict]:
    env = os.environ if env is None else env
    pub = (env.get("VAPID_PUBLIC_KEY") or "").strip()
    priv = (env.get("VAPID_PRIVATE_KEY") or "").strip()
    if not pub or not priv:
        return None
    subject = (env.get("VAPID_SUBJECT") or "").strip() or DEFAULT_SUBJECT
    return {"public_key": pub, "private_key": priv, "subject": subject}


def generate_vapid_keys() -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    d = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"public_key": b64u(pub), "private_key": b64u(d)}


def endpoint_allowed(endpoint: str) -> bool:
    try:
        u = urlsplit(endpoint or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        return False
    return any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


def valid_subscription(sub: dict) -> bool:
    if not isinstance(sub, dict):
        return False
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else sub
    try:
        p256dh = b64u_dec(keys.get("p256dh") or "")
        auth = b64u_dec(keys.get("auth") or "")
    except Exception:  # noqa: BLE001
        return False
    return endpoint_allowed(sub.get("endpoint") or "") and len(p256dh) == 65 and p256dh[0] == 4 and len(auth) == 16


def _private_key(priv_b64: str):
    return ec.derive_private_key(int.from_bytes(b64u_dec(priv_b64), "big"), ec.SECP256R1())


def vapid_header(endpoint: str, cfg: dict, *, now: Optional[float] = None) -> str:
    u = urlsplit(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({
        "aud": f"{u.scheme}://{u.netloc}",
        "exp": int((now if now is not None else time.time()) + 12 * 3600),
        "sub": cfg["subject"],
    }, separators=(",", ":")).encode())
    signing = f"{head}.{claims}".encode()
    r, s = decode_dss_signature(_private_key(cfg["private_key"]).sign(signing, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={head}.{claims}.{b64u(sig)}, k={cfg['public_key']}"


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, *, salt: Optional[bytes] = None, server_key=None) -> bytes:
    """RFC 8291 aes128gcm body (single record)."""
    ua_pub = b64u_dec(p256dh_b64)
    auth = b64u_dec(auth_b64)
    salt = salt or os.urandom(16)
    as_priv = server_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(shared, auth, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    if len(payload) > RECORD_SIZE - 16 - 1 - 86:
        raise ValueError("push payload too large")
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_pub)]) + as_pub + ct


def decrypt(body: bytes, ua_priv, auth_b64: str) -> bytes:
    """Receiver side (what the browser does). Used by tests to prove `encrypt`."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = _hkdf(shared, b64u_dec(auth_b64), b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert rs == RECORD_SIZE
    return plain.rstrip(b"\x00")[:-1]


def notification(title: str, body: str, url: str = "", tag: str = "") -> dict:
    out = {"title": (title or "Bricks")[:120], "body": (body or "")[:240]}
    if url:
        out["url"] = url
    if tag:
        out["tag"] = tag[:64]
    return out


async def send_one(sub: dict, message: dict, cfg: dict, *, client=None, ttl: int = 86400) -> str:
    """POST one encrypted push. Returns 'sent', 'gone' (drop the device) or 'failed'."""
    endpoint = sub.get("endpoint") or ""
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else sub
    if not endpoint_allowed(endpoint):
        return "failed"
    try:
        body = encrypt(json.dumps(message, separators=(",", ":")).encode(), keys["p256dh"], keys["auth"])
        headers = {
            "Authorization": vapid_header(endpoint, cfg),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(int(ttl)),
            "Urgency": "high",
        }
        own = client is None
        if own:
            import httpx
            client = httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(endpoint, content=body, headers=headers)
        finally:
            if own:
                await client.aclose()
        if resp.status_code in (200, 201, 202):
            return "sent"
        if resp.status_code in (404, 410):
            return "gone"
        return "failed"
    except Exception:  # noqa: BLE001
        return "failed"


# ── subscription lookups (psycopg2-style connection from a `connect` callable) ──
def _rows(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def subscriptions_for_email(connect: Callable, email: str) -> list:
    addr = (email or "").strip()
    if not addr:
        return []
    conn = None
    try:
        conn = connect()
        if conn is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT endpoint, p256dh, auth FROM public.push_subscriptions "
                "WHERE lower(email) = lower(%s) ORDER BY created_at DESC LIMIT 20",
                (addr,),
            )
            return _rows(cur)
    except Exception:  # noqa: BLE001 - no table / DB blip => no devices
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def mark_results(connect: Callable, results: dict) -> None:
    gone = [e for e, r in results.items() if r == "gone"]
    ok = [e for e, r in results.items() if r == "sent"]
    if not gone and not ok:
        return
    conn = None
    try:
        conn = connect()
        if conn is None:
            return
        with conn.cursor() as cur:
            if gone:
                cur.execute("DELETE FROM public.push_subscriptions WHERE endpoint = ANY(%s)", (gone,))
            if ok:
                cur.execute("UPDATE public.push_subscriptions SET last_ok_at = now() WHERE endpoint = ANY(%s)", (ok,))
        try:
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


async def push_to_email(connect: Callable, email: str, message: dict, *, cfg: Optional[dict] = None,
                        client=None, subs: Optional[list] = None) -> dict:
    """Send `message` to every device the agent turned push on for. Never raises."""
    cfg = cfg or vapid_config()
    if not cfg:
        return {"sent": 0, "devices": 0, "skipped": "vapid_not_configured"}
    devices = subs if subs is not None else subscriptions_for_email(connect, email)
    results = {}
    for sub in devices:
        results[sub.get("endpoint") or ""] = await send_one(sub, message, cfg, client=client)
    mark_results(connect, results)
    return {
        "sent": sum(1 for r in results.values() if r == "sent"),
        "gone": sum(1 for r in results.values() if r == "gone"),
        "devices": len(devices),
    }
