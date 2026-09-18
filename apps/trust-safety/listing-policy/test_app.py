"""Offline checks for listing-policy."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    clauses = app.parse_policy(app.SAMPLE_POLICY.read_text(encoding="utf-8"))
    assert [c["id"] for c in clauses] == [f"P{n}" for n in range(1, 8)], clauses
    assert app.parse_policy("# comment only\n\n") == []
    assert app.parse_policy("Nothing illegal, obviously")[0]["id"] == "P1"
    # prose with a colon keeps its whole text; an id must carry a digit
    assert app.parse_policy("Note: nothing illegal")[0]["text"] == "Note: nothing illegal"

    listings = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(listings) >= 8
    assert all({"title", "category", "price", "description", "seller"} <= set(l) for l in listings)

    questions = app.build_questions(listings, clauses)
    # per clause + counterfeit + off_platform + mismatch + exposure + family
    assert len(questions) == len(listings) * (len(clauses) + 5), len(questions)

    # independent hazards are separate Nouls, never levels on one rubric
    for key in ("counterfeit_0", "off_platform_0", "mismatch_0", "clause_0_0"):
        assert questions[key]["type"] == "noul", key
    assert questions["exposure_0"]["type"] == "score"
    assert questions["family_0"]["type"] == "choice"

    # a Choice must have a no-match option
    assert "none_apply" in questions["family_0"]["criteria"]
    assert all(len(level) > 15 for level in app.EXPOSURE_LEVELS), app.EXPOSURE_LEVELS
    assert len(app.EXPOSURE_LEVELS) == 4
    assert all(len(text) > 15 for text in app.RESTRICTED_FAMILIES.values())

    gates = dict(app.DEFAULT_GATES)
    clean = {"counterfeit": 0.02, "off_platform_payment": 0.01, "condition_mismatch": 0.03}
    assert app.decide(0.05, clean, 0.2, gates) == "approve"
    assert app.decide(0.95, clean, 3.0, gates) == "remove"
    assert app.decide(0.95, clean, 1.0, gates) == "human_review", "clause hit, low exposure -> person"
    assert app.decide(0.05, {**clean, "counterfeit": 0.9}, 0.2, gates) == "human_review", (
        "a hazard with no clause behind it is evidence, not authority"
    )
    assert app.decide(0.5, clean, 0.0, gates) == "human_review", "grey band never auto-actions"
    # boundaries
    assert app.decide(gates["breach"], clean, gates["remove"], gates) == "remove"
    assert app.decide(0.0, {**clean, "counterfeit": gates["hazard"]}, 0.0, gates) == "human_review"
    assert app.decide(gates["uncertain"] - 0.01, clean, 0.0, gates) == "approve"


if __name__ == "__main__":
    check()
    print("ok")
