"""Offline checks for vendor-rfp-scorer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    responses = json.loads((HERE / "sample_responses.json").read_text(encoding="utf-8"))
    assert len(responses) >= 4
    criteria = app.DEFAULT_CRITERIA
    for response in responses:
        assert set(criteria) <= set(response["sections"]), response["vendor"]

    questions = app.build_questions(responses, criteria)
    gated = [k for k, spec in criteria.items() if spec.get("mandatory")]
    per_response = len(criteria) + len(gated)
    assert len(questions) == len(responses) * per_response
    assert questions["q_security__0"]["type"] == "score"
    assert questions["m_security__0"]["type"] == "noul"
    # a criterion with no gate must not produce a Noul
    assert "m_commercial__0" not in questions
    # Score levels must be concrete situations
    assert all(len(level) > 15 for level in app.QUALITY_LEVELS)
    assert len(app.QUALITY_LEVELS) == 5

    weights = {key: 1.0 for key in criteria}
    top = len(app.QUALITY_LEVELS) - 1
    best = {key: float(top) for key in criteria}
    worst = {key: 0.0 for key in criteria}
    assert abs(app.weighted_score(best, weights) - 100.0) < 1e-6
    assert app.weighted_score(worst, weights) == 0.0
    # a zero weight removes a criterion from the model entirely
    one_only = {**{k: 0.0 for k in criteria}, "commercial": 1.0}
    mixed = {**worst, "commercial": float(top)}
    assert abs(app.weighted_score(mixed, one_only) - 100.0) < 1e-6

    passing = {**best, **{f"m_{k}": 0.95 for k in gated}}
    failing = {**best, **{f"m_{k}": 0.95 for k in gated}, f"m_{gated[0]}": 0.10}
    assert app.gate_failures(passing, criteria, 0.70) == []
    assert app.gate_failures(failing, criteria, 0.70) == [gated[0]]
    # lowering the gate lets it through -- the gate is policy, not evidence
    assert app.gate_failures(failing, criteria, 0.05) == []

    # a perfect score must NOT outrank a compliant vendor if it fails a gate
    frame = app.rank(
        [{"vendor": "fails-gate"}, {"vendor": "compliant"}],
        [failing, {**worst, **{f"m_{k}": 0.95 for k in gated}}],
        criteria,
        weights,
        0.70,
    )
    assert list(frame["vendor"]) == ["compliant", "fails-gate"]
    assert frame.iloc[0]["score"] < frame.iloc[1]["score"]


if __name__ == "__main__":
    check()
    print("ok")
