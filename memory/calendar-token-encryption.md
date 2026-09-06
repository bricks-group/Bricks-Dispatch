# Calendar token encryption at rest

Dispatch stores vendor calendar OAuth / CalDAV secrets in `vendor_calendar_connections`.

## Vercel env (dispatch-api / bricks-go-api)

| Variable | Required | Notes |
|---|---|---|
| `CALENDAR_TOKEN_ENCRYPTION_KEY` | Recommended for production | Any long secret, or a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Non-Fernet secrets are SHA-256–derived into a Fernet key. |

## Behaviour

- **Write**: `access_token`, `refresh_token`, `caldav_password` are encrypted (`enc:v1:…`) when the key is set.
- **FreeBusy / auto-assign**: secrets are decrypted only in the FreeBusy path. If the key is missing and ciphertext is present, decrypt returns `None` → calendar treated as **unknown** (assign continues).
- **Backfill**: plaintext rows are re-encrypted on next write, or best-effort on FreeBusy load.
- **UI Connected**: still honest — Connected means a token/URL is present (ciphertext counts). Never fake Connected.

Do not commit the encryption key. Rotate by setting a new key only after re-encrypting or re-connecting calendars (old ciphertext will fail soft until reconnected).
