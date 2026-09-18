"""Offline checks for reading-queue. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    items = app.load_queue(None)
    assert len(items) >= 15, "a read-later pile should be a pile"
    assert all({"title", "words", "excerpt"} <= set(item) for item in items)

    indices = list(range(len(items)))
    questions = app.build_questions(items, indices, "postgres ingest")
    assert len(questions) == len(items) * 5, len(questions)
    assert questions["relevance_0"]["type"] == "score"
    assert questions["decides_0"]["type"] == "noul"
    assert all(len(level) > 15 for level in app.RELEVANCE_LEVELS + app.DEPTH_LEVELS)

    # the whole pile fits one request at any real context size
    assert len(app.split_batches(items, 64000)) == 1
    assert len(app.split_batches(items, 32000)) == 1
    # a tiny window forces a split, and every item still appears exactly once
    tiny = app.split_batches(items, 2000)
    assert len(tiny) > 1, tiny
    assert sorted(i for batch in tiny for i in batch) == indices
    assert all(batch for batch in tiny), "no empty batch"
    assert app.split_batches([], 64000) == []

    assert app.minutes_to_read({"words": 2200}) == 10
    assert app.minutes_to_read({"words": 10}) == 1, "never round a real article to zero"

    judgments = [
        {"relevance": 3.0, "depth": 3.0, "decides": 1.0, "evergreen": 1.0, "reference": 0.0},
        {"relevance": 0.0, "depth": 0.0, "decides": 0.0, "evergreen": 0.0, "reference": 1.0},
    ]
    two = [{"title": "a", "words": 2200}, {"title": "b", "words": 2200}]
    weights = {"relevance": 1.0, "depth": 0.6, "decides": 0.8, "evergreen": 0.2}
    ranked = app.rank(two, judgments, weights)
    assert [index for _, index in ranked] == [0, 1]
    # zeroing every weight leaves a deterministic order rather than a random one
    flat = app.rank(two, judgments, {k: 0.0 for k in weights})
    assert [index for _, index in flat] == [0, 1]

    # the session takes what fits, in rank order, and skips what does not
    assert app.fill_session(ranked, two, 10) == [0]
    assert app.fill_session(ranked, two, 20) == [0, 1]
    assert app.fill_session(ranked, two, 5) == []


if __name__ == "__main__":
    check()
    print("ok")
