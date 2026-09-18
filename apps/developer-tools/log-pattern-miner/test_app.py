"""Offline checks for log-pattern-miner. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # -- normalisation is regex, and must be stable across the variable parts
    a = app.normalise("2026-09-18T03:00:01.220Z ERROR [db] pool exhausted size=20 waiting=7 timeout=30s")
    b = app.normalise("2026-09-18T04:11:59.001Z ERROR [db] pool exhausted size=40 waiting=51 timeout=30s")
    assert a == b, (a, b)
    assert "<ts>" in a and "<n>" in a
    uuid_line = app.normalise("job 4f1a2b3c-1111-2222-3333-444455556666 failed")
    assert uuid_line == "job <uuid> failed", uuid_line
    assert app.normalise("connect 10.4.2.19:6379") == "connect <ip>"
    assert app.normalise("   ") == ""
    assert len(app.normalise("x" * 5000)) <= app.TEMPLATE_CHARS

    assert app.level_of("2026-01-01 WARN [x] y") == "WARN"
    assert app.level_of("no level here") == "UNKNOWN"

    # -- the MAP step collapses many lines into few templates ---------------
    lines = (HERE / "sample_logs.txt").read_text(encoding="utf-8").splitlines()
    populated = [line for line in lines if line.strip()]
    templates = app.cluster(lines)
    assert len(populated) > 500, len(populated)
    assert 5 < len(templates) < len(populated) / 10, len(templates)
    # counting is Python's: every line lands in exactly one template
    assert sum(t.count for t in templates) == len(populated)
    assert templates == sorted(templates, key=lambda t: -t.count)
    assert all(0 < len(t.examples) <= app.EXAMPLES_PER_TEMPLATE for t in templates)
    assert templates[0].top_level in {"INFO", "WARN", "ERROR", "FATAL", "UNKNOWN"}
    assert app.cluster(["", "   ", ""]) == []

    # -- three judgments per template, one request --------------------------
    questions = app.build_questions(templates)
    assert len(questions) == 3 * len(templates), len(questions)
    assert questions["mode_0"]["type"] == "choice"
    assert questions["actionable_0"]["type"] == "noul"
    assert questions["data_at_risk_0"]["type"] == "noul"

    # -- the Choice has two escape hatches, and every option is described ---
    assert "unclear" in questions["mode_0"]["criteria"]
    assert "other" in questions["mode_0"]["criteria"]
    assert all(len(text) > 15 for text in app.FAILURE_MODES.values())

    # -- REDUCE is Python arithmetic over the cluster counts ----------------
    judged = [
        {"mode": "timeout", "count": 95, "actionable": 0.9, "data_at_risk": 0.1},
        {"mode": "timeout", "count": 5, "actionable": 0.2, "data_at_risk": 0.1},
        {"mode": "auth_rejected", "count": 70, "actionable": 0.8, "data_at_risk": 0.0},
    ]
    rolled = app.reduce_modes(judged)
    assert rolled[0] == {"mode": "timeout", "lines": 100, "templates": 2, "actionable_lines": 95}, rolled[0]
    assert [row["mode"] for row in rolled] == ["timeout", "auth_rejected"]
    assert sum(row["lines"] for row in rolled) == sum(j["count"] for j in judged)

    # -- triage policy boundaries -------------------------------------------
    gates = {"actionable_gate": 0.6, "data_gate": 0.6}

    def item(**kwargs):
        return {"mode": "timeout", "actionable": 0.0, "data_at_risk": 0.0, **kwargs}

    assert app.triage(item(data_at_risk=0.6), **gates) == "page_someone"
    # data loss outranks even a routine-looking lifecycle line
    assert app.triage(item(mode="deploy_lifecycle", data_at_risk=0.9), **gates) == "page_someone"
    assert app.triage(item(mode="unclear", actionable=0.9), **gates) == "improve_the_log_line"
    assert app.triage(item(mode="other", actionable=0.9), **gates) == "improve_the_log_line"
    assert app.triage(item(mode="deploy_lifecycle", actionable=0.9), **gates) == "ignore"
    assert app.triage(item(actionable=0.6), **gates) == "investigate"
    assert app.triage(item(actionable=0.59), **gates) == "ignore"


if __name__ == "__main__":
    check()
    print("ok")
