"""Offline checks for method-gap-finder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    manuscripts = json.loads((HERE / "sample_manuscripts.json").read_text(encoding="utf-8"))
    assert manuscripts

    # The four named gaps are all critical by default.
    assert {"control", "sample_rationale", "ethics", "preregistration"} <= set(app.CRITICAL)

    questions = app.build_questions(manuscripts)
    assert len(questions) == len(manuscripts) * len(app.CHECKLIST), len(questions)
    for qid, question in questions.items():
        assert question["type"] == "noul", f"{qid}: independent conditions are Nouls"
        assert set(question["criteria"]) == {"true", "false"}, qid
        assert len(question["criteria"]["true"]) > 15 and len(question["criteria"]["false"]) > 15

    # Every checklist key is unique, or results silently overwrite each other.
    keys = [item["key"] for item in app.CHECKLIST]
    assert len(keys) == len(set(keys)), keys

    # Three-band classification, boundaries inclusive on both ends.
    assert app.classify(0.75, 0.75, 0.25) == "present"
    assert app.classify(0.25, 0.75, 0.25) == "absent"
    assert app.classify(0.5, 0.75, 0.25) == "unclear"

    # Veto logic: one absent critical item beats every present one.
    every_present = {key: "present" for key in keys}
    assert app.verdict(every_present) == ("ready", [])

    one_missing = dict(every_present, control="absent")
    result, drivers = app.verdict(one_missing)
    assert result == "blocking" and drivers == ["control"], (result, drivers)

    ambiguous = dict(every_present, ethics="unclear")
    assert app.verdict(ambiguous) == ("needs_review", ["ethics"])

    # An advisory gap never blocks.
    advisory_gap = dict(every_present, blinding="absent", data_availability="absent")
    assert app.verdict(advisory_gap) == ("ready", [])

    # Severity is policy: reclassifying an item re-runs no inference.
    assert app.verdict(advisory_gap, critical=["blinding"])[0] == "blocking"


if __name__ == "__main__":
    check()
    print("ok")
