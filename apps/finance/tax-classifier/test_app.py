"""Offline checks for tax-classifier. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    frame = pd.read_csv(HERE / "sample_expenses.csv")
    assert {"vendor", "amount", "description"} <= set(frame.columns)
    assert len(frame) >= 20

    # -- question design --
    records = frame.head(3).to_dict("records")
    questions = app.build_questions(records)
    assert len(questions) == 4 * len(records)
    assert app.UNCLEAR in questions["cat_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.CATEGORIES.values())
    assert all(len(level) > 15 for level in app.EVIDENCE_LEVELS), "levels are situations, not labels"
    assert questions["cat_0"]["type"] == "choice"
    assert questions["mixed_0"]["type"] == "noul" and questions["capital_0"]["type"] == "noul"
    assert questions["evidence_0"]["type"] == "score"

    # -- routing: confidence decides what reaches a human --
    gates = dict(confidence_gate=0.7, mixed_gate=0.4, capital_gate=0.55)
    assert app.route("office_and_software", 0.95, 0.02, 0.02, **gates) == "claim_in_full"
    assert app.route("office_and_software", 0.55, 0.02, 0.02, **gates) == "ask_a_human"
    assert app.route("office_and_software", 0.70, 0.02, 0.02, **gates) == "claim_in_full", "gate is inclusive"
    assert app.route(app.UNCLEAR, 0.99, 0.0, 0.0, **gates) == "ask_a_human", (
        "the no-match option must route to a person however confident it was"
    )
    assert app.route("premises", 0.95, 0.80, 0.02, **gates) == "apportion_with_a_human"
    assert app.route("equipment", 0.95, 0.02, 0.90, **gates) == "capital_allowances"
    assert app.route("equipment", 0.95, 0.50, 0.90, **gates) == "apportion_with_a_human", (
        "a part-private asset needs a person before it needs an allowance"
    )
    for disallowed in app.DISALLOWED:
        assert app.route(disallowed, 0.99, 0.99, 0.99, **gates) == "excluded"
    assert "client_entertaining" in app.DISALLOWED, "entertaining is excluded, not merely low-confidence"

    # -- the arithmetic stays in Python --
    assert app.relief(1000.0, 0.29) == 290.0
    assert app.relief(0.0, 0.29) == 0.0

    assert app.batch([1, 2, 3], 2) == [[1, 2], [3]]


if __name__ == "__main__":
    check()
    print("ok")
