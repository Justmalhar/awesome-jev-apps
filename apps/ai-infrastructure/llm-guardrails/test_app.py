"""Offline checks for llm-guardrails. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    events = app.load_traffic(None)
    assert len(events) >= 8, "sample must be big enough to show a mix of verdicts"
    assert {"id", "stage", "text"} <= set(events[0])

    questions = app.build_questions(events)
    assert len(questions) == len(events) * (len(app.HAZARDS) + 1)

    # Independent hazards must be separate Nouls, never levels on one rubric.
    for name in app.HAZARDS:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["severity_0"]["type"] == "score"
    assert questions["severity_0"]["criteria"] == app.SEVERITY_LEVELS

    # Score levels must be concrete situations, not labels.
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS)
    assert len(set(app.SEVERITY_LEVELS)) == len(app.SEVERITY_LEVELS)

    gates = {"block_p": 0.75, "review_p": 0.40, "block_severity": 2.0}
    assert app.decide({"jailbreak": 0.9, "pii_leak": 0.1}, 3.0, **gates) == "block"
    assert app.decide({"jailbreak": 0.9, "pii_leak": 0.1}, 1.0, **gates) == "review"
    assert app.decide({"jailbreak": 0.5}, 0.2, **gates) == "review"
    assert app.decide({"jailbreak": 0.39}, 1.99, **gates) == "pass"
    # boundary: exactly at both thresholds blocks
    assert app.decide({"pii_leak": 0.75}, 2.0, **gates) == "block"
    # severity alone is enough for a human look even with no hazard flagged
    assert app.decide({"pii_leak": 0.0}, 2.0, **gates) == "review"

    # Batching respects a context budget rather than hardcoding one.
    assert len(app.batch(events, 10**6)) == 1
    tiny = app.batch(events, 1)
    assert len(tiny) == len(events) and all(len(g) == 1 for g in tiny)
    assert sum(len(g) for g in app.batch(events, 200)) == len(events)


if __name__ == "__main__":
    check()
    print("ok")
