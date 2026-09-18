"""Offline checks for tool-output-validator. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    calls = app.load_calls(None)
    assert len(calls) >= 8
    assert {"id", "intent", "call", "output"} <= set(calls[0])
    assert len({c["id"] for c in calls}) == len(calls)

    questions = app.build_questions(calls)
    assert len(questions) == len(calls) * (len(app.PROPERTIES) + 1)
    for name in app.PROPERTIES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["risk_0"]["type"] == "score"
    assert all(len(level) > 15 for level in app.RISK_LEVELS)
    assert len(set(app.RISK_LEVELS)) == len(app.RISK_LEVELS)
    # Every question must point at the call it judges, not at a bare index.
    assert questions["risk_0"]["instructions"]["tool_call"]["what_came_back"] == "`calls[0].output`"

    gates = {"gate": 0.6, "risk_gate": 2.0}
    base = {"answers_intent": 0.0, "error_in_disguise": 0.0, "wrong_subject": 0.0,
            "missing_requested_field": 0.0, "empty_but_valid": 0.0}

    assert app.decide({**base, "answers_intent": 0.9}, 0.2, **gates) == "accept"
    # a success-shaped failure is a retry, not an escalation
    assert app.decide({**base, "error_in_disguise": 0.9}, 2.9, **gates) == "retry"
    # answering a different question is the dangerous one
    assert app.decide({**base, "answers_intent": 0.9, "wrong_subject": 0.8}, 0.1, **gates) \
        == "escalate"
    # an honest empty result is an answer...
    assert app.decide({**base, "empty_but_valid": 0.9}, 0.5, **gates) == "empty"
    # ...unless acting on the absence is itself risky
    assert app.decide({**base, "empty_but_valid": 0.9}, 2.0, **gates) == "escalate"
    # nothing fired and nothing answered: retry when cheap, escalate when risky
    assert app.decide(base, 0.1, **gates) == "retry"
    assert app.decide(base, 2.5, **gates) == "escalate"
    # boundary: exactly at the gate counts
    assert app.decide({**base, "answers_intent": 0.6}, 0.1, **gates) == "accept"

    assert len(app.batch(calls, 10**6)) == 1
    assert sum(len(g) for g in app.batch(calls, 40)) == len(calls)


if __name__ == "__main__":
    check()
    print("ok")
