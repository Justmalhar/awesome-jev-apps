"""Offline checks for review-authenticity."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    rules = app.parse_rules(app.SAMPLE_RULES.read_text(encoding="utf-8"))
    assert [r["id"] for r in rules] == ["A1", "A2", "A3", "A4", "A5"], rules
    assert app.parse_rules("no incentivised reviews at all")[0]["id"] == "A1"
    # prose with a colon keeps its whole text; an id must carry a digit
    assert app.parse_rules("Note: disclose samples")[0]["text"] == "Note: disclose samples"
    assert app.parse_rules("") == []

    reviews = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(reviews) >= 8
    assert all({"product", "rating", "title", "text", "verified_purchase"} <= set(r) for r in reviews)

    questions = app.build_questions(reviews, rules)
    # per rule + 4 independent tells + firsthand score + subject choice
    assert len(questions) == len(reviews) * (len(rules) + 6), len(questions)

    # four tells that can co-occur are four Nouls, not one rubric
    tells = ("incentivised_0", "templated_0", "second_hand_0", "solicited_burst_0")
    assert len({id(questions[t]) for t in tells}) == 4
    assert all(questions[t]["type"] == "noul" for t in tells)
    assert questions["firsthand_0"]["type"] == "score"

    assert "unclear" in questions["subject_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(level) > 15 for level in app.FIRSTHAND_LEVELS), app.FIRSTHAND_LEVELS
    assert len(app.FIRSTHAND_LEVELS) == 4
    assert all(len(text) > 15 for text in app.SUBJECT_KINDS.values())

    gates = dict(app.DEFAULT_GATES)
    quiet = {"incentivised": 0.05, "templated": 0.1, "second_hand": 0.02, "campaign_phrasing": 0.03}
    assert app.decide(0.05, quiet, 2.5, gates) == "publish"
    assert app.decide(0.9, quiet, 2.5, gates) == "human_review", "your own rule always goes to a person"
    assert app.decide(0.05, {**quiet, "incentivised": 0.9}, 2.5, gates) == "label"
    two = {**quiet, "incentivised": 0.9, "templated": 0.85}
    assert app.decide(0.05, two, 0.4, gates) == "suppress"
    assert app.decide(0.05, two, 2.6, gates) == "label", (
        "specific, lived detail blocks suppression however many tells fire"
    )
    # boundaries
    assert app.decide(gates["rule"], quiet, 3.0, gates) == "human_review"
    assert app.decide(0.0, {**quiet, "templated": gates["tell"]}, 3.0, gates) == "label"
    assert app.decide(0.0, two, gates["firsthand"], gates) == "label"


if __name__ == "__main__":
    check()
    print("ok")
