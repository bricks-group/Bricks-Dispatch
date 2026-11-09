"""In-memory stand-in for the audit_events store used by delivery_ledger."""
from app.services import delivery_ledger as dl


class FakeLedger:
    def __init__(self, monkeypatch, *, down: bool = False):
        self.rows: dict[str, dict] = {}
        self.down = down

        async def _insert(row):
            if self.down:
                raise dl.LedgerUnavailable("store down")
            if row["id"] in self.rows:
                return False
            self.rows[row["id"]] = dict(row)
            return True

        async def _rows_like(prefix):
            if self.down:
                raise dl.LedgerUnavailable("store down")
            return [r for k, r in self.rows.items() if k.startswith(prefix)]

        async def _delete(key):
            self.rows.pop(key, None)

        async def _record_try(job_id, n, status):
            key = f"delivery-email-try:{job_id}:{n}"
            if key in self.rows:
                self.rows[key]["details"] = {"try": n, "status": status}

        async def trail(job_id):
            return sorted([r for r in self.rows.values() if r.get("entity_id") == str(job_id)],
                          key=lambda r: r["created_at"])

        monkeypatch.setattr(dl, "_insert", _insert)
        monkeypatch.setattr(dl, "_rows_like", _rows_like)
        monkeypatch.setattr(dl, "_delete", _delete)
        monkeypatch.setattr(dl, "_record_try", _record_try)
        monkeypatch.setattr(dl, "trail", trail)

    def actions(self, job_id=None):
        return [r["action"] for r in self.rows.values() if job_id is None or r["entity_id"] == str(job_id)]
