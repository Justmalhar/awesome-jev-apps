"""Offline checks for intent-palette."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    questions = app.build_questions("export this as a pdf")
    assert len(questions) == 2, "one request per keystroke, two questions"
    assert app.NO_MATCH in questions["action"]["criteria"], "a Choice needs a no-match option"
    assert questions["settled"]["type"] == "noul"
    assert len(questions["action"]["criteria"]) == len(app.ACTIONS) + 1

    # Debounce is Python policy, not a timer.
    assert app.should_fire("exp", None) is True
    assert app.should_fire("ex", None) is False
    assert app.should_fire("exp", "exp") is False
    assert app.should_fire("  exp  ", "exp") is False

    assert app.keystroke_prefixes("ab") == []
    assert app.keystroke_prefixes("abcd") == ["abc", "abcd"]
    long_query = "x" * 60
    sampled = app.keystroke_prefixes(long_query)
    assert len(sampled) == app.MAX_EVENTS, sampled
    assert sampled[-1] == long_query, "a replay must end on the full query"

    assert app.decide(0.2, "file.export_pdf", 0.9, 0.55, 0.25) == "keep_typing"
    assert app.decide(0.9, app.NO_MATCH, 0.9, 0.55, 0.25) == "no_match"
    assert app.decide(0.9, "file.export_pdf", 0.1, 0.55, 0.25) == "no_match"
    assert app.decide(0.9, "file.export_pdf", 0.9, 0.55, 0.25) == "show"
    # Boundary: exactly at the threshold commits.
    assert app.decide(0.55, "file.export_pdf", 0.25, 0.55, 0.25) == "show"


if __name__ == "__main__":
    check()
    print("ok")
