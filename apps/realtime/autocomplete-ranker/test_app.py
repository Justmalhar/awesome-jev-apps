"""Offline checks for autocomplete-ranker."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    context = app.CONTEXTS[0]
    questions = app.build_questions(context)
    assert len(questions) == 2, "the whole candidate set is one request"
    assert app.NO_MATCH in questions["intended"]["criteria"], "a Choice needs a no-match option"
    assert len(questions["intended"]["criteria"]) == len(context["candidates"]) + 1
    assert questions["worth_showing"]["type"] == "noul"
    # Every scenario must be rankable, including the one where nothing fits.
    for scenario in app.CONTEXTS:
        assert len(app.build_questions(scenario)["intended"]["criteria"]) >= 3

    candidates = context["candidates"]
    probabilities = {"c1": 0.10, "c2": 0.05, "c3": 0.05, "c4": 0.70, "c5": 0.05, "c6": 0.05}

    # weight 1.0 = pure meaning; weight 0.0 = exactly the popularity ranker.
    assert app.rank(probabilities, candidates, 1.0)[0][0] == "c4"
    assert app.rank(probabilities, candidates, 0.0)[0][0] == "c1"
    assert [cid for cid, _ in app.rank({}, candidates, 0.0)] == [
        cid for cid, _ in sorted(((c["id"], c["popularity"]) for c in candidates), key=lambda kv: -kv[1])
    ]

    blended = app.rank(probabilities, candidates, 0.5)
    assert len(blended) == len(candidates), "every candidate keeps a rank"
    assert abs(dict(blended)["c4"] - (0.5 * 0.70 + 0.5 * 0.44)) < 1e-9
    # A candidate with no probability returned must not vanish.
    assert set(cid for cid, _ in app.rank({"c1": 1.0}, candidates, 0.9)) == {c["id"] for c in candidates}
    # Ties break deterministically, so the popup does not flicker between renders.
    flat = [{"id": "a", "popularity": 0.5}, {"id": "b", "popularity": 0.5}]
    assert app.rank({}, flat, 0.0) == app.rank({}, flat, 0.0) == [("a", 0.5), ("b", 0.5)]


if __name__ == "__main__":
    check()
    print("ok")
