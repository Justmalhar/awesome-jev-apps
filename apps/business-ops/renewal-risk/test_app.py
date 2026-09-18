"""Offline checks for renewal-risk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    accounts = json.loads((HERE / "sample_accounts.json").read_text(encoding="utf-8"))
    assert len(accounts) >= 8
    assert all({"name", "segment", "arr", "notes"} <= set(a) for a in accounts)

    questions = app.build_questions(accounts)
    per_account = len(app.DIMENSIONS) + len(app.FLAGS)
    assert len(questions) == len(accounts) * per_account

    for key, spec in app.DIMENSIONS.items():
        assert questions[f"{key}__0"]["type"] == "score"
        assert all(len(level) > 15 for level in spec["levels"]), key
    for key in app.FLAGS:
        assert questions[f"{key}__0"]["type"] == "noul"

    weights = {key: 1.0 for key in app.DIMENSIONS}
    healthy = {**{k: 0.0 for k in app.DIMENSIONS}, **{k: 0.0 for k in app.FLAGS}}
    dire = {
        **{k: len(spec["levels"]) - 1 for k, spec in app.DIMENSIONS.items()},
        **{k: 0.0 for k in app.FLAGS},
    }
    assert app.composite(healthy, weights) == 0.0
    assert abs(app.composite(dire, weights) - 100.0) < 1e-6
    # a zero weight must drop a dimension out entirely
    only_adoption = {**weights, **{k: 0.0 for k in app.DIMENSIONS if k != "adoption"}}
    assert abs(app.composite(dire, only_adoption) - 100.0) < 1e-6

    assert app.band(healthy, weights, 0.6, 60, 35)[0] == "green"
    assert app.band(dire, weights, 0.6, 60, 35)[0] == "red"

    # two hard flags go red even on a perfect composite -- vetoes are not averaged
    flagged = {**healthy, "champion_departed": 0.9, "exit_enquiry": 0.8}
    verdict, total, reason = app.band(flagged, weights, 0.6, 60, 35)
    assert verdict == "red" and total == 0.0 and "champion_departed" in reason
    # one flag alone is amber, not red
    one = {**healthy, "champion_departed": 0.9}
    assert app.band(one, weights, 0.6, 60, 35)[0] == "amber"
    # expansion in flight offsets a single flag
    offset = {**one, "expansion_in_flight": 0.9}
    assert app.band(offset, weights, 0.6, 60, 35)[0] == "green"
    # but expansion must not rescue a genuinely red composite
    assert app.band({**dire, "expansion_in_flight": 0.95}, weights, 0.6, 60, 35)[0] == "red"
    # raising the gate disarms the flags
    assert app.band(flagged, weights, 0.99, 60, 35)[0] == "green"

    frame = pd.DataFrame(
        [
            {"account": "a", "band": "red", "arr": 100.0, "risk": 80.0},
            {"account": "b", "band": "red", "arr": 50.0, "risk": 70.0},
            {"account": "c", "band": "green", "arr": 10.0, "risk": 5.0},
        ]
    )
    table = app.portfolio(frame)
    assert table.loc["red", "accounts"] == 2
    assert table.loc["red", "arr"] == 150.0
    assert table.loc["red", "mean_risk"] == 75.0


if __name__ == "__main__":
    check()
    print("ok")
