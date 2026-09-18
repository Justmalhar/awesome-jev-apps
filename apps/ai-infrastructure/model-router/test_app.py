"""Offline checks for model-router. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    turns = app.load_turns(None)
    assert len(turns) >= 10
    questions = app.build_questions(turns)
    assert len(questions) == len(turns) * (2 + len(app.FLAGS))

    # Difficulty and stakes are SEPARATE scores, not one rubric.
    assert questions["difficulty_0"]["type"] == "score"
    assert questions["consequence_0"]["type"] == "score"
    assert questions["difficulty_0"]["criteria"] != questions["consequence_0"]["criteria"]
    for levels in (app.DIFFICULTY_LEVELS, app.CONSEQUENCE_LEVELS):
        assert len(levels) >= 3 and all(len(level) > 15 for level in levels)
        assert len(set(levels)) == len(levels)
    for name in app.FLAGS:
        assert questions[f"{name}_0"]["type"] == "noul"
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}

    calm = {"multi_step": 0.0, "underspecified": 0.0, "needs_fresh_facts": 0.0}
    gates = {"difficulty_gate": 2.0, "consequence_gate": 2.0, "abstain_p": 0.65}
    assert app.route(0.2, 0.1, calm, **gates) == "cheap"
    assert app.route(2.0, 0.1, calm, **gates) == "frontier"          # boundary
    # easy but dangerous still goes to the frontier model -- the whole point
    assert app.route(0.3, 2.4, calm, **gates) == "frontier"
    assert app.route(0.1, 0.1, {**calm, "underspecified": 0.65}, **gates) == "abstain"
    # abstain wins over everything, including a hard turn
    assert app.route(3.0, 3.0, {**calm, "underspecified": 0.9}, **gates) == "abstain"
    assert app.route(0.1, 0.1, {**calm, "needs_fresh_facts": 0.8}, **gates) == "frontier"

    # Cost projection is monotone and lives entirely in Python.
    assert app.turn_cost(100, "frontier") > app.turn_cost(100, "cheap")
    assert app.turn_cost(4000, "cheap") > app.turn_cost(100, "cheap")
    assert set(app.MODELS) == {"cheap", "frontier"}

    assert len(app.batch(turns, 10**6)) == 1
    assert sum(len(g) for g in app.batch(turns, 50)) == len(turns)


if __name__ == "__main__":
    check()
    print("ok")
