"""Offline checks for live-moderation."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    message = app.STREAM["messages"][0]
    questions = app.build_questions(message, app.STREAM["topic"])
    assert len(questions) == 5, "one request per message, five questions"
    assert sum(1 for q in questions.values() if q["type"] == "noul") == 4
    assert questions["severity"]["type"] == "score"
    # Score levels must be concrete situations, not labels.
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS)
    assert len(app.SEVERITY_LEVELS) == 4

    base = {
        "attacks_person": 0.0,
        "is_promotion": 0.0,
        "off_topic": 0.0,
        "needs_human": 0.0,
        "severity": 0.0,
    }
    assert app.decide(base, 0.8, 0.5) == "allow"
    assert app.decide({**base, "is_promotion": 0.9}, 0.8, 0.5) == "remove"
    assert app.decide({**base, "attacks_person": 0.85}, 0.8, 0.5) == "remove"
    assert app.decide({**base, "attacks_person": 0.6}, 0.8, 0.5) == "hold"
    assert app.decide({**base, "off_topic": 0.7}, 0.8, 0.5) == "hold"
    assert app.decide({**base, "severity": 2.4}, 0.8, 0.5) == "hold"
    # Boundaries: exactly at a threshold acts.
    assert app.decide({**base, "is_promotion": 0.8}, 0.8, 0.5) == "remove"
    assert app.decide({**base, "off_topic": 0.5}, 0.8, 0.5) == "hold"
    assert app.decide({**base, "off_topic": 0.49}, 0.8, 0.5) == "allow"
    # needs_human never removes on its own -- it escalates, which is a separate lever.
    assert app.decide({**base, "needs_human": 1.0}, 0.8, 0.5) == "allow"

    assert len(app.STREAM["messages"]) >= 10, "the stream must be long enough to show latency spread"


if __name__ == "__main__":
    check()
    print("ok")
