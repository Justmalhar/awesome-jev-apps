"""Offline checks for appeal-router."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    rules = app.parse_rules(app.SAMPLE_RULES.read_text(encoding="utf-8"))
    assert [r["id"] for r in rules] == [f"R{n}" for n in range(1, 8)], rules

    appeals = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(appeals) >= 8
    fields = {"content", "action", "cited_rule", "reason_given", "appellant_statement"}
    assert all(fields <= set(a) for a in appeals)

    # the rule lookup is exact, so Python does it -- never the model
    unknown = app.resolve_citations(appeals, rules)
    assert unknown == [], unknown
    assert appeals[0]["cited_rule_text"] == rules[0]["text"]
    stray = [{"cited_rule": "R99"}, {"cited_rule": ""}]
    assert app.resolve_citations(stray, rules) == ["R99", "(none cited)"]
    assert stray[0]["cited_rule_text"] == "", "an unknown citation resolves to empty, not to a guess"

    questions = app.build_questions(appeals)
    # view + 3 signals + cost
    assert len(questions) == len(appeals) * 5, len(questions)
    assert questions["view_0"]["type"] == "choice"
    signals = ("rule_fits_0", "new_information_0", "process_error_0")
    assert all(questions[s]["type"] == "noul" for s in signals)
    assert len({id(questions[s]) for s in signals}) == 3, "independent signals, separate Nouls"
    assert questions["cost_0"]["type"] == "score"

    # abstention is a real option on the Choice, not an afterthought in code
    assert "cannot_tell" in questions["view_0"]["criteria"]
    assert all(len(text) > 15 for text in app.VIEWS.values())
    assert all(len(level) > 15 for level in app.COST_LEVELS), app.COST_LEVELS
    assert len(app.COST_LEVELS) == 4

    gates = dict(app.DEFAULT_GATES)
    clean = {"rule_fits": 0.95, "new_information": 0.05, "process_error": 0.03}
    assert app.decide("uphold", 0.9, clean, 0.5, gates) == "uphold_recommended"
    assert app.decide("overturn", 0.9, clean, 0.5, gates) == "overturn_recommended"
    assert app.decide("overturn", 0.9, clean, 2.8, gates) == "priority_review", "costly overturns jump the queue"
    assert app.decide("cannot_tell", 0.99, clean, 0.5, gates) == "abstain", (
        "an explicit refusal is honoured even at high confidence"
    )
    assert app.decide("uphold", 0.2, clean, 0.5, gates) == "abstain"
    assert app.decide("uphold", 0.9, {**clean, "process_error": 0.9}, 0.0, gates) == "priority_review"
    assert app.decide("uphold", 0.9, {**clean, "new_information": 0.9}, 0.0, gates) == "priority_review"
    assert app.decide("uphold", 0.9, {**clean, "rule_fits": 0.1}, 0.0, gates) == "priority_review", (
        "an uphold on a rule that does not fit is not an uphold"
    )
    # boundaries
    assert app.decide("uphold", gates["confidence"], clean, 0.0, gates) == "uphold_recommended"
    assert app.decide("overturn", 0.9, clean, gates["cost"], gates) == "priority_review"
    assert app.decide("uphold", 0.9, {**clean, "rule_fits": gates["rule_fits"]}, 0.0, gates) == "uphold_recommended"
    # no route ever ends without a person -- every label is a queue, not an outcome
    assert set(
        app.decide(v, c, clean, k, gates)
        for v in ("overturn", "uphold", "cannot_tell")
        for c in (0.2, 0.9)
        for k in (0.0, 2.8)
    ) <= {"abstain", "priority_review", "overturn_recommended", "uphold_recommended"}


if __name__ == "__main__":
    check()
    print("ok")
