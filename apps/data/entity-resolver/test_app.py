"""Offline checks for entity-resolver. No key, no network."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # ── The rubric is the contract: three concrete actions, not adjectives. ──
    assert len(app.LEVELS) == 3, app.LEVELS
    assert all(len(level) > 15 for level in app.LEVELS), "levels must be situations, not labels"

    # ── Blocking: recall-oriented prefilter, and it must actually reduce. ──
    records = [
        {"name": "Acme Corp", "city": "London"},
        {"name": "ACME Corporation", "city": "London"},
        {"name": "Tanaka Precision KK", "city": "Tokyo"},
    ]
    everything = app.blocking_pairs(records, 0.0)
    assert len(everything) == 3, "cutoff 0 must keep all n(n-1)/2 pairs"
    assert [t[2] for t in everything] == sorted((t[2] for t in everything), reverse=True), \
        "pairs come back most-similar-first"

    tight = app.blocking_pairs(records, 0.5)
    assert (0, 1) in [(i, j) for i, j, _ in tight], "the obvious duplicate must survive blocking"
    assert (0, 2) not in [(i, j) for i, j, _ in tight], "unrelated records must be filtered out"
    assert app.blocking_pairs(records, 1.01) == [], "an impossible cutoff keeps nothing"
    assert app.blocking_pairs([], 0.5) == [], "no records, no pairs"

    # ── Union-find: merges must compose transitively into one cluster. ──
    union = app.Union(5)
    union.union(0, 1)
    union.union(1, 2)
    clusters = union.clusters()
    assert sorted(len(members) for members in clusters.values()) == [1, 1, 3], clusters
    assert union.find(0) == union.find(2), "A=B and B=C must put A and C together"
    assert union.find(3) != union.find(4), "untouched records stay singletons"
    union.union(2, 0)  # already joined; must be idempotent
    assert sorted(len(m) for m in union.clusters().values()) == [1, 1, 3]

    # ── One question per surviving pair, all in a single ask(). ──
    pairs = [(0, 1, 0.9), (0, 2, 0.5)]
    questions = app.build_questions(pairs)
    assert len(questions) == len(pairs), "exactly one question per pair, batched"
    assert set(questions) == {"pair_0", "pair_1"}
    for question in questions.values():
        assert question["type"] == "score"
        assert question["criteria"] == app.LEVELS
        # State is referenced by path, never inlined -- ingested once, asked many times.
        assert question["instructions"]["record_a"].startswith("`pairs[")
    assert app.build_questions([]) == {}

    # ── The bundled sample must exist and carry the traps the README claims. ──
    sample = HERE / "sample_records.csv"
    assert sample.is_file(), "the app must run cold, before the user supplies anything"
    with sample.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 20, len(rows)
    names = [row["company_name"] for row in rows]
    assert "Acme Corp" in names and "ACME Corporation" in names, "obvious duplicates"
    assert sum("Stark Industries" in name for name in names) == 2, "near-miss subsidiary trap"
    assert len({row["source_system"] for row in rows}) == 2, "records come from two merged systems"


if __name__ == "__main__":
    check()
    print("ok")
