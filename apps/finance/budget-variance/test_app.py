"""Offline checks for budget-variance. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    budget = pd.read_csv(HERE / "sample_budget.csv")
    notes = pd.read_csv(HERE / "sample_notes.csv")
    frame = app.reconcile(budget, notes)

    # -- every number is pandas', and must be exact --
    assert len(frame) == len(budget), "the join must not duplicate or drop budget lines"
    energy = frame[frame["line_id"] == "BL-11"].iloc[0]
    assert energy["variance"] == 41900
    assert energy["variance_pct"] == 48.72, energy["variance_pct"]
    assert energy["direction"] == "overspend"
    content = frame[frame["line_id"] == "BL-09"].iloc[0]
    assert content["variance"] == -43600 and content["direction"] == "underspend"
    assert frame[frame["line_id"] == "BL-08"].iloc[0]["direction"] == "on_budget"
    # lines with no note must survive the join with an empty string, not NaN
    assert frame[frame["line_id"] == "BL-01"].iloc[0]["note"] == ""

    # -- materiality filters before the model is involved --
    lines = app.material(frame, 25000, 5.0)
    assert 0 < len(lines) < len(frame), f"{len(lines)} of {len(frame)}"
    assert "BL-04" not in set(lines["line_id"]), "a 1,200 variance is not material"
    assert "BL-11" in set(lines["line_id"])
    assert "BL-10" not in set(lines["line_id"]), "11,200 is large but only 2.6 percent"
    assert app.material(frame, 10_000_000, 0.0).empty

    # -- question design --
    records = lines.to_dict("records")
    questions = app.build_questions(records)
    assert len(questions) == 5 * len(records)
    assert app.NO_CAUSE in questions["cause_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.CAUSES.values())
    assert all(len(level) > 15 for level in app.SPECIFICITY_LEVELS)
    assert questions["cause_0"]["type"] == "choice"
    assert questions["specificity_0"]["type"] == "score"
    assert [questions[f"{p}_0"]["type"] for p in ("recurring", "controllable", "action")] == ["noul"] * 3

    # -- what finance does next, at the boundaries --
    gates = dict(recurring_gate=0.6, control_gate=0.6)
    assert app.action(app.NO_CAUSE, 0.9, 0.9, **gates) == "explanation_required", (
        "an unexplained material variance is a conversation whatever else is true"
    )
    assert app.action("price_increase", 0.9, 0.1, **gates) == "reforecast"
    assert app.action("waste_or_oversight", 0.9, 0.9, **gates) == "reforecast_and_challenge"
    assert app.action("waste_or_oversight", 0.1, 0.9, **gates) == "challenge_owner"
    assert app.action("timing_shift", 0.1, 0.1, **gates) == "phasing_correction"
    assert app.action("project_delay", 0.1, 0.1, **gates) == "phasing_correction"
    assert app.action("unplanned_event", 0.1, 0.1, **gates) == "accept"
    assert app.action("price_increase", 0.6, 0.1, **gates) == "reforecast", "gate is inclusive"
    assert app.action("price_increase", 0.59, 0.59, **gates) == "accept"

    # -- the only multiplication in the app --
    assert app.run_rate_impact(41900, 2) == 83800.0
    assert app.run_rate_impact(41900, 0) == 0.0
    assert app.run_rate_impact(-43600, 2) == -87200.0


if __name__ == "__main__":
    check()
    print("ok")
