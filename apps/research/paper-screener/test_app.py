"""Offline checks for paper-screener."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    papers = json.loads((HERE / "sample_abstracts.json").read_text(encoding="utf-8"))
    assert len(papers) >= 8

    protocol = app.DEFAULT_PROTOCOL
    questions = app.build_questions(papers, protocol)
    per_paper = len(protocol["include"]) + len(protocol["exclude"])
    assert len(questions) == len(papers) * per_paper

    accept, reject = 0.75, 0.25
    # The veto is the whole design: one fired exclusion beats every inclusion.
    verdict, reason = app.decide([0.99] * 4, [0.95, 0.0, 0.0], accept, reject)
    assert verdict == "exclude", f"exclusion must veto strong inclusions: {reason}"

    assert app.decide([0.9] * 4, [0.0] * 3, accept, reject)[0] == "include"
    # Borderline must reach a human rather than be forced either way.
    assert app.decide([0.9, 0.5, 0.9, 0.9], [0.0] * 3, accept, reject)[0] == "review"
    assert app.decide([0.9, 0.05, 0.9, 0.9], [0.0] * 3, accept, reject)[0] == "exclude"


if __name__ == "__main__":
    check()
    print("ok")
