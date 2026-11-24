#!/usr/bin/env python3
"""Fail when a SQL migration creates a public table that never enables RLS.

Supabase grants anon and authenticated full access to new public tables by
default, so a table without row level security is readable and writable by
anyone holding the public anon key. Every public table created in this repo's
migrations must have a matching ``alter table ... enable row level security``
in some migration.

    python3 scripts/check_migration_rls.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"node_modules", ".git"}

IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
CREATE_RE = re.compile(
    r"\bcreate\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?"
    rf"(?P<name>(?:{IDENT}\s*\.\s*)?{IDENT})",
    re.IGNORECASE,
)
TEMP_RE = re.compile(r"\bcreate\s+(?:global\s+|local\s+)?temp(?:orary)?\s+table\b", re.IGNORECASE)
RLS_RE = re.compile(
    r"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?"
    rf"(?P<name>(?:{IDENT}\s*\.\s*)?{IDENT})\s+enable\s+row\s+level\s+security",
    re.IGNORECASE,
)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(sql: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", sql))


def _normalize(name: str) -> tuple[str, str]:
    parts = [p.strip().strip('"') for p in name.split(".")]
    if len(parts) == 1:
        return "public", parts[0].lower()
    return parts[0].lower(), parts[1].lower()


def _sql_files() -> list[Path]:
    return sorted(
        p for p in REPO.rglob("*.sql") if not SKIP_DIRS.intersection(p.parts)
    )


def main() -> int:
    created: dict[tuple[str, str], Path] = {}
    locked: set[tuple[str, str]] = set()
    for path in _sql_files():
        sql = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for match in CREATE_RE.finditer(sql):
            if TEMP_RE.match(sql, match.start()):
                continue
            key = _normalize(match.group("name"))
            if key[0] == "public":
                created.setdefault(key, path)
        for match in RLS_RE.finditer(sql):
            locked.add(_normalize(match.group("name")))

    missing = sorted(k for k in created if k not in locked)
    for schema, table in missing:
        rel = created[(schema, table)].relative_to(REPO)
        print(
            f"{rel}: {schema}.{table} is created without "
            "`alter table ... enable row level security`",
            file=sys.stderr,
        )
    if missing:
        print(
            "\nEnable RLS on every public table. For backend-only tables also "
            "`revoke all on table <t> from anon, authenticated;`.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(created)} public tables, all with row level security.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
