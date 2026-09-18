"""Offline checks for scam-detector."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

HAZARDS = (
    "advance_fee", "impersonation", "credential_request",
    "urgency", "off_platform", "irreversible_payment",
)


def check() -> None:
    import app

    patterns = app.parse_patterns(app.SAMPLE_PATTERNS.read_text(encoding="utf-8"))
    assert [p["id"] for p in patterns] == [f"S{n}" for n in range(1, 7)], patterns
    assert app.parse_patterns("Warning: anything urgent")[0]["id"] == "S1"
    assert app.parse_patterns("#only a comment\n") == []

    messages = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(messages) >= 8
    assert all({"sender", "channel", "subject", "body"} <= set(m) for m in messages)

    questions = app.build_questions(messages, patterns)
    # per pattern + 6 hazards + ask score + pretext choice
    assert len(questions) == len(messages) * (len(patterns) + 8), len(questions)

    # six independent hazards are six Nouls -- never one rubric, never one Choice
    keys = [f"{name}_0" for name in HAZARDS]
    assert len({id(questions[k]) for k in keys}) == 6
    assert all(questions[k]["type"] == "noul" for k in keys)
    assert questions["ask_0"]["type"] == "score"
    assert questions["pattern_0_0"]["type"] == "noul"

    assert "no_pretext" in questions["pretext_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(level) > 15 for level in app.ASK_LEVELS), app.ASK_LEVELS
    assert len(app.ASK_LEVELS) == 4
    assert all(len(text) > 15 for text in app.PRETEXT_KINDS.values())

    gates = dict(app.DEFAULT_GATES)
    quiet = {name: 0.05 for name in HAZARDS}
    loud = {**quiet, "advance_fee": 0.9, "urgency": 0.85, "irreversible_payment": 0.92}

    assert app.decide(0.02, quiet, 0.2, gates) == "deliver"
    assert app.decide(0.02, loud, 2.8, gates) == "quarantine"
    assert app.decide(0.02, loud, 0.5, gates) == "human_review", (
        "hazards with nothing actually being asked for is a person's call"
    )
    assert app.decide(0.9, quiet, 0.2, gates) == "human_review", "your own pattern goes to a person"
    assert app.decide(0.02, {**quiet, "urgency": 0.9}, 1.0, gates) == "warn"
    # boundaries
    assert app.decide(0.0, loud, gates["ask"], gates) == "quarantine"
    assert app.decide(0.0, {**quiet, "urgency": gates["hazard"]}, 0.0, gates) == "warn"
    assert app.decide(gates["pattern"], quiet, 0.0, gates) == "human_review"
    # a stricter operator can demand more agreement before quarantining
    strict = {**gates, "quarantine": 5}
    assert app.decide(0.02, loud, 2.8, strict) == "warn"


if __name__ == "__main__":
    check()
    print("ok")
