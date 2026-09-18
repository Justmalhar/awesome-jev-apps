"""Offline checks for subscription-auditor. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    # -- merchant normalisation: the same shop under two statement spellings --
    assert app.merchant_key("NETFLIX.COM 4471") == app.merchant_key("NETFLIX.COM*8812")
    assert app.merchant_key("TESCO SUPERSTORE 4001") == app.merchant_key("TESCO SUPERSTORE 4099")
    assert app.merchant_key("NETFLIX.COM 4471") != app.merchant_key("SPOTIFY UK LTD")

    # -- grouping is pure pandas and must measure, not guess --
    frame = pd.read_csv(HERE / "sample_transactions.csv")
    grouped = app.group_merchants(frame, min_charges=2)
    assert not grouped.empty
    assert (grouped["charges"] >= 2).all()
    assert "NETFLIX.COM" in set(grouped["merchant"])
    netflix = grouped[grouped["merchant"] == "NETFLIX.COM"].iloc[0]
    assert 25 <= netflix["typical_gap_days"] <= 35, netflix["typical_gap_days"]
    assert app.group_merchants(frame, min_charges=99).empty
    # income must never be audited as a subscription
    assert not any("PAYROLL" in m for m in grouped["merchant"])

    # -- question design --
    records = grouped.to_dict("records")
    questions = app.build_questions(records)
    assert len(questions) == 4 * len(records)
    assert "not_a_subscription" in questions["kind_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(level) > 15 for level in app.CANCEL_LEVELS)
    assert all(len(text) > 15 for text in app.KINDS.values())
    assert questions["recurring_0"]["type"] == "noul"
    assert questions["cancel_0"]["type"] == "score"

    # -- policy is Python, and is tested at its boundaries --
    assert app.verdict(0.9, 0.9, 0.6, 0.55) == "cancel_candidate"
    assert app.verdict(0.9, 0.2, 0.6, 0.55) == "active"
    assert app.verdict(0.1, 0.9, 0.6, 0.55) == "not_recurring", "forgotten alone must not cancel it"
    assert app.verdict(0.6, 0.55, 0.6, 0.55) == "cancel_candidate", "gate is inclusive"
    assert app.verdict(0.59, 0.99, 0.6, 0.55) == "not_recurring"

    # -- arithmetic stays in Python --
    assert app.annualise(10.0, 30.0) == 121.67
    assert app.annualise(120.0, 365.0) == 120.0
    assert app.annualise(10.0, None) == 0.0
    assert app.annualise(10.0, 0) == 0.0


if __name__ == "__main__":
    check()
    print("ok")
