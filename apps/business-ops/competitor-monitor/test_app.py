"""Offline checks for competitor-monitor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    updates = json.loads((HERE / "sample_updates.json").read_text(encoding="utf-8"))
    assert len(updates) >= 10
    assert all({"competitor", "headline", "detail"} <= set(u) for u in updates)

    questions = app.build_questions(updates, app.DEFAULT_ROADMAP)
    per_update = len(app.SIGNALS) + 3
    assert len(questions) == len(updates) * per_update

    # the Choice needs a no-match option
    assert "other" in questions["type__0"]["criteria"]
    # Score levels are concrete situations, not labels
    for levels in (app.RELEVANCE_LEVELS, app.THREAT_LEVELS):
        assert all(len(level) > 15 for level in levels)
    # the roadmap must reach the model -- relevance is meaningless without it
    assert questions["relevance__0"]["instructions"]["our_roadmap"] == app.DEFAULT_ROADMAP
    for key in app.SIGNALS:
        assert questions[f"{key}__0"]["type"] == "noul"

    base = {
        "relevance": 0.2, "threat": 0.2, "shipped": 0.1, "pricing_move": 0.0,
        "our_stated_gap": 0.0, "customer_visible": 0.1, "substance": 0.9,
    }
    assert app.triage(base, 2.6, 2.0, 0.4)[0] == "noise"
    # marketing content is discarded however relevant it looks
    loud = {**base, "relevance": 3.9, "threat": 2.9, "substance": 0.05}
    assert app.triage(loud, 2.6, 2.0, 0.4)[0] == "noise"
    # shipped and on-roadmap is the only thing that earns "act"
    shipped = {**base, "relevance": 3.5, "shipped": 0.95}
    assert app.triage(shipped, 2.6, 2.0, 0.4)[0] == "act"
    # the same thing merely announced is a watch, not an act
    assert app.triage({**shipped, "shipped": 0.1}, 2.6, 2.0, 0.4)[0] == "watch"
    # off-roadmap pricing moves still surface
    assert app.triage({**base, "pricing_move": 0.9}, 2.6, 2.0, 0.4)[0] == "watch"
    # raising the bar suppresses more -- the filter is policy, not evidence
    assert app.triage(shipped, 3.9, 2.0, 0.4)[0] == "noise"

    frame = pd.DataFrame(
        [
            {"verdict": "act", "competitor": "A", "headline": "h1", "relevance": 3.5},
            {"verdict": "watch", "competitor": "A", "headline": "h2", "relevance": 2.8},
            {"verdict": "noise", "competitor": "B", "headline": "h3", "relevance": 0.1},
        ]
    )
    table = app.brief(frame)
    assert list(table.index) == ["A"], "noise must not reach the brief"
    assert table.loc["A", "items"] == 2
    assert app.brief(frame[frame["verdict"] == "noise"]).empty


if __name__ == "__main__":
    check()
    print("ok")
