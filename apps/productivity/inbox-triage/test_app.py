"""Offline checks for inbox-triage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    sys.modules.pop("app", None)
    import app

    messages = json.loads((HERE / "sample_inbox.json").read_text(encoding="utf-8"))
    assert len(messages) >= 10
    assert all({"from", "subject", "body"} <= set(m) for m in messages)

    questions = app.build_questions(messages)
    # 5 independent judgments per message, all in one request.
    assert len(questions) == len(messages) * 5, len(questions)
    assert questions["mine_0"]["type"] == "noul"
    assert questions["urgency_0"]["type"] == "score"
    assert "automated" in app.CATEGORIES

    # Urgency levels must describe concrete situations, not be bare labels.
    levels = questions["urgency_0"]["criteria"]
    assert len(levels) == 4 and all(len(level) > 15 for level in levels), levels


if __name__ == "__main__":
    check()
    print("ok")
