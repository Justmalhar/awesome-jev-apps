"""Offline checks for ci-triage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    failures = json.loads((HERE / "sample_failures.json").read_text(encoding="utf-8"))
    assert len(failures) >= 10
    assert all({"test", "message", "log"} <= set(f) for f in failures)

    questions = app.build_questions(failures[:5])
    assert len(questions) == 15, "class + retry-safety + blast radius per failure"
    assert "unclear" in app.CLASSES, "a Choice needs a no-match option"
    # retry-safety cuts ACROSS the taxonomy, so it must be its own Noul.
    assert questions["retry_0"]["type"] == "noul"


if __name__ == "__main__":
    check()
    print("ok")
