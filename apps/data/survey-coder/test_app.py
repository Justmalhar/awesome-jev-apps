"""Offline checks for survey-coder."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    codes = app.load_codebook()
    assert len(codes) == 10, "the bundled codebook is ten codes"
    assert all({"id", "label", "description"} <= set(code) for code in codes)
    # The description carries the boundary cases; a bare label is the documented trap.
    assert all(len(code["description"]) > 40 for code in codes)
    assert len({code["id"] for code in codes}) == len(codes), "code ids must be unique"

    with (HERE / "sample_responses.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 40 and "response" in rows[0], "40 open-ends ship with the app"

    # One Noul per (answer, code) -- never a Choice, never levels on a Score.
    questions = app.build_questions(["too expensive and I never used it", "idk"], codes)
    assert len(questions) == 2 * len(codes) == 20
    assert all(question["type"] == "noul" for question in questions.values())
    assert "r0__price" in questions and "r1__temporary" in questions
    # Both codes of a genuinely two-code answer are asked independently.
    assert questions["r0__price"]["criteria"] != questions["r0__low_usage"]["criteria"]

    # Batching reads the provider's window; a smaller window means more requests.
    responses = [row["response"] for row in rows]
    wide = app.batch_responses(responses, codes, 64000)
    narrow = app.batch_responses(responses, codes, 32000)
    assert sorted(i for batch in wide for i in batch) == list(range(len(responses)))
    assert sorted(i for batch in narrow for i in batch) == list(range(len(responses)))
    assert len(narrow) >= len(wide) and len(wide) >= 1
    assert app.batch_responses([], codes, 64000) == []

    # Thresholding boundaries. Everything here runs on a stored probability.
    assert app.decide(0.95, 0.5, 0.1) == "applied"
    assert app.decide(0.60, 0.5, 0.1) == "applied", "exactly at apply_at + band is applied"
    assert app.decide(0.59, 0.5, 0.1) == "review"
    assert app.decide(0.50, 0.5, 0.1) == "review"
    assert app.decide(0.41, 0.5, 0.1) == "review"
    assert app.decide(0.40, 0.5, 0.1) == "not_applied", "exactly at apply_at - band is out"
    assert app.decide(0.02, 0.5, 0.1) == "not_applied"
    # A zero band removes the queue entirely: every code is decided.
    assert {app.decide(p, 0.5, 0.0) for p in (0.0, 0.49, 0.51, 1.0)} == {"applied", "not_applied"}
    # Moving the threshold re-labels without touching the probability.
    assert app.decide(0.30, 0.2, 0.05) == "applied" and app.decide(0.30, 0.8, 0.05) == "not_applied"


if __name__ == "__main__":
    check()
    print("ok")
