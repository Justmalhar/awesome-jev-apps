"""Offline checks for slack-digest. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    messages = app.load_messages(None)
    assert len(messages) >= 20
    assert len({m["channel"] for m in messages}) >= 5, "a digest needs many channels"

    questions = app.build_questions(messages, "I own checkout.")
    assert len(questions) == len(messages) * 6, len(questions)
    assert "other" in questions["kind_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.KINDS.values())
    assert all(len(level) > 15 for level in app.RELEVANCE_LEVELS)

    # mention detection is Python's job, and it matches whole handles only
    assert app.mentions("hey @maya can you look", "@maya")
    assert app.mentions("hey @Maya", "maya")
    assert not app.mentions("hey @mayank can you look", "@maya")
    assert not app.mentions("no handle here", "@maya")
    assert not app.mentions("@maya", "")
    assert any(app.mentions(m["text"], "@maya") for m in messages), "sample must exercise mentions"

    gates = {"relevance_gate": 1.5, "noul_gate": 0.6}
    quiet = {"concerns": 0.1, "open_ask": 0.1, "decision": 0.1, "broken": 0.1, "relevance": 0.2}
    assert not app.surfaces(quiet, False, **gates)
    # a direct mention beats every gate
    assert app.surfaces(quiet, True, **gates)
    # relevant but no reason attached stays filtered
    assert not app.surfaces({**quiet, "relevance": 3.0}, False, **gates)
    assert app.surfaces({**quiet, "relevance": 3.0, "broken": 0.9}, False, **gates)
    assert app.surfaces({**quiet, "relevance": 1.5, "decision": 0.6}, False, **gates)
    # an unanswered ask alone is not enough if it is not the reader's area
    assert not app.surfaces({**quiet, "relevance": 3.0, "open_ask": 0.9}, False, **gates)

    tags = app.reasons({"concerns": 0.9, "open_ask": 0.9, "decision": 0.0, "broken": 0.0},
                       True, noul_gate=0.6)
    assert any("named" in t for t in tags) and len(tags) == 3
    assert app.reasons({"concerns": 0.0, "open_ask": 0.0, "decision": 0.0, "broken": 0.0},
                       False, noul_gate=0.6) == []


if __name__ == "__main__":
    check()
    print("ok")
