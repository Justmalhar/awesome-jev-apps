"""Offline checks for test-gap-finder. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import test_gap as app

    behaviours = app.extract_behaviours((HERE / "sample_module.py").read_text(encoding="utf-8"))
    tests = app.extract_tests((HERE / "sample_tests.py").read_text(encoding="utf-8"))

    # -- behaviours and tests come from the parser, exactly -----------------
    names = [b.name for b in behaviours]
    assert "Order.refund" in names and "authorise" in names and "Order.tax" in names, names
    assert not [n for n in names if n.startswith("_") or ".__" in n], names
    refund = next(b for b in behaviours if b.name == "Order.refund")
    assert refund.signature == "refund(amount, reason)", refund.signature
    assert refund.raises == ["ValueError"], refund.raises
    assert refund.doc.startswith("Refund")
    assert all(len(b.source) <= app.SOURCE_CHARS for b in behaviours)
    assert len(tests) == 7, [t.name for t in tests]
    assert all(t.name.startswith("test") and t.source for t in tests)

    # -- blocking is Python, generous, and bounded --------------------------
    pairs = app.candidate_pairs(behaviours, tests, per_behaviour=3)
    assert len(pairs) == 3 * len(behaviours), len(pairs)
    assert all(p.test in tests for p in pairs)
    per_behaviour = {}
    for pair in pairs:
        per_behaviour.setdefault(pair.behaviour.name, []).append(pair.similarity)
    # every behaviour gets a shortlist, and it is ranked
    assert len(per_behaviour) == len(behaviours)
    assert all(ratios == sorted(ratios, reverse=True) for ratios in per_behaviour.values())
    # the obvious name match must reach the shortlist, or Jev never sees it
    subtotal = [p.test.name for p in pairs if p.behaviour.name == "Order.subtotal"]
    assert "test_subtotal_sums_line_totals" in subtotal, subtotal
    assert app.candidate_pairs(behaviours, [], 3) == []

    # -- one noul per pair plus one risk score per behaviour, one request ---
    questions = app.build_questions(pairs, behaviours)
    assert len(questions) == len(pairs) + len(behaviours), len(questions)
    assert questions["covers_0"]["type"] == "noul"
    assert set(questions["covers_0"]["criteria"]) == {"true", "false"}
    assert questions["risk_0"]["type"] == "score"
    assert len(app.RISK_LEVELS) == 4
    assert all(len(level) > 15 for level in app.RISK_LEVELS)
    assert questions["risk_0"]["criteria"] == app.RISK_LEVELS

    # -- the gap decision is pure Python ------------------------------------
    two = [app.Behaviour(name="a", doc="", signature="a()"),
           app.Behaviour(name="b", doc="", signature="b()")]
    shortlist = [
        app.Pair(behaviour=two[0], test=app.Test(name="test_weak", doc=""), similarity=0.9),
        app.Pair(behaviour=two[0], test=app.Test(name="test_real", doc=""), similarity=0.1),
        app.Pair(behaviour=two[1], test=app.Test(name="test_other", doc=""), similarity=0.5),
    ]
    rows = app.find_gaps(two, shortlist, {0: 0.2, 1: 0.95, 2: 0.1},
                         {0: 0.5, 1: 2.4}, cover_gate=0.6, risk_gate=2.0)
    by_name = {row["behaviour"]: row for row in rows}
    # the BEST covering test wins, not the most name-similar one
    assert by_name["a"]["verdict"] == "covered"
    assert by_name["a"]["best_test"] == "test_real", by_name["a"]
    assert by_name["b"]["verdict"] == "untested_and_risky"
    assert by_name["b"]["best_test"] == ""
    # gaps sort to the top
    assert rows[0]["behaviour"] == "b"
    # a low-risk gap is still a gap, just not a build failure
    low = app.find_gaps(two, shortlist, {0: 0.2, 1: 0.95, 2: 0.1},
                        {0: 0.5, 1: 1.0}, cover_gate=0.6, risk_gate=2.0)
    assert {r["behaviour"]: r["verdict"] for r in low}["b"] == "untested"
    # raising the bar re-decides stored floats with no inference
    strict = app.find_gaps(two, shortlist, {0: 0.2, 1: 0.95, 2: 0.1},
                           {0: 0.5, 1: 2.4}, cover_gate=0.99, risk_gate=2.0)
    assert all(row["verdict"] != "covered" for row in strict)


if __name__ == "__main__":
    check()
    print("ok")
