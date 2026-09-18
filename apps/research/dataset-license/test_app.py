"""Offline checks for dataset-license."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    datasets = json.loads((HERE / "sample_licences.json").read_text(encoding="utf-8"))
    assert datasets
    assert all({"id", "name", "licence_text"} <= set(d) for d in datasets)

    questions = app.build_questions(datasets)
    per_dataset = len(app.USES) * 2 + len(app.OBLIGATIONS) + 1
    assert len(questions) == len(datasets) * per_dataset, len(questions)

    # Grant and restriction are SEPARATE Nouls -- never one probability.
    assert questions["grant_0_commercial"]["type"] == "noul"
    assert questions["forbid_0_commercial"]["type"] == "noul"

    family = questions["family_0"]
    assert family["type"] == "choice"
    assert "unclear" in family["criteria"], "a Choice needs a no-match option"
    assert len(family["criteria"]) >= 3

    # The four corners of the two-signal space are all distinct outcomes.
    assert app.judge(0.95, 0.05, 0.75, 0.25)[0] == "permitted"
    assert app.judge(0.95, 0.90, 0.75, 0.25)[0] == "prohibited", "a restriction vetoes a grant"
    assert app.judge(0.10, 0.10, 0.75, 0.25)[0] == "not_granted", "silence is not permission"
    assert app.judge(0.60, 0.40, 0.75, 0.25)[0] == "unclear"
    assert app.judge(0.75, 0.25, 0.75, 0.25)[0] == "permitted", "thresholds are inclusive"

    # Veto across intended uses, never an average.
    assert app.overall({"a": "permitted", "b": "permitted"}) == ("consistent", [])
    result, drivers = app.overall({"a": "permitted", "b": "prohibited", "c": "unclear"})
    assert result == "blocked" and drivers == ["b"], (result, drivers)
    assert app.overall({"a": "permitted", "b": "unclear"}) == ("seek_advice", ["b"])
    # Narrowing the intent changes the verdict with no inference re-run.
    assert app.overall({"a": "permitted"}) == ("consistent", [])


if __name__ == "__main__":
    check()
    print("ok")
