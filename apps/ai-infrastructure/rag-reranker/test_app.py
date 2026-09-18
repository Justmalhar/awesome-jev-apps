"""Offline checks for rag-reranker. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app
    import baseline

    queries = app.load_queries(None)
    assert len(queries) >= 2, "several queries, so the comparison is not one anecdote"
    assert all(len(q["passages"]) >= 8 for q in queries), "a shallow candidate set proves nothing"
    assert {"id", "text"} <= set(queries[0]["passages"][0])

    questions = app.build_questions(queries)
    pairs = sum(len(q["passages"]) for q in queries)
    assert len(questions) == pairs * 3

    # Relevance is a Score; the two distractor properties are independent Nouls.
    assert questions["relevance_0_0"]["type"] == "score"
    assert questions["relevance_0_0"]["criteria"] == app.RELEVANCE_LEVELS
    for name in ("direct_0_0", "different_subject_0_0"):
        assert questions[name]["type"] == "noul", name
        assert set(questions[name]["criteria"]) == {"true", "false"}, name

    # Score levels must be concrete situations, not labels.
    assert all(len(level) > 15 for level in app.RELEVANCE_LEVELS)
    assert len(set(app.RELEVANCE_LEVELS)) == len(app.RELEVANCE_LEVELS)

    # The ranking function: weights are arguments, and the distractor penalty
    # must be able to sink an otherwise perfect-looking passage.
    weights = {"w_relevance": 1.0, "w_direct": 0.5, "w_different": 0.8}
    top = app.combine(3.0, 1.0, 0.0, **weights)
    wrong_plan = app.combine(3.0, 1.0, 1.0, **weights)
    assert top > wrong_plan, "a passage about another plan must be pushed down"
    assert abs(top - 1.5) < 1e-9 and abs(wrong_plan - 0.7) < 1e-9
    assert app.combine(0.0, 0.0, 0.0, **weights) == 0.0
    # zeroing a weight must switch that signal off entirely
    assert app.combine(3.0, 1.0, 1.0, w_relevance=1.0, w_direct=0.5, w_different=0.0) == top

    # Rank movement is computed in Python, positive meaning promoted.
    assert app.movement(["a", "b", "c"], ["c", "a", "b"]) == {"c": 2, "a": -1, "b": -1}
    assert app.movement(["a", "b"], ["a", "b"]) == {"a": 0, "b": 0}

    # The bundled baseline must run with nothing installed, and must fall for
    # the vocabulary trap -- that failure is what this app exists to fix.
    trap = [
        "Return a laptop: laptop return requests for a laptop accessory are handled here.",
        "Business plan hardware may be sent back up to 45 days after it arrives.",
        "Our Lisbon office is on the fourth floor and opens at nine.",
    ]
    ordering = baseline.rank("how long do I have to return a laptop", trap)
    assert [index for index, _ in ordering] == [0, 1, 2], ordering
    assert ordering[0][1] > ordering[1][1], "the trap must outscore the correct answer"
    assert baseline.rank("anything", []) == []
    assert baseline.rank("", ["text"]) == [(0, 0.0)]
    # ties keep original order, so the comparison table is stable between runs
    assert [i for i, _ in baseline.rank("zzz", ["a b", "a b"])] == [0, 1]
    # and it runs on the real sample without blowing up
    first = queries[0]
    assert len(baseline.rank(first["query"], [p["text"] for p in first["passages"]])) == \
        len(first["passages"])

    # Batching respects a context budget rather than hardcoding one.
    assert len(app.batch(queries, 10**6)) == 1
    assert sum(len(g) for g in app.batch(queries, 1)) == len(queries)


if __name__ == "__main__":
    check()
    print("ok")
