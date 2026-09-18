"""Offline checks for candidate-screener."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    applications = json.loads((HERE / "sample_applications.json").read_text(encoding="utf-8"))
    assert len(applications) >= 8
    assert all({"id", "resume"} <= set(a) for a in applications)
    # sample data must not carry identity attributes that have no place in screening
    for application in applications:
        assert "name" not in application, "sample applications are anonymised"

    role = app.DEFAULT_ROLE
    questions = app.build_questions(applications, role)
    per_application = len(role["required"]) + len(role["preferred"]) + 1
    assert len(questions) == len(applications) * per_application
    # every criterion is its own Noul -- nothing is composed
    assert questions["required_0_0"]["type"] == "noul"
    assert questions["evidence_0"]["type"] == "noul"

    # the default criteria must themselves be job-related
    assert app.screen_criteria(role["required"] + role["preferred"]) == []
    # protected characteristics are blocked before anything is sent
    for bad in (
        "The candidate graduated within the last three years.",
        "The candidate is a native speaker of English.",
        "The candidate has no gaps in employment due to family reasons.",
        "The candidate would be a good culture fit for a young team.",
        "The candidate holds citizenship in this country.",
    ):
        assert app.screen_criteria([bad]), bad

    # THE contract: there is no rejection outcome, at any threshold, ever
    outcomes = set()
    for required in ([0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], [0.9, 0.5, 0.1, 0.8], []):
        for met_at, review_at in ((0.75, 0.25), (0.99, 0.01), (0.5, 0.49)):
            outcomes.add(app.triage(required, met_at, review_at)[0])
    assert outcomes <= {"advance", "review", "review_thin"}, outcomes
    assert not any("reject" in outcome for outcome in outcomes)

    assert app.triage([0.9, 0.9, 0.9, 0.9], 0.75, 0.25)[0] == "advance"
    assert app.triage([0.9, 0.9, 0.9, 0.05], 0.75, 0.25)[0] == "review_thin"
    # borderline evidence is never treated as absent
    assert app.triage([0.9, 0.9, 0.9, 0.5], 0.75, 0.25)[0] == "review"
    # an empty criteria list must go to a human, not to "advance"
    assert app.triage([], 0.75, 0.25)[0] == "review"
    # the flagged criterion is named, so a human knows what to verify
    assert "#4" in app.triage([0.9, 0.9, 0.9, 0.05], 0.75, 0.25)[1]


if __name__ == "__main__":
    check()
    print("ok")
