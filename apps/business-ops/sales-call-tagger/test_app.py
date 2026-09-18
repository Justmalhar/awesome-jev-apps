"""Offline checks for sales-call-tagger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    calls = json.loads((HERE / "sample_calls.json").read_text(encoding="utf-8"))
    assert len(calls) >= 8
    assert all({"id", "account", "notes"} <= set(c) for c in calls)

    competitors = app.DEFAULT_COMPETITORS
    questions = app.build_questions(calls, competitors)
    per_call = len(app.OBJECTIONS) + len(app.BUYING_SIGNALS) + len(competitors) + 1
    assert len(questions) == len(calls) * per_call

    # objections and competitors are independent -- one Noul each, never a Choice
    for key in {**app.OBJECTIONS, **app.BUYING_SIGNALS}:
        assert questions[f"{key}__0"]["type"] == "noul"
    assert questions["comp0__0"]["type"] == "noul"
    # the next step is exclusive, so a Choice -- with an escape hatch
    assert questions["next__0"]["type"] == "choice"
    assert "no_next_step" in questions["next__0"]["criteria"]
    # a call with no competitors configured still asks the rest
    assert len(app.build_questions(calls[:1], [])) == per_call - len(competitors)

    live = {"next_step": "demo_booked", "problem_owned": 0.9, "timing": 0.1,
            "incumbent_satisfaction": 0.1}
    assert app.qualify(live, 0.6) == "advancing"
    # a strong next step does not rescue a call where the problem is not owned
    assert app.qualify({**live, "problem_owned": 0.2}, 0.6) == "working"
    # nor does it survive a timing block
    assert app.qualify({**live, "timing": 0.95}, 0.6) == "working"
    dead = {"next_step": "no_next_step", "problem_owned": 0.2, "timing": 0.9,
            "incumbent_satisfaction": 0.8}
    assert app.qualify(dead, 0.6) == "stalled"
    # raising the threshold above the evidence stops it being stalled
    assert app.qualify(dead, 0.99) == "working"

    frame = pd.DataFrame(
        [{"price": 0.9, "timing": 0.9}, {"price": 0.9, "timing": 0.1}, {"price": 0.1, "timing": 0.1}]
    )
    matrix = app.objection_matrix(frame, ["price", "timing"], 0.6)
    assert matrix.loc["price", "price"] == 2
    assert matrix.loc["price", "timing"] == 1


if __name__ == "__main__":
    check()
    print("ok")
