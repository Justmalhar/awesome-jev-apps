"""Offline checks for ad-brand-safety."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    rules = app.parse_rules(app.SAMPLE_RULES.read_text(encoding="utf-8"))
    assert [r["id"] for r in rules] == [f"B{n}" for n in range(1, 7)], rules
    assert app.parse_rules("Note: keep it clean")[0]["text"] == "Note: keep it clean"

    creative = json.loads(app.SAMPLE_CREATIVE.read_text(encoding="utf-8"))
    assert {"brand", "headline", "body"} <= set(creative)
    placements = json.loads(app.SAMPLE_PLACEMENTS.read_text(encoding="utf-8"))
    assert len(placements) >= 8
    assert all({"site", "page_title", "page_summary"} <= set(p) for p in placements)

    questions = app.build_questions(placements, rules)
    # per rule + 3 hazards + suitability + context
    assert len(questions) == len(placements) * (len(rules) + 5), len(questions)

    # every question must name BOTH states -- that is the whole point
    both = questions["rule_0_0"]["instructions"]["pair"]
    assert any(v.startswith("`creative.") for v in both.values())
    assert any(v.startswith("`placements[0]") for v in both.values())

    hazards = ("implied_comment_0", "tone_clash_0", "audience_mismatch_0")
    assert all(questions[h]["type"] == "noul" for h in hazards)
    assert len({id(questions[h]) for h in hazards}) == 3, "independent hazards, separate Nouls"
    assert questions["suitability_0"]["type"] == "score"

    assert "unclear" in questions["context_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(level) > 15 for level in app.SUITABILITY_LEVELS), app.SUITABILITY_LEVELS
    assert len(app.SUITABILITY_LEVELS) == 4
    assert all(len(text) > 15 for text in app.CONTEXT_KINDS.values())

    gates = dict(app.DEFAULT_GATES)
    calm = {"implied_comment": 0.05, "tone_clash": 0.03, "audience_mismatch": 0.08}
    assert app.decide(0.02, calm, 0.3, gates) == "run"
    assert app.decide(0.95, calm, 2.9, gates) == "block"
    assert app.decide(0.95, calm, 0.5, gates) == "human_review", "rule hit, no damage -> person"
    assert app.decide(0.02, {**calm, "tone_clash": 0.9}, 0.3, gates) == "human_review"
    assert app.decide(0.02, calm, 2.9, gates) == "human_review", (
        "damage with no rule behind it is a gap in the rule set, not an auto-block"
    )
    assert app.decide(0.02, calm, 1.8, gates) == "reduce"
    assert app.decide(0.5, calm, 0.2, gates) == "human_review", "grey band never auto-runs"
    # boundaries
    assert app.decide(gates["rule"], calm, gates["block"], gates) == "block"
    assert app.decide(0.0, calm, gates["caution"], gates) == "reduce"
    assert app.decide(0.0, {**calm, "implied_comment": gates["hazard"]}, 0.0, gates) == "human_review"
    assert app.decide(gates["uncertain"] - 0.01, calm, 0.0, gates) == "run"


if __name__ == "__main__":
    check()
    print("ok")
