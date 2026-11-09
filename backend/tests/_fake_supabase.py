"""Stateful in-memory stand-in for the Supabase client (tables, filters,
unique keys), so the delivery chain can run end to end with no network.
Unique keys mirror prod: every table on id; media_delivery also on job_id.
payouts.job_id is NOT unique (as in prod before 20261109_delivery_robust.sql)."""
import asyncio
import copy
import fnmatch
import uuid

UNIQUE = {"media_delivery": ("job_id",)}


class DuplicateKey(Exception):
    code = "23505"

    def __str__(self):
        return "duplicate key value violates unique constraint"


class CheckViolation(Exception):
    """Mirrors live `payouts_amount_positive CHECK (amount > 0)` (pg 23514)."""
    code = "23514"

    def __str__(self):
        return 'new row for relation "payouts" violates check constraint "payouts_amount_positive"'


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload = db, table, "select", None
        self.filters, self._limit, self._single, self._order, self._conflict = [], None, False, None, None

    # builders
    def select(self, *_a, **_k):
        self.op = "select"; return self

    def insert(self, row):
        self.op, self.payload = "insert", row; return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload, self._conflict = "upsert", row, on_conflict; return self

    def update(self, fields):
        self.op, self.payload = "update", fields; return self

    def delete(self):
        self.op = "delete"; return self

    def eq(self, c, v): self.filters.append(lambda r: str(r.get(c)) == str(v)); return self
    def neq(self, c, v): self.filters.append(lambda r: str(r.get(c)) != str(v)); return self
    def in_(self, c, vs): s = {str(v) for v in vs}; self.filters.append(lambda r: str(r.get(c)) in s); return self
    def like(self, c, p): self.filters.append(lambda r: fnmatch.fnmatchcase(str(r.get(c) or ""), p.replace("%", "*"))); return self
    def gte(self, c, v): self.filters.append(lambda r: r.get(c) is not None and str(r.get(c)) >= str(v)); return self
    def lte(self, c, v): self.filters.append(lambda r: r.get(c) is not None and str(r.get(c)) <= str(v)); return self
    def lt(self, c, v): self.filters.append(lambda r: r.get(c) is not None and str(r.get(c)) < str(v)); return self
    def is_(self, c, v): self.filters.append(lambda r: r.get(c) is None if str(v) == "null" else True); return self
    def not_(self, *a, **k): return self
    def limit(self, n): self._limit = n; return self
    def order(self, c, desc=False): self._order = (c, desc); return self
    def maybe_single(self): self._single = True; return self
    def single(self): self._single = True; return self

    def _match(self):
        return [r for r in self.db.tables.setdefault(self.table, []) if all(f(r) for f in self.filters)]

    def _check_payouts_amount(self, row):
        if self.table != "payouts" or not self.db.enforce_payouts_amount:
            return
        if "amount" not in row:
            return
        if not (row.get("amount") or 0) > 0:
            raise CheckViolation()

    def _check_unique(self, row, ignore=None):
        rows = self.db.tables.setdefault(self.table, [])
        for col in ("id",) + UNIQUE.get(self.table, ()):
            if row.get(col) is None:
                continue
            if any(r is not ignore and str(r.get(col)) == str(row[col]) for r in rows):
                raise DuplicateKey()

    async def execute(self):
        await asyncio.sleep(0)  # let concurrent callers interleave like real I/O
        if self.db.down.get(self.table):
            raise RuntimeError(f"{self.table} unavailable")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = self._match()
            if self._order:
                c, d = self._order
                out = sorted(out, key=lambda r: str(r.get(c) or ""), reverse=d)
            if self._limit:
                out = out[: self._limit]
            out = copy.deepcopy(out)
            if self._single:
                return _Res(out[0] if out else None) if out else None
            return _Res(out)
        if self.op == "insert":
            new = [dict(r) for r in (self.payload if isinstance(self.payload, list) else [self.payload])]
            for r in new:
                r.setdefault("id", str(uuid.uuid4()))
                self._check_payouts_amount(r)
                self._check_unique(r)
                rows.append(r)
            return _Res(copy.deepcopy(new))
        if self.op == "upsert":
            r = dict(self.payload)
            key = self._conflict or "id"
            hit = next((x for x in rows if str(x.get(key)) == str(r.get(key))), None)
            if hit:
                merged = {**hit, **r}
                self._check_payouts_amount(merged)
                hit.update(r)
                return _Res([copy.deepcopy(hit)])
            self._check_payouts_amount(r)
            r.setdefault("id", str(uuid.uuid4()))
            rows.append(r)
            return _Res([copy.deepcopy(r)])
        if self.op == "update":
            hit = self._match()
            for r in hit:
                merged = {**r, **copy.deepcopy(self.payload)}
                self._check_payouts_amount(merged)
                r.update(copy.deepcopy(self.payload))
            return _Res(copy.deepcopy(hit))
        if self.op == "delete":
            hit = self._match()
            self.db.tables[self.table] = [r for r in rows if r not in hit]
            return _Res(copy.deepcopy(hit))
        raise AssertionError(self.op)


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.down: dict[str, bool] = {}
        # Live CHECK payouts_amount_positive: amount > 0. Set False only to
        # simulate a DB that has not yet applied the constraint.
        self.enforce_payouts_amount = True

    def table(self, name):
        return _Q(self, name)

    def rows(self, name, **eq):
        return [r for r in self.tables.get(name, []) if all(str(r.get(k)) == str(v) for k, v in eq.items())]

    def one(self, name, **eq):
        r = self.rows(name, **eq)
        return r[0] if r else None
