"""Offline checks for onboarding-gaps."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    steps = json.loads((HERE / "sample_steps.json").read_text(encoding="utf-8"))
    docs = json.loads((HERE / "sample_docs.json").read_text(encoding="utf-8"))
    assert len(steps) >= 8 and len(docs) >= 4
    assert all("task" in step for step in steps)
    assert all({"title", "body"} <= set(doc) for doc in docs)

    pairs = app.all_pairs(steps, docs)
    assert len(pairs) == len(steps) * len(docs)
    questions = app.build_questions(steps, docs, pairs)
    assert len(questions) == len(pairs) * 2
    assert questions["act_0_0"]["type"] == "noul"
    assert questions["men_0_0"]["type"] == "noul"
    # "explains it" and "mentions it" are separate properties, never one rubric
    assert questions["act_0_0"]["criteria"] != questions["men_0_0"]["criteria"]

    # chunking follows the provider's context window
    assert len(app.batch_pairs(pairs, docs, 500000)) == 1
    small = app.batch_pairs(pairs, docs, 4000)
    assert len(small) > 1
    assert [p for chunk in small for p in chunk] == pairs

    # coverage takes a max over documents -- in Python, never in a question
    judgments = {
        pair: {"actionable": 0.1, "mentioned": 0.1} for pair in pairs
    }
    judgments[(0, 2)] = {"actionable": 0.95, "mentioned": 0.99}
    frame = app.coverage(steps, docs, judgments)
    assert len(frame) == len(steps)
    assert frame.iloc[0]["best explanation"] == 0.95
    assert frame.iloc[0]["best doc"] == docs[2]["title"]
    assert frame.iloc[1]["best explanation"] == 0.1

    assert app.classify(0.9, 0.9, 0.7, 0.2) == "covered"
    # the interesting case: present in the docs, useless to a new joiner
    assert app.classify(0.2, 0.9, 0.7, 0.2) == "mentioned but not explained"
    assert app.classify(0.05, 0.05, 0.7, 0.2) == "missing entirely"
    assert app.classify(0.3, 0.4, 0.7, 0.2) == "thin"


if __name__ == "__main__":
    check()
    print("ok")
