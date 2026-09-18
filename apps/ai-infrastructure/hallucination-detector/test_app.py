"""Offline checks for hallucination-detector. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    data = app.load_data(None)
    assert {"question", "context", "answers"} <= set(data)
    assert len(data["answers"]) >= 3 and len(data["context"]) >= 3

    # Sentence splitting is Python's job, and must survive abbreviations and quotes.
    assert app.split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert app.split_sentences("No terminator") == ["No terminator"]
    assert app.split_sentences("   ") == []
    assert len(app.split_sentences('He said "stop". She left.')) == 2

    rows = app.flatten(data["answers"])
    assert len(rows) > len(data["answers"]), "answers must split into several sentences"
    assert rows[0]["answer_id"] == data["answers"][0]["id"]

    questions = app.build_questions(rows)
    assert len(questions) == len(rows) * (len(app.PROPERTIES) + 1)
    for name in app.PROPERTIES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["harm_0"]["type"] == "score"
    assert all(len(level) > 15 for level in app.HARM_LEVELS)
    assert len(set(app.HARM_LEVELS)) == len(app.HARM_LEVELS)

    gates = {"grounded_p": 0.6, "contradict_p": 0.6, "invented_p": 0.6, "hedge_p": 0.7}
    base = {"grounded": 0.0, "contradicts": 0.0, "invented_specific": 0.0, "hedge": 0.0}
    assert app.classify({**base, "grounded": 0.9}, **gates) == "grounded"
    assert app.classify({**base, "contradicts": 0.8}, **gates) == "contradicted"
    assert app.classify({**base, "invented_specific": 0.9}, **gates) == "fabricated"
    assert app.classify(base, **gates) == "unsupported"
    # a hedge is never a hallucination, even with nothing grounding it
    assert app.classify({**base, "hedge": 0.8, "invented_specific": 0.9}, **gates) == "hedge"
    # grounded wins over an "invented" specific that is in fact supported
    assert app.classify({**base, "grounded": 0.9, "invented_specific": 0.9}, **gates) == "grounded"

    roll = {"harm_gate": 1.5, "bad_sentence_fraction": 0.34}
    assert app.grade(["grounded", "grounded", "hedge"], [0.1, 0.1, 0.1], **roll) == "pass"
    assert app.grade(["grounded", "fabricated"], [0.1, 2.5], **roll) == "fail"
    # a harmless fabrication is a review, not an automatic failure
    assert app.grade(["grounded", "grounded", "fabricated"], [0.1, 0.1, 0.2], **roll) == "review"
    # too many weak sentences fails even when each one is harmless
    assert app.grade(["unsupported", "unsupported", "grounded"], [0.1, 0.1, 0.1], **roll) == "fail"
    # an answer that is nothing but hedges is not a grounding failure
    assert app.grade(["hedge", "hedge"], [3.0, 3.0], **roll) == "pass"

    assert len(app.batch(rows, 10**6)) == 1
    assert sum(len(g) for g in app.batch(rows, 20)) == len(rows)


if __name__ == "__main__":
    check()
    print("ok")
