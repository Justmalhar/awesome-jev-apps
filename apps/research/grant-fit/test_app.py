"""Offline checks for grant-fit."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    calls = json.loads((HERE / "sample_calls.json").read_text(encoding="utf-8"))
    proposal = json.loads((HERE / "sample_proposal.json").read_text(encoding="utf-8"))
    assert len(calls) >= 6 and proposal["summary"]

    # Dates are Python's job. The model is never handed one.
    live, closed = app.open_calls(calls, date(2027, 1, 1), 30)
    assert closed, "the sample must exercise the deadline filter"
    assert all(c["deadline"] >= "2027-01-31" for c in live), [c["deadline"] for c in live]
    assert len(live) + len(closed) == len(calls)
    # Exactly-at-the-lead-time is still live; one day short is not.
    edge = [{"funder": "f", "name": "n", "deadline": "2027-01-31"}]
    assert app.open_calls(edge, date(2027, 1, 1), 30)[0] == edge
    assert app.open_calls(edge, date(2027, 1, 2), 30)[1] == edge
    # An undated call is a human's problem, not a silent drop.
    undated = [{"funder": "f", "name": "n"}]
    assert app.open_calls(undated, date(2027, 1, 1), 30)[0] == undated

    questions = app.build_questions(proposal, live)
    assert len(questions) == len(live) * 3, len(questions)
    assert questions["fit_0"]["type"] == "score"
    assert questions["eligible_0"]["type"] == "noul"
    assert questions["disqualified_0"]["type"] == "noul"

    # Score levels are concrete situations, not labels.
    assert len(app.FIT_LEVELS) >= 3
    assert all(len(level) > 15 for level in app.FIT_LEVELS), app.FIT_LEVELS

    # Veto beats fit: a perfect match you cannot apply to scores zero, not "high".
    assessments = [
        {"index": 0, "fit": 2.9, "eligible": 0.02, "disqualified": 0.1},   # ineligible
        {"index": 1, "fit": 2.8, "eligible": 0.95, "disqualified": 0.9},   # fails a requirement
        {"index": 2, "fit": 2.1, "eligible": 0.9, "disqualified": 0.05},   # shortlist
        {"index": 3, "fit": 2.6, "eligible": 0.9, "disqualified": 0.05},   # shortlist, better
        {"index": 4, "fit": 0.4, "eligible": 0.9, "disqualified": 0.05},   # below the floor
    ]
    shortlist, rejected = app.triage(assessments, eligible_at=0.40, disqualify_at=0.60, fit_floor=1.5)
    assert [r["index"] for r in shortlist] == [3, 2], shortlist
    assert {r["index"] for r in rejected} == {0, 1, 4}
    assert all(r["reason"] for r in shortlist + rejected)
    # Loosening the floor re-ranks with no inference re-run.
    assert len(app.triage(assessments, 0.40, 0.60, 0.0)[0]) == 3


if __name__ == "__main__":
    check()
    print("ok")
