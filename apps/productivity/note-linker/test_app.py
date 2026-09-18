"""Offline checks for note-linker. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    vault = app.load_vault(None)
    note = app.load_new_note(None)
    assert len(vault) >= 10 and note.strip(), "samples must make the app run cold"
    assert all({"title", "excerpt"} <= set(item) for item in vault)

    indices = list(range(len(vault)))
    questions = app.build_questions(indices)
    assert len(questions) == len(vault) * 5, len(questions)
    assert all(len(level) > 15 for level in app.RELATION_LEVELS)
    assert questions["relation_0"]["type"] == "score"
    assert questions["duplicate_0"]["type"] == "noul"
    # the new note lives in state once, not copied into every question
    assert questions["relation_0"]["instructions"]["pair"]["new_note"] == "`new_note`"

    assert len(app.split_batches(vault, 64000)) == 1
    chunks = app.split_batches(vault, 1200)
    assert len(chunks) > 1 and sorted(i for c in chunks for i in c) == indices

    gates = {"relate_gate": 1.8, "noul_gate": 0.6}
    base = {"relation": 3.0, "extends": 0.0, "background": 0.0, "contradicts": 0.0, "duplicate": 0.0}
    assert app.link_type({**base, "duplicate": 0.9}, **gates) == "merge"
    assert app.link_type({**base, "contradicts": 0.9}, **gates) == "contradicts"
    assert app.link_type({**base, "extends": 0.8}, **gates) == "extends"
    assert app.link_type({**base, "background": 0.8}, **gates) == "background"
    assert app.link_type(base, **gates) == "see_also"
    assert app.link_type({**base, "relation": 0.5}, **gates) is None
    # a duplicate outranks a weak relation score: merging is worth surfacing anyway
    assert app.link_type({**base, "relation": 0.1, "duplicate": 0.95}, **gates) == "merge"
    # a conflict outranks an extension, because the reader must resolve it first
    assert app.link_type({**base, "extends": 0.9, "contradicts": 0.9}, **gates) == "contradicts"

    block = app.render_links([("merge", {"title": "A", "path": "a.md"}, {"relation": 3.0}),
                              ("see_also", {"title": "B", "path": "b.md"}, {"relation": 2.0})])
    assert "[[A]]" in block and "[[B]]" in block
    assert block.index("[[A]]") < block.index("[[B]]"), "merge candidates come first"
    assert "No note" in app.render_links([])


if __name__ == "__main__":
    check()
    print("ok")
