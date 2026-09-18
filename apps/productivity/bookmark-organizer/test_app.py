"""Offline checks for bookmark-organizer. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    tree = app.parse_taxonomy(app.DEFAULT_TAXONOMY)
    assert set(tree) == {"Engineering", "Craft", "Career", "Home"}, tree
    assert "Databases and storage" in tree["Engineering"]
    # a top-level folder with no children is legal and keeps an empty list
    assert app.parse_taxonomy("Alone\n") == {"Alone": []}
    # indentation, not bullets, defines nesting; blank lines are ignored
    assert app.parse_taxonomy("A\n\n  b\n  c\nB\n  d\n") == {"A": ["b", "c"], "B": ["d"]}
    # an indented line before any parent is dropped rather than crashing
    assert app.parse_taxonomy("   orphan\nA\n  b\n") == {"A": ["b"]}

    bookmarks = app.load_bookmarks(None)
    assert len(bookmarks) >= 20, "years of bookmarks means a real pile"

    indices = list(range(len(bookmarks)))
    questions = app.build_questions(indices, tree)
    assert len(questions) == len(bookmarks) * 4, len(questions)
    # both Choices carry an escape hatch
    assert app.UNFILED in questions["top_0"]["criteria"]
    assert app.NO_SUB in questions["sub_0"]["criteria"]
    assert len(questions["sub_0"]["criteria"]) == sum(len(v) for v in tree.values()) + 1
    assert all(len(text) > 15 for text in questions["top_0"]["criteria"].values())

    assert len(app.split_batches(bookmarks, 64000)) == 1
    chunks = app.split_batches(bookmarks, 1500)
    assert len(chunks) > 1 and sorted(i for c in chunks for i in c) == indices

    # the two levels agreeing gives a clean path
    agreed = {"top": "Engineering", "sub": "Engineering/Databases and storage"}
    assert app.file_path(agreed, tree) == ("Engineering/Databases and storage", False)
    # disagreement is surfaced, not silently resolved
    crossed = {"top": "Engineering", "sub": "Career/Interviewing and hiring"}
    path, conflict = app.file_path(crossed, tree)
    assert conflict and path == "Engineering/_needs a look"
    # the escape hatches
    assert app.file_path({"top": app.UNFILED, "sub": "Home/Cooking"}, tree) == ("Unfiled", False)
    assert app.file_path({"top": "Craft", "sub": app.NO_SUB}, tree) == ("Craft", False)

    sure = {"top": "Craft", "top_confidence": 0.9, "sub_confidence": 0.9}
    assert not app.needs_review(sure, confidence_gate=0.5)
    assert app.needs_review({**sure, "sub_confidence": 0.2}, confidence_gate=0.5)
    assert app.needs_review({**sure, "top_confidence": 0.2}, confidence_gate=0.5)
    assert app.needs_review({**sure, "top": app.UNFILED}, confidence_gate=0.5)


if __name__ == "__main__":
    check()
    print("ok")
