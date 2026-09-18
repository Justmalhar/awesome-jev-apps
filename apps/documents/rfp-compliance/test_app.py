"""Offline checks for rfp-compliance. No key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    # Each app ships its own module named `app`; the shared runner imports them
    # into one process, so drop any predecessor before resolving ours.
    sys.modules.pop("app", None)
    sys.modules.pop("jev_provider", None)
    import app

    requirements = json.loads(app.SAMPLE_REQUIREMENTS.read_text(encoding="utf-8"))
    assert len(requirements) >= 10, "sample tender should be big enough to be interesting"
    assert any(r["mandatory"] for r in requirements) and any(
        not r["mandatory"] for r in requirements
    ), "sample must exercise both the mandatory and the optional path"
    assert app.SAMPLE_PROPOSAL.read_text(encoding="utf-8").strip(), "bundled proposal is empty"

    questions = build = app.build_questions(requirements)
    assert len(questions) == len(requirements) * app.QUESTIONS_PER_REQUIREMENT

    # Two independent Nouls per requirement -- not one Choice, not a Score.
    assert all(q["type"] == "noul" for q in build.values())
    for index in range(len(requirements)):
        for prefix in ("addressed", "committed"):
            question = questions[f"{prefix}_{index}"]
            assert set(question["criteria"]) == {"true", "false"}
            assert question["instructions"]["requirement"] == f"`requirements[{index}].text`"
            assert question["instructions"]["proposal"] == "`proposal`"

    # No threshold may be baked into an instruction -- policy lives in Python.
    serialised = json.dumps(questions).lower()
    for leaked in ("0.75", "0.25", "threshold"):
        assert leaked not in serialised, f"{leaked!r} leaked into a question"

    accept, reject = 0.75, 0.25
    assert app.classify(0.95, 0.95, accept, reject)[0] == "covered"
    assert app.classify(0.05, 0.99, accept, reject)[0] == "gap", "unanswered outranks committed"
    assert app.classify(0.95, 0.05, accept, reject)[0] == "weak", "capability is not commitment"
    assert app.classify(0.60, 0.60, accept, reject)[0] == "weak", "borderline is never covered"
    # Boundaries are inclusive on both sides.
    assert app.classify(0.25, 0.9, accept, reject)[0] == "gap"
    assert app.classify(0.75, 0.75, accept, reject)[0] == "covered"

    covered = {"id": "R-01", "mandatory": True, "status": "covered"}
    optional_gap = {"id": "R-08", "mandatory": False, "status": "gap"}
    mandatory_weak = {"id": "R-02", "mandatory": True, "status": "weak"}

    assert app.bid_verdict([covered], False) == (True, None)
    assert app.bid_verdict([covered, optional_gap], False)[0] is True, "optional gaps do not veto"
    assert app.bid_verdict([covered, optional_gap], True)[0] is False, "unless the buyer says so"

    # One unmet mandatory requirement vetoes any amount of good coverage.
    ok, blocker = app.bid_verdict([covered] * 12 + [mandatory_weak], False)
    assert ok is False and blocker["id"] == "R-02"

    # Worst status is named first, and mandatory beats optional at equal status.
    _, blocker = app.bid_verdict([mandatory_weak, optional_gap], True)
    assert blocker["id"] == "R-08", "a gap is worse than a weak answer"
    _, blocker = app.bid_verdict(
        [{"id": "R-99", "mandatory": False, "status": "gap"}, optional_gap | {"mandatory": True}],
        True,
    )
    assert blocker["mandatory"] is True


if __name__ == "__main__":
    check()
    print("ok")
