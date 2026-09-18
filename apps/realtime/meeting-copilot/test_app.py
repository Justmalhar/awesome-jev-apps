"""Offline checks for meeting-copilot."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import copilot as app

    utterances = app.TRANSCRIPT["utterances"]
    questions = app.build_questions(utterances[2], utterances[3])
    assert len(questions) == 4, "one request per utterance, four questions"
    assert all(q["type"] == "noul" for q in questions.values()), "independent properties are Nouls"
    assert set(questions) == set(app.FLAGS)
    assert all({"true", "false"} <= set(q["criteria"]) for q in questions.values())

    # The first utterance has no predecessor; the question set must not change shape.
    assert len(app.build_questions(None, utterances[0])) == 4

    quiet = {"commitment": 0.1, "dodges_question": 0.1, "decision": 0.1, "raises_blocker": 0.1}
    assert app.flags_for(quiet, 0.7) == []
    assert app.flags_for({**quiet, "commitment": 0.95}, 0.7) == ["commitment"]
    both = app.flags_for({**quiet, "commitment": 0.8, "decision": 0.9}, 0.7)
    assert set(both) == {"commitment", "decision"}, "two flags can be true at once"
    # Boundary: exactly at the threshold raises.
    assert app.flags_for({**quiet, "decision": 0.7}, 0.7) == ["decision"]
    assert app.flags_for({**quiet, "decision": 0.69}, 0.7) == []
    # Lowering the threshold raises more, with no new inference.
    assert len(app.flags_for({**quiet, "decision": 0.3}, 0.05)) == 4

    # Pacing is Python arithmetic over word counts, never asked of the model.
    assert app.speaking_seconds("one two three four five", 150.0) == 2.0
    assert app.speaking_seconds("hi") == 0.4, "a floor stops one-word lines replaying instantly"
    assert app.speaking_seconds("a " * 300) > app.speaking_seconds("a " * 30)

    assert len(utterances) >= 12, "the transcript must be long enough to show both flags"


if __name__ == "__main__":
    check()
    print("ok")
