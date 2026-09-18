"""Offline checks for calendar-triage. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    events = app.load_events(None)
    assert len(events) >= 10, "sample week should be a real week"
    assert all("minutes" in e and "title" in e for e in events)

    questions = app.build_questions(events, "I own billing.")
    assert len(questions) == len(events) * 6, len(questions)
    assert questions["kind_0"]["type"] == "choice"
    assert "unclear" in questions["kind_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(level) > 15 for level in app.VALUE_LEVELS)
    assert all(len(text) > 15 for text in app.KINDS.values())
    assert questions["value_0"]["type"] == "score"
    assert questions["needs_me_0"]["type"] == "noul"

    gates = {"need_gate": 0.55, "delegate_gate": 0.65, "value_gate": 1.6}
    assert app.decide({"needs_me": 0.9, "delegable": 0.1, "value": 2.5}, **gates) == "attend"
    assert app.decide({"needs_me": 0.2, "delegable": 0.8, "value": 0.5}, **gates) == "delegate"
    assert app.decide({"needs_me": 0.9, "delegable": 0.1, "value": 0.4}, **gates) == "shorten"
    assert app.decide({"needs_me": 0.1, "delegable": 0.2, "value": 2.9}, **gates) == "shorten"
    assert app.decide({"needs_me": 0.1, "delegable": 0.1, "value": 0.2}, **gates) == "skip"
    # exactly on the gate counts as met
    assert app.decide({"needs_me": 0.55, "delegable": 0.0, "value": 1.6}, **gates) == "attend"
    # a delegable meeting you are still needed at is kept, not handed over
    assert app.decide({"needs_me": 0.9, "delegable": 0.9, "value": 2.0}, **gates) == "attend"

    assert app.reclaimed_minutes("skip", 60) == 60
    assert app.reclaimed_minutes("shorten", 45) == 22
    assert app.reclaimed_minutes("attend", 60) == 0

    # the model is never handed a duration to reason about
    dumped = json.dumps(questions)
    for banned in ("minutes", "how long", "hours"):
        assert banned not in dumped, banned


if __name__ == "__main__":
    check()
    print("ok")
