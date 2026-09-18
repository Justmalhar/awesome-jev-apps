"""Offline checks for agent-trace-classifier. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    traces = app.load_traces(None)
    assert len(traces) >= 12
    assert {"id", "goal", "steps", "final_state"} <= set(traces[0])

    questions = app.build_map_questions(traces)
    assert len(questions) == len(traces) * (1 + len(app.FLAGS))

    # A Choice must be able to decline. Without this it nominates a cause for
    # a trace that shows nothing at all.
    assert questions["cause_0"]["type"] == "choice"
    assert "unclear" in questions["cause_0"]["criteria"]
    assert len(app.CAUSES["unclear"]) > 15
    # Independent properties stay separate Nouls.
    for name in app.FLAGS:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name

    rows = [
        {"id": "a", "cause": "retry_loop", "confidence": 0.9},
        {"id": "b", "cause": "retry_loop", "confidence": 0.5},
        {"id": "c", "cause": "retry_loop", "confidence": 0.7},
        {"id": "d", "cause": "retry_loop", "confidence": 0.6},
        {"id": "e", "cause": "unclear", "confidence": 0.3},
    ]
    clusters = app.cluster(rows)
    assert [c["cause"] for c in clusters] == ["retry_loop", "unclear"], "largest cluster first"
    assert clusters[0]["size"] == 4 and abs(clusters[0]["share"] - 0.8) < 1e-9
    assert abs(clusters[0]["mean_confidence"] - 0.675) < 1e-9
    # Exemplars are the most confident members, capped, and ordered.
    assert clusters[0]["exemplars"] == ["a", "c", "d"]
    assert clusters[1]["members"] == ["e"]

    reduce_questions = app.build_reduce_questions(clusters[:1])
    assert len(reduce_questions) == 1
    assert reduce_questions["remedy_0"]["type"] == "choice"
    assert "none" in reduce_questions["remedy_0"]["criteria"]
    assert all(len(text) > 15 for text in app.REMEDIES.values())

    assert app.decide_alert(clusters, alert_share=0.25) is True
    assert app.decide_alert(clusters, alert_share=0.9) is False
    # An "unclear" pile is a measurement problem, not a failure cause to page on.
    assert app.decide_alert(app.cluster([{"id": "x", "cause": "unclear", "confidence": 0.2}]),
                            alert_share=0.25) is False
    # boundary: exactly at the share triggers
    assert app.decide_alert([{"cause": "retry_loop", "share": 0.25}], alert_share=0.25) is True

    assert len(app.batch(traces, 10**6)) == 1
    assert sum(len(g) for g in app.batch(traces, 50)) == len(traces)


if __name__ == "__main__":
    check()
    print("ok")
