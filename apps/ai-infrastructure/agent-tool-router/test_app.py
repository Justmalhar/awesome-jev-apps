"""Offline checks for agent-tool-router."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def check() -> None:
    import router

    catalog = router.CATALOG
    assert len(catalog) >= 30, len(catalog)
    assert all({"name", "summary", "parameters"} <= set(t) for t in catalog)

    names = [t["name"] for t in catalog]
    # Duplicates would silently collide as Choice option keys.
    assert len(names) == len(set(names)), "tool names must be unique"


if __name__ == "__main__":
    check()
    print("ok")
