"""Offline checks for peer-review-router."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    submissions = json.loads((HERE / "sample_submissions.json").read_text(encoding="utf-8"))
    reviewers = json.loads((HERE / "sample_reviewers.json").read_text(encoding="utf-8"))
    assert len(submissions) >= 6 and len(reviewers) >= 6

    # Exact conflicts are settled in Python, never sent to the model.
    by_id = {r["id"]: r for r in reviewers}
    s1 = next(s for s in submissions if s["id"] == "S1")
    assert app.hard_conflict(s1, by_id["R2"]) is not None, "an author must be excluded"
    assert app.hard_conflict(s1, by_id["R1"]) is None, "an unrelated reviewer must not be"
    # Same institution, different case/spacing, still caught.
    assert app.hard_conflict(
        {"authors": [], "author_affiliations": ["Northfield  University."]},
        {"name": "x", "affiliation": "northfield university"},
    ) is not None
    # A declared co-author of an author is a hard conflict too.
    assert app.hard_conflict(
        {"authors": ["Marta Vilela"], "author_affiliations": ["Elsewhere"]},
        by_id["R8"],
    ) is not None
    hard = {
        (s, r)
        for s in range(len(submissions))
        for r in range(len(reviewers))
        if app.hard_conflict(submissions[s], reviewers[r])
    }
    assert hard, "the sample must exercise the Python conflict filter"

    pairs = [(s, r) for s in range(2) for r in range(len(reviewers))]
    questions = app.build_questions(submissions[:2], reviewers, pairs)
    assert len(questions) == len(pairs) * 2, len(questions)
    assert questions["fit_0_0"]["type"] == "score"
    assert questions["coi_0_0"]["type"] == "noul", "a conflict is its own Noul, not a fit level"
    assert all(len(level) > 15 for level in app.FIT_LEVELS), app.FIT_LEVELS

    # Batching for size only; nothing is dropped and order is preserved.
    assert app.plan_batches([10, 10, 10], 25) == [[0, 1], [2]]
    assert app.plan_batches([900], 100) == [[0]]
    assert app.plan_batches([], 50) == []

    # Allocation: veto first, then greedy on fit, under a load cap.
    def pair(s: str, r: str, fit: float, coi: float = 0.02, hard: str | None = None) -> dict:
        return {"submission": s, "reviewer": r, "fit": fit, "coi": coi, "hard_conflict": hard}

    pairs = [
        pair("P1", "A", 3.0, coi=0.9),            # best fit, conflicted -> never assigned
        pair("P1", "B", 2.4),
        pair("P1", "C", 1.8),
        pair("P2", "B", 2.9),
        pair("P2", "C", 2.2),
        pair("P2", "A", 0.3),                     # below the fit floor
        pair("P3", "A", 2.7, hard="same institution"),
    ]
    assignments, blocked, shortfalls = app.assign(
        pairs, reviews_per_paper=2, max_load=2, coi_at=0.45, fit_floor=1.0
    )
    chosen = {(a["submission"], a["reviewer"]) for a in assignments}
    assert ("P1", "A") not in chosen, "a conflicted pair must never be assigned"
    assert ("P3", "A") not in chosen, "a hard conflict must never be assigned"
    assert chosen == {("P1", "B"), ("P1", "C"), ("P2", "B"), ("P2", "C")}, chosen
    assert {(b["submission"], b["reviewer"]) for b in blocked} == {("P1", "A"), ("P3", "A")}
    assert [s["submission"] for s in shortfalls] == ["P3"], shortfalls

    # The load cap actually binds, and shortfalls are reported, never papered over.
    capped, _, short = app.assign(pairs, reviews_per_paper=2, max_load=1, coi_at=0.45, fit_floor=1.0)
    assert len(capped) == 2, capped
    assert len(short) >= 2, short

    # Relaxing the veto is a Python-side change: no inference re-runs.
    loose, _, _ = app.assign(pairs, reviews_per_paper=2, max_load=2, coi_at=0.95, fit_floor=1.0)
    assert ("P1", "A") in {(a["submission"], a["reviewer"]) for a in loose}
    assert ("P3", "A") not in {(a["submission"], a["reviewer"]) for a in loose}, (
        "a hard conflict is not a threshold and must survive any slider"
    )


if __name__ == "__main__":
    check()
    print("ok")
