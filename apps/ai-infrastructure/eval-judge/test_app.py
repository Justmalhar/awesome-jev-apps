"""Offline checks for eval-judge. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    data = app.load_data(None)
    assert {"task", "brief", "rubric", "outputs"} <= set(data)
    outputs = data["outputs"]
    assert len(outputs) >= 8
    assert {"id", "label", "text"} <= set(outputs[0])

    questions = app.build_questions(outputs)
    assert len(questions) == len(outputs) * (len(app.DIMENSIONS) + len(app.GATES))

    # Independent rubric dimensions are separate Scores, never one blended rubric.
    seen_levels = []
    for name, spec in app.DIMENSIONS.items():
        question = questions[f"{name}_0"]
        assert question["type"] == "score", name
        assert question["criteria"] == spec["levels"], name
        assert all(len(level) > 15 for level in spec["levels"]), name
        assert len(set(spec["levels"])) == len(spec["levels"]), name
        seen_levels.append(tuple(spec["levels"]))
    assert len(set(seen_levels)) == len(seen_levels), "each dimension needs its own levels"

    # Hard gates are Nouls: disqualifying conditions, not degrees of quality.
    for name in app.GATES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name

    even = dict.fromkeys(app.DIMENSIONS, 1.0)
    dims = {"instruction_following": 3.0, "factual_correctness": 3.0,
            "completeness": 0.0, "directness": 0.0}
    assert abs(app.aggregate(dims, even) - 1.5) < 1e-9
    # Reweighting is pure Python: no new inference to favour one dimension.
    heavy = {**even, "factual_correctness": 3.0}
    assert app.aggregate(dims, heavy) > app.aggregate(dims, even)
    try:
        app.aggregate(dims, dict.fromkeys(app.DIMENSIONS, 0.0))
    except ValueError:
        pass
    else:
        raise AssertionError("zero weights must be rejected, not divided by")

    clear = dict.fromkeys(app.GATES, 0.0)
    gates = {"pass_score": 2.0, "gate_p": 0.6}
    assert app.grade(2.5, clear, **gates) == "pass"
    assert app.grade(2.0, clear, **gates) == "pass"          # boundary
    assert app.grade(1.99, clear, **gates) == "fail"
    # A tripped gate disqualifies at any score, including a perfect one.
    assert app.grade(3.0, {**clear, "refused": 0.6}, **gates) == "gated"
    assert app.grade(3.0, {**clear, "format_violation": 0.95}, **gates) == "gated"
    assert app.grade(3.0, {**clear, "refused": 0.59}, **gates) == "pass"

    assert len(app.batch(outputs, 10**6)) == 1
    assert sum(len(g) for g in app.batch(outputs, 40)) == len(outputs)


if __name__ == "__main__":
    check()
    print("ok")
