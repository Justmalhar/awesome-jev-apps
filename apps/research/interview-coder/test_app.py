"""Offline checks for interview-coder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    transcript = (HERE / "sample_transcript.txt").read_text(encoding="utf-8")
    codebook = json.loads((HERE / "sample_codebook.json").read_text(encoding="utf-8"))
    assert len(codebook) >= 5

    passages = app.split_passages(transcript)
    assert len(passages) > 8, len(passages)
    speakers = {p["speaker"] for p in passages}
    assert "Interviewer" in speakers and "P07" in speakers, speakers
    # Continuation lines fold into the previous turn rather than becoming passages.
    folded = app.split_passages("P1: first line\ntrailing continuation of that same turn")
    assert len(folded) == 1 and folded[0]["text"].endswith("turn"), folded

    participant = [p for p in passages if p["speaker"] != "Interviewer"]
    indices = list(range(len(participant)))
    questions = app.build_questions(participant, indices, codebook)
    assert len(questions) == len(participant) * len(codebook), len(questions)
    for qid, question in questions.items():
        assert question["type"] == "noul", qid  # codes co-occur: never a Choice
        assert set(question["criteria"]) == {"true", "false"}, qid

    # Batching is greedy over a token budget and never drops an item.
    assert app.plan_batches([10, 10, 10], 25) == [[0, 1], [2]]
    assert app.plan_batches([500], 100) == [[0]], "an oversized item still gets sent"
    assert app.plan_batches([], 100) == []
    flat = [i for b in app.plan_batches([7] * 11, 20) for i in b]
    assert flat == list(range(11)), flat

    # Two bands, both open on the right side of the code set.
    applied, borderline = app.assign_codes(
        {"a": 0.91, "b": 0.65, "c": 0.5, "d": 0.34}, apply_at=0.65, review_at=0.35
    )
    assert applied == ["a", "b"], applied
    assert borderline == ["c"], borderline
    assert app.assign_codes({"a": 0.1}, 0.65, 0.35) == ([], [])


if __name__ == "__main__":
    check()
    print("ok")
