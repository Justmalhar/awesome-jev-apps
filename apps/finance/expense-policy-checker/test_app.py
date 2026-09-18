"""Offline checks for expense-policy-checker. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    policy = json.loads((HERE / "sample_policy.json").read_text(encoding="utf-8"))
    judged, computed = app.split_rules(policy["rules"])
    assert judged and computed, "the sample policy must exercise both paths"
    assert len(judged) + len(computed) == len(policy["rules"]), "no rule may be silently dropped"
    for rule in judged:
        assert len(rule["breach"]) > 15 and len(rule["clean"]) > 15, rule["id"]

    # -- one Noul per (claim, rule), all in one request --
    claims = [
        {"claim_id": "X1", "vendor": "Bar", "category": "meals", "amount": 60.0,
         "description": "drinks", "receipt": "yes", "approval_ref": ""},
        {"claim_id": "X2", "vendor": "Cafe", "category": "meals", "amount": 9.0,
         "description": "coffee", "receipt": "no", "approval_ref": ""},
    ]
    questions = app.build_questions(claims, judged)
    assert len(questions) == len(claims) * len(judged)
    assert all(q["type"] == "noul" for q in questions.values()), "rules are independent, so Nouls"
    assert set(questions["b_0_0"]["criteria"]) == {"true", "false"}

    # -- every comparison lives in Python, and is right at the boundary --
    over_cap = app.deterministic_breaches(claims[0], computed)
    assert "CAP-08" in over_cap, "60.00 of meals must trip the 45.00 cap"
    assert "RCP-10" not in over_cap, "a receipt was attached"
    assert app.deterministic_breaches(claims[1], computed) == {}, "9.00 with no receipt is fine"
    exactly_at_cap = {**claims[0], "amount": 45.0}
    assert "CAP-08" not in app.deterministic_breaches(exactly_at_cap, computed), "the cap is inclusive"
    big = {"claim_id": "X3", "vendor": "Air", "category": "travel", "amount": 300.0,
           "description": "flight", "receipt": "no", "approval_ref": ""}
    hits = app.deterministic_breaches(big, computed)
    assert "CAP-09" in hits and "RCP-10" in hits
    assert "CAP-09" not in app.deterministic_breaches({**big, "approval_ref": "PA-1"}, computed)
    assert "CAP-08" not in hits, "the meal cap must not apply to travel"

    # -- VETO, not average: the whole point of the app --
    nine_clean_one_breach = {f"R{i}": 0.02 for i in range(9)} | {"BAD": 0.95}
    assert app.verdict(nine_clean_one_breach, 0.7, 0.35) == "reject"
    mean = sum(nine_clean_one_breach.values()) / len(nine_clean_one_breach)
    assert mean < 0.35, "an average would have approved it -- that is the failure being avoided"
    assert app.verdict({"A": 0.7}, 0.7, 0.35) == "reject", "reject gate is inclusive"
    assert app.verdict({"A": 0.69}, 0.7, 0.35) == "review"
    assert app.verdict({"A": 0.35}, 0.7, 0.35) == "review", "review gate is inclusive"
    assert app.verdict({"A": 0.34, "B": 0.01}, 0.7, 0.35) == "approve"
    assert app.verdict({}, 0.7, 0.35) == "approve"

    assert app.batch([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


if __name__ == "__main__":
    check()
    print("ok")
