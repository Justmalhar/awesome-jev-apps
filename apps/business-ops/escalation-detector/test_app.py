"""Offline checks for escalation-detector."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    threads = json.loads((HERE / "sample_threads.json").read_text(encoding="utf-8"))
    assert len(threads) >= 8
    assert all({"id", "account", "messages"} <= set(t) for t in threads)

    questions = app.build_questions(threads)
    per_thread = len(app.TRIGGERS) + len(app.DIMENSIONS)
    assert len(questions) == len(threads) * per_thread

    for key in app.TRIGGERS:
        assert questions[f"{key}__0"]["type"] == "noul"
    for key, spec in app.DIMENSIONS.items():
        assert questions[f"{key}__0"]["type"] == "score"
        assert all(len(level) > 15 for level in spec["levels"]), key

    weights = {key: 1.0 for key in app.DIMENSIONS}
    calm = {**{k: 0.0 for k in app.TRIGGERS}, **{k: 0.0 for k in app.DIMENSIONS}}
    hot = {
        **{k: 0.0 for k in app.TRIGGERS},
        **{k: len(spec["levels"]) - 1 for k, spec in app.DIMENSIONS.items()},
    }
    assert app.composite(calm, weights) == 0.0
    assert abs(app.composite(hot, weights) - 100.0) < 1e-6

    assert app.decide(calm, weights, 0.6, 60, 30)[0] == "leave_to_agent"
    assert app.decide(hot, weights, 0.6, 60, 30)[0] == "escalate"

    # the abstention band is the product: a middling thread must not be forced
    middling = {**{k: 0.0 for k in app.TRIGGERS}, **{k: 1.5 for k in app.DIMENSIONS}}
    assert app.decide(middling, weights, 0.6, 60, 30)[0] == "look_at_it"
    # ... and collapsing the band removes it
    assert app.decide(middling, weights, 0.6, 40, 40)[0] == "escalate"

    # a hard trigger must survive four calm dimensions -- vetoes are not averaged
    legal = {**calm, "legal_threat": 0.91}
    verdict, total, reason = app.decide(legal, weights, 0.6, 60, 30)
    assert verdict == "escalate" and total == 0.0 and "legal_threat" in reason
    # ... and must not fire below its own threshold
    assert app.decide(legal, weights, 0.95, 60, 30)[0] == "leave_to_agent"

    rows = [
        {"composite": 5.0, "verdict": "leave_to_agent"},
        {"composite": 45.0, "verdict": "look_at_it"},
        {"composite": 95.0, "verdict": "escalate"},
    ]
    table = app.calibration_table(rows)
    assert int(table["threads"].sum()) == 3
    assert int(table["abstained"].sum()) == 1


if __name__ == "__main__":
    check()
    print("ok")
