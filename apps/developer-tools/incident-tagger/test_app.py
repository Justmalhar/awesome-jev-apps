"""Offline checks for incident-tagger. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    incidents = app.load_incidents((HERE / "sample_postmortems.json").read_text(encoding="utf-8"))
    assert len(incidents) >= 8, len(incidents)
    assert all(set(item) == {"id", "title", "text"} for item in incidents)
    assert all(item["title"] and item["text"] for item in incidents)
    assert all(len(item["text"]) <= app.TEXT_CHARS for item in incidents)
    # an entry with no id still gets one, from Python
    assert app.load_incidents('[{"title": "t", "text": "x"}]')[0]["id"] == 0

    # -- one Choice, one Score, and one Noul per independent factor ---------
    questions = app.build_questions(incidents)
    per_incident = 2 + len(app.FACTORS)
    assert len(questions) == per_incident * len(incidents), len(questions)
    assert questions["cause_0"]["type"] == "choice"
    assert questions["recurrence_0"]["type"] == "score"
    assert len(app.FACTORS) >= 6
    for name in app.FACTORS:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}

    # factors are independent properties, never options on the cause Choice
    assert not (set(app.FACTORS) & set(app.CAUSE_CLASSES))

    # -- the Choice needs somewhere to put "the write-up does not say" ------
    assert "unclear" in questions["cause_0"]["criteria"]
    assert all(len(text) > 15 for text in app.CAUSE_CLASSES.values())
    # every noul states both sides of its boundary, concretely
    for name, (task, true, false) in app.FACTORS.items():
        assert task.endswith("?"), name
        assert len(true) > 15 and len(false) > 15, name

    # -- score levels are concrete situations -------------------------------
    assert len(app.RECURRENCE_LEVELS) == 4
    assert all(len(level) > 15 for level in app.RECURRENCE_LEVELS)
    assert questions["recurrence_0"]["criteria"] == app.RECURRENCE_LEVELS

    # -- the tally is Python's, and several factors may apply at once -------
    def tagged(identifier, **factors):
        return {"id": identifier, "factors": {name: factors.get(name, 0.0) for name in app.FACTORS}}

    corpus = [
        tagged("A", detection_gap=0.9, no_safe_rollback=0.9, known_risk_accepted=0.8),
        tagged("B", detection_gap=0.7, no_safe_rollback=0.2),
        tagged("C", detection_gap=0.59),
    ]
    rows = app.tally(corpus, 0.6)
    by_name = {row["factor"]: row for row in rows}
    assert len(rows) == len(app.FACTORS)
    assert by_name["detection_gap"]["incidents"] == 2, by_name["detection_gap"]
    assert by_name["detection_gap"]["examples"] == "A, B"
    assert by_name["detection_gap"]["share"] == round(2 / 3, 3)
    assert by_name["no_safe_rollback"]["incidents"] == 1
    assert by_name["alert_noise"]["incidents"] == 0
    assert rows[0]["factor"] == "detection_gap", rows[0]
    # one incident carrying three factors is counted under all three
    assert sum(row["incidents"] for row in app.tally([corpus[0]], 0.6)) == 3
    # lowering the gate re-counts stored floats with no inference
    assert app.tally(corpus, 0.5)[0]["incidents"] == 3
    assert app.tally([], 0.6)[0]["incidents"] == 0


if __name__ == "__main__":
    check()
    print("ok")
