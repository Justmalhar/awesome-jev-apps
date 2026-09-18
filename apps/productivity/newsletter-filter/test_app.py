"""Offline checks for newsletter-filter. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    items = app.load_digest(None)
    assert len(items) >= 20
    assert len({item["newsletter"] for item in items}) >= 3, "a digest spans newsletters"

    questions = app.build_questions(items, "databases")
    assert len(questions) == len(items) * 5, len(questions)
    assert all(len(level) > 15 for level in app.MATCH_LEVELS + app.SUBSTANCE_LEVELS)
    assert questions["match_0"]["type"] == "score"
    assert questions["sponsored_0"]["type"] == "noul"

    gates = {"match_gate": 2.0, "substance_gate": 1.5, "noul_gate": 0.6, "drop_duplicates": True}
    good = {"match": 3.0, "substance": 3.0, "sponsored": 0.0, "withholds": 0.0, "everywhere": 0.0}
    assert app.keeps(good, **gates)
    # a sponsored item is cut however well it matches
    assert not app.keeps({**good, "sponsored": 0.9}, **gates)
    assert app.dropped_because({**good, "sponsored": 0.9}, **gates) == "sponsored"
    # widely covered news is cut only while the checkbox is on
    assert not app.keeps({**good, "everywhere": 0.9}, **gates)
    assert app.keeps({**good, "everywhere": 0.9}, **{**gates, "drop_duplicates": False})
    # off-topic and empty are separate reasons
    assert app.dropped_because({**good, "match": 0.5}, **gates) == "not your subject"
    assert app.dropped_because({**good, "substance": 0.5}, **gates) == "nothing in it"
    assert app.dropped_because(good, **gates) == "kept"
    # clickbait alone never cuts an item; it is shown as a flag
    assert app.keeps({**good, "withholds": 1.0}, **gates)
    # exactly on both gates survives
    assert app.keeps({**good, "match": 2.0, "substance": 1.5}, **gates)


if __name__ == "__main__":
    check()
    print("ok")
