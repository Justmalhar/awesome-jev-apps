"""Offline checks for community-moderator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    rules = app.parse_rules(app.SAMPLE_RULES.read_text(encoding="utf-8"))
    assert [r["id"] for r in rules] == ["R1", "R2", "R3", "R4", "R5", "R6"], rules
    assert rules[0]["text"].startswith("No unsolicited"), rules[0]
    # comments and blanks are dropped; an unprefixed line still gets an id
    loose = app.parse_rules("# note\n\nBe kind to each other\nR9: No crossposting\n")
    assert loose == [
        {"id": "R1", "text": "Be kind to each other"},
        {"id": "R9", "text": "No crossposting"},
    ], loose
    # a colon inside prose must not be mistaken for an id prefix -- an id has a digit
    assert app.parse_rules("Warning: no spam of any kind")[0] == {
        "id": "R1", "text": "Warning: no spam of any kind"}

    posts = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(posts) >= 8 and all({"author", "thread", "body"} <= set(p) for p in posts)

    questions = app.build_questions(posts, rules)
    # one Noul per rule per post, plus severity + contested + concern
    assert len(questions) == len(posts) * (len(rules) + 3), len(questions)
    assert questions["rule_0_0"]["type"] == "noul"
    assert questions["severity_0"]["type"] == "score"
    assert questions["contested_0"]["type"] == "noul"

    # a Choice must have an escape hatch
    assert "no_concern" in questions["concern_0"]["criteria"]
    # Score levels must be concrete situations, not labels
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS), app.SEVERITY_LEVELS
    assert len(app.SEVERITY_LEVELS) == 4

    # every judged property that can be true at once is its own question
    assert questions["rule_0_0"] is not questions["rule_0_1"]

    gates = dict(app.DEFAULT_GATES)
    assert app.decide(0.05, 0.1, 0.02, gates) == "allow"
    assert app.decide(0.95, 3.0, 0.02, gates) == "block"
    assert app.decide(0.95, 1.0, 0.02, gates) == "warn"
    assert app.decide(0.50, 2.9, 0.02, gates) == "human_review", "grey band never auto-actions"
    assert app.decide(0.99, 3.0, 0.90, gates) == "human_review", "contested beats block"
    # boundaries land on the acting side
    assert app.decide(gates["hit"], gates["block"], 0.0, gates) == "block"
    assert app.decide(gates["uncertain"], 0.0, 0.0, gates) == "human_review"
    assert app.decide(gates["uncertain"] - 0.01, 0.0, 0.0, gates) == "allow"
    assert app.decide(0.0, 0.0, gates["contested"], gates) == "human_review"


if __name__ == "__main__":
    check()
    print("ok")
