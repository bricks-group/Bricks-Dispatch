"""Regression: Vercel drops create_task after response — assignment notify must await."""
from __future__ import annotations

from pathlib import Path

from app.routers import vendor_os as vos


def test_manual_assign_awaits_notify_assignment_not_create_task():
    src = Path(vos.__file__).read_text()
    assert "await notify_assignment(" in src
    # Manual assign path must not fire-and-forget notify_assignment
    assert "create_task(\n                    notify_assignment" not in src
    assert "asyncio.create_task(\n                    notify_assignment" not in src
    # No create_task wrapping notify_assignment at all
    assert "create_task(\n                    notify_assignment" not in src.replace(" ", "")
    for line in src.splitlines():
        if "notify_assignment" in line and "create_task" in line:
            raise AssertionError(f"hollow notify_assignment: {line.strip()}")
