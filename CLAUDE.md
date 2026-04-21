# Bricks-Dispatch (Python)

You are working on **Bricks-Dispatch**, the job orchestration layer of the BRICKS ecosystem.

## Ecosystem context
- **Bricks-Brokerage** (JS) — listings source, upstream of Booking.
- **Bricks-Booking** (TS) — sends confirmed shoot jobs to this service.
- **Bricks-Dispatch** (this repo, Python) — assigns jobs to crews, runs automations, tracks status, reports back to Booking.

## Rules
- Treat incoming job payloads from Booking as a versioned contract.
- Keep worker code idempotent and retry-safe.
- Prefer typed dataclasses / Pydantic models for job schemas.
