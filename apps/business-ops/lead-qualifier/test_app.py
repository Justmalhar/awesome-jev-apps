"""Offline checks for lead-qualifier."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    leads = json.loads((HERE / "sample_leads.json").read_text(encoding="utf-8"))
    assert len(leads) >= 8, "sample must be big enough to rank"
    assert all({"name", "company", "role", "message"} <= set(lead) for lead in leads)

    questions = app.build_questions(leads, "an ICP")
    per_lead = len(app.DIMENSIONS) + len(app.DISQUALIFIERS) + 1
    assert len(questions) == len(leads) * per_lead, len(questions)

    # a Choice must have an escape hatch
    assert "unclear" in questions["ask__0"]["criteria"]
    # Score levels must be concrete situations, not labels
    for spec in app.DIMENSIONS.values():
        assert all(len(level) > 15 for level in spec["levels"]), spec["label"]
    # disqualifiers are independent Nouls, never levels on a rubric
    for key in app.DISQUALIFIERS:
        assert questions[f"{key}__0"]["type"] == "noul"

    weights = {key: 1.0 for key in app.DIMENSIONS}
    best = {key: len(spec["levels"]) - 1 for key, spec in app.DIMENSIONS.items()}
    worst = {key: 0.0 for key in app.DIMENSIONS}
    assert abs(app.composite(best, weights) - 100.0) < 1e-6
    assert app.composite(worst, weights) == 0.0

    clean = {**best, **{k: 0.0 for k in app.DISQUALIFIERS}}
    dirty = {**best, **{k: 0.0 for k in app.DISQUALIFIERS}, "competitor": 0.9}
    assert app.decide(100.0, clean, 0.7, 75, 50)[0] == "hot"
    assert app.decide(60.0, clean, 0.7, 75, 50)[0] == "warm"
    assert app.decide(10.0, clean, 0.7, 75, 50)[0] == "nurture"
    # a veto must survive a perfect composite
    assert app.decide(100.0, dirty, 0.7, 75, 50)[0] == "disqualified"
    # and must not fire below the threshold
    assert app.decide(100.0, dirty, 0.95, 75, 50)[0] == "hot"


if __name__ == "__main__":
    check()
    print("ok")
