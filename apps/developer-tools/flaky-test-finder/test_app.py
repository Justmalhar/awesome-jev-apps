"""Offline checks for flaky-test-finder. No key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # -- rates are computed in Python, exactly, from the run records --------
    runs = json.loads((HERE / "sample_runs.json").read_text(encoding="utf-8"))
    histories = app.summarise(runs)
    assert histories, "sample must contain failing tests"
    by_name = {h.test: h for h in histories}

    broken = by_name["tests/auth/test_session.py::test_expires_after_ttl"]
    assert broken.runs == broken.failures == 18, (broken.runs, broken.failures)
    assert broken.failure_rate == 1.0

    rare = by_name["tests/cli/test_help.py::test_prints_usage"]
    assert rare.runs == 200 and rare.failures == 1
    assert abs(rare.failure_rate - 0.005) < 1e-9

    # passing tests with no failures are dropped, and results are rate-ordered
    assert all(h.failures for h in histories)
    assert histories == sorted(histories, key=lambda h: -h.failure_rate)

    # duplicate messages are deduped and capped in Python, not by the model
    flake = by_name["tests/ui/test_dashboard.py::test_chart_renders"]
    assert len(flake.messages) == len(set(flake.messages)) <= app.MAX_MESSAGES
    assert all(len(m) <= app.MESSAGE_CHARS for h in histories for m in h.messages)
    assert app.summarise([{"test": "", "status": "fail"}]) == []

    # -- three independent judgments per test, one request ------------------
    questions = app.build_questions(histories)
    assert len(questions) == 3 * len(histories), len(questions)
    assert questions["mechanism_0"]["type"] == "choice"
    assert questions["varied_0"]["type"] == "noul"
    assert questions["self_inflicted_0"]["type"] == "noul"

    # -- a Choice must have somewhere to put "I cannot tell" ----------------
    assert "unclear" in questions["mechanism_0"]["criteria"]
    assert "deterministic_failure" in app.MECHANISMS
    assert not (app.FLAKE_MECHANISMS & {"unclear", "deterministic_failure"})
    assert app.FLAKE_MECHANISMS <= set(app.MECHANISMS)
    assert all(len(text) > 15 for text in app.MECHANISMS.values())

    # -- policy boundaries, all pure Python ---------------------------------
    gates = {"quarantine_floor": 0.05, "broken_ceiling": 0.85, "test_fault_gate": 0.6}
    always = app.TestHistory(test="t", runs=10, failures=10)
    sometimes = app.TestHistory(test="t", runs=100, failures=20)
    barely = app.TestHistory(test="t", runs=1000, failures=1)

    assert app.classify(always, "timing_race", 0.9, **gates) == "broken"
    assert app.classify(barely, "timing_race", 0.9, **gates) == "watch"
    assert app.classify(sometimes, "timing_race", 0.9, **gates) == "quarantine"
    # same test, same rate, same mechanism -- only the fault judgment moves it
    assert app.classify(sometimes, "timing_race", 0.2, **gates) == "intermittent_bug"
    assert app.classify(sometimes, "timing_race", 0.6, **gates) == "quarantine"
    assert app.classify(sometimes, "unclear", 0.9, **gates) == "needs_human"
    assert app.classify(sometimes, "deterministic_failure", 0.9, **gates) == "broken"


if __name__ == "__main__":
    check()
    print("ok")
