"""Offline checks for age-appropriateness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    bands = app.parse_lines(app.SAMPLE_BANDS.read_text(encoding="utf-8"))
    assert len(bands) == 5, bands
    # the rubric IS the operator's text, so the concreteness check runs on it
    assert all(len(band) > 15 for band in bands), bands
    assert app.rubric_problems(bands) == []
    assert app.rubric_problems(["PG", "18"]), "labels must be rejected as a rubric"
    assert app.rubric_problems(["A long enough single band description here"]), "one band is not a rubric"

    concerns = app.parse_concerns(app.SAMPLE_CONCERNS.read_text(encoding="utf-8"))
    assert [c["id"] for c in concerns] == [f"C{n}" for n in range(1, 7)], concerns
    assert app.parse_concerns("Note: anything scary")[0]["text"] == "Note: anything scary"

    items = json.loads(app.SAMPLE.read_text(encoding="utf-8"))
    assert len(items) >= 8
    assert all({"title", "kind", "summary", "transcript_excerpt"} <= set(i) for i in items)

    questions = app.build_questions(items, bands, concerns)
    # band score + one Noul per concern + borderline + driver choice
    assert len(questions) == len(items) * (len(concerns) + 3), len(questions)
    assert questions["band_0"]["type"] == "score"
    assert questions["band_0"]["criteria"] == bands, "the operator's bands are the levels"
    assert questions["concern_0_0"]["type"] == "noul"
    assert questions["borderline_0"]["type"] == "noul"

    assert "nothing_notable" in questions["driver_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.DRIVER_KINDS.values())

    # rounding is Python's job, and it rounds toward the older audience
    margin = app.DEFAULT_GATES["margin"]
    assert app.assign_band(0.0, bands, margin) == 0
    assert app.assign_band(1.2, bands, margin) == 1
    assert app.assign_band(1.25, bands, margin) == 2, "at the margin, round to the older band"
    assert app.assign_band(1.9, bands, margin) == 2
    assert app.assign_band(4.9, bands, margin) == 4, "never past the last band"
    assert app.assign_band(9.0, bands, margin) == 4
    assert app.assign_band(1.5, bands, 1.0) == 1, "a margin of 1.0 disables rounding up"

    gates = dict(app.DEFAULT_GATES)
    quiet = {c["id"]: 0.05 for c in concerns}
    assert app.decide(0, 0.9, 0.05, quiet, gates) == "publish"
    assert app.decide(0, 0.2, 0.05, quiet, gates) == "human_review", "low confidence never publishes"
    assert app.decide(3, 0.9, 0.9, quiet, gates) == "human_review", "borderline never publishes"
    assert app.decide(0, 0.9, 0.05, {**quiet, "C1": 0.9}, gates) == "human_review", (
        "a named concern inside a young band always needs a person"
    )
    assert app.decide(4, 0.9, 0.05, {**quiet, "C1": 0.9}, gates) == "publish", (
        "the same concern in an adult band is expected, not an exception"
    )
    # boundaries
    assert app.decide(0, gates["confidence"], 0.0, quiet, gates) == "publish"
    assert app.decide(3, 0.9, gates["borderline"], quiet, gates) == "human_review"
    assert app.decide(2, 0.9, 0.0, {**quiet, "C1": gates["concern"]}, gates) == "publish"
    assert app.decide(1, 0.9, 0.0, {**quiet, "C1": gates["concern"]}, gates) == "human_review"


if __name__ == "__main__":
    check()
    print("ok")
