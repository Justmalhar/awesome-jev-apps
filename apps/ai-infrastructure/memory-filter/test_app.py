"""Offline checks for memory-filter. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    store = app.load_store(None)
    memories = store["memories"]
    assert len(memories) >= 20 and len(store["turns"]) >= 3
    assert {"id", "text"} <= set(memories[0])

    assert app.pick_turn(store, None) is store["turns"][0]
    assert app.pick_turn(store, "t-2")["id"] == "t-2"
    try:
        app.pick_turn(store, "nope")
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown turn id must fail loudly")

    # The WHOLE store is ranked in one request: one question set, no chunking.
    questions = app.build_questions(memories)
    assert len(questions) == len(memories) * (len(app.PROPERTIES) + 1)
    for name in app.PROPERTIES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["omission_0"]["type"] == "score"
    assert all(len(level) > 15 for level in app.OMISSION_LEVELS)
    assert len(set(app.OMISSION_LEVELS)) == len(app.OMISSION_LEVELS)

    assert app.tokens_of({"text": "x" * 400}) == 100
    assert app.tokens_of({"text": ""}) == 1, "a memory always costs something"

    quiet = dict.fromkeys(app.PROPERTIES, 0.0)
    hot = {**quiet, "relevant": 1.0, "would_change_reply": 1.0}
    assert app.rank_of(hot, 3.0, standing_bonus=0.45) > app.rank_of(quiet, 0.0, standing_bonus=0.45)
    # A standing preference outranks an equally irrelevant one-off.
    standing = {**quiet, "standing_preference": 1.0}
    assert app.rank_of(standing, 0.0, standing_bonus=0.45) == 0.45
    assert app.rank_of(quiet, 0.0, standing_bonus=0.45) == 0.0

    def row(mid, rank, tokens, superseded=0.0):
        return {"id": mid, "rank": rank, "tokens": tokens,
                "properties": {**quiet, "superseded": superseded}}

    gates = {"min_rank": 0.55, "superseded_p": 0.60}
    kept, dropped = app.select(
        [row("a", 0.9, 10), row("b", 0.8, 10), row("c", 0.2, 10), row("d", 0.95, 10, 0.9)],
        budget_tokens=20, **gates)
    assert [r["id"] for r in kept] == ["a", "b"], "highest rank first, within budget"
    reasons = {r["id"]: r["reason"] for r in dropped}
    # A superseded memory is dropped however highly it ranks -- stale beats relevant.
    assert reasons["d"] == "superseded"
    assert reasons["c"] == "below-rank"

    kept, dropped = app.select([row("a", 0.9, 30), row("b", 0.8, 30)],
                               budget_tokens=30, **gates)
    assert [r["id"] for r in kept] == ["a"]
    assert [r["reason"] for r in dropped] == ["no-budget"]

    # Budget exactly fits: nothing is squeezed out.
    kept, dropped = app.select([row("a", 0.9, 10)], budget_tokens=10, **gates)
    assert len(kept) == 1 and not dropped


if __name__ == "__main__":
    check()
    print("ok")
