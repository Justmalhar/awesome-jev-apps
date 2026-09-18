"""Offline checks for label-auditor."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SAMPLE = HERE / "sample_labelled.csv"


def check() -> None:
    import app

    assert SAMPLE.is_file(), "the dataset ships with the app"
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8")))
    assert len(rows) == 40 and {"id", "text", "label"} <= set(rows[0])
    labels = sorted({row["label"] for row in rows})
    assert labels == ["account_access", "billing", "cancellation",
                      "feature_request", "technical"]

    # ── the rubric levels are actions, and they stand alone ──
    assert len(app.LEVELS) == 3
    assert all(len(level) > 15 for level in app.LEVELS), "levels are situations, not adjectives"
    assert app.ACTIONS == ("keep", "review", "correct")

    # ── the label set comes from the data, never hardcoded ──
    options = app.label_options(labels)
    assert set(options) == set(labels) | {app.NO_FIT}
    assert options["billing"] == app.LABEL_HINTS["billing"]
    invented = app.label_options(["shipping_delay"])
    assert "shipping delay" in invented["shipping_delay"], "unknown labels still get a description"
    assert len(invented[app.NO_FIT]) > 40, "the escape hatch needs a real description"

    # ── both questions per row, one request ──
    sample = [{"id": r["id"], "text": r["text"], "label": r["label"]} for r in rows[:5]]
    questions = app.build_questions(sample, labels)
    assert len(questions) == 10, "2 questions x 5 rows, batched into one ask()"
    assert questions["verdict_0"]["type"] == "score"
    assert questions["suggest_0"]["type"] == "choice"
    assert app.NO_FIT in questions["suggest_0"]["criteria"], "a Choice needs its no-match option"
    assert questions["verdict_4"]["criteria"] == app.LEVELS

    # ── batching reads the provider window rather than a constant ──
    assert len(app.batch_rows(sample, 100_000)) == 1
    assert len(app.batch_rows(sample, 1)) == 5
    assert sum(len(group) for group in app.batch_rows(sample, 40)) == 5

    # ── the policy, at every boundary ──
    act = lambda v, assigned, suggested, conf: app.decide(
        v, assigned, suggested, conf, correct_at=1.5, conf_gate=0.6)
    assert act(0.49, "billing", "billing", 0.9) == "keep"
    assert act(0.50, "billing", "technical", 0.9) == "review", "ambiguous band starts here"
    assert act(1.49, "billing", "technical", 0.9) == "review"
    assert act(1.50, "billing", "technical", 0.9) == "correct"
    assert act(1.90, "billing", "technical", 0.59) == "review", "a guessed replacement is not a correction"
    assert act(1.90, "billing", "billing", 0.99) == "review", "nothing to correct it to"
    assert act(1.95, "billing", app.NO_FIT, 0.99) == "review", "a taxonomy hole is a human's call"
    # thresholds are policy: same answers, different action
    assert app.decide(1.2, "billing", "technical", 0.9, correct_at=1.0, conf_gate=0.6) == "correct"
    assert app.decide(1.9, "billing", "technical", 0.9, correct_at=2.0, conf_gate=0.6) == "review"


if __name__ == "__main__":
    check()
    print("ok")
