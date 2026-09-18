"""Offline checks for data-extractor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    records = json.loads((HERE / "sample_methods.json").read_text(encoding="utf-8"))
    assert records, "sample data must not be empty"

    # Candidates are parsed in Python, so every selectable value is verbatim.
    spans = app.split_candidates(records[0]["methods"])
    assert len(spans) > 4, spans
    for span in spans:
        assert span in records[0]["methods"], f"candidate is not verbatim: {span!r}"
    assert len(app.split_candidates("a. " * 100, max_candidates=7)) <= 7

    candidates = [app.split_candidates(r["methods"]) for r in records]
    questions = app.build_questions(records, candidates)
    assert len(questions) == len(records) * len(app.FIELDS) * 2, len(questions)

    for qid, question in questions.items():
        if question["type"] == "choice":
            assert app.NO_MATCH in question["criteria"], f"{qid} has no escape hatch"
            assert len(question["criteria"]) >= 3, qid
        else:
            assert set(question["criteria"]) == {"true", "false"}, qid

    # Policy boundaries.
    assert app.resolve(0.10, "s2", 0.9, 0.30, 0.60)[0] == "not_reported"
    assert app.resolve(0.30, "s2", 0.9, 0.30, 0.60)[0] == "not_reported", "threshold is inclusive"
    assert app.resolve(0.95, app.NO_MATCH, 0.9, 0.30, 0.60)[0] == "review"
    assert app.resolve(0.95, "s2", 0.45, 0.30, 0.60)[0] == "review"
    assert app.resolve(0.95, "s2", 0.60, 0.30, 0.60)[0] == "extracted"


if __name__ == "__main__":
    check()
    print("ok")
