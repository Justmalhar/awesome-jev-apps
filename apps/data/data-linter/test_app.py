"""Offline checks for data-linter. No key, no network."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

COLUMNS = ["record_id", "job_title", "seniority", "department", "city",
           "country", "currency", "account_status", "description"]


def check() -> None:
    import app

    # ── Rules are data, and every one of them must be a usable Noul. ──
    assert len(app.RULES) >= 5, "not enough cross-field rules to be worth running"
    assert len({rule["id"] for rule in app.RULES}) == len(app.RULES), "rule ids must be unique"
    for rule in app.RULES:
        assert len(rule["fields"]) >= 2, f"{rule['id']} is not a CROSS-field rule"
        assert len(rule["true"]) > 40 and len(rule["false"]) > 40, \
            f"{rule['id']} criteria must spell out the boundary, not restate the label"
        assert "Example:" in rule["true"], f"{rule['id']} must give a concrete positive example"

    # ── A rule whose columns are absent must never be asked. ──
    assert len(app.applicable_rules(COLUMNS, [r["id"] for r in app.RULES])) == len(app.RULES)
    assert app.applicable_rules(["city"], ["geo"]) == [], "geo needs country too"
    assert [r["id"] for r in app.applicable_rules(COLUMNS, ["geo"])] == ["geo"], "disabled rules stay off"
    assert app.applicable_rules(COLUMNS, []) == []

    # ── The Choice must have escape hatches; without one it blames a field anyway. ──
    options = app.culprit_options(["city", "country"])
    assert "no_contradiction" in options and "unclear" in options
    assert len(options) == 4
    assert all(len(text) > 30 for text in options.values()), "options need real descriptions"

    # ── One Noul per (row, rule) plus one Choice per row, in a single ask(). ──
    rows = [dict.fromkeys(COLUMNS, "x") for _ in range(3)]
    questions = app.build_questions(rows, app.RULES, COLUMNS)
    assert len(questions) == 3 * (len(app.RULES) + 1), len(questions)
    assert questions["geo_0"]["type"] == "noul"
    assert questions["culprit_2"]["type"] == "choice"
    assert questions["geo_1"]["instructions"]["city"] == "`rows[1].city`", "state by path, not inlined"
    assert app.build_questions([], app.RULES, COLUMNS) == {}

    # ── Batching follows the provider, never a hardcoded context size. ──
    many = [dict.fromkeys(COLUMNS, "v" * 200) for _ in range(40)]
    small = app.batch_rows(many, app.RULES, 8_000)
    large = app.batch_rows(many, app.RULES, 64_000)
    assert sum(len(batch) for batch in small) == 40, "no row may be dropped by batching"
    assert sum(len(batch) for batch in large) == 40
    assert len(small) > len(large), "a smaller context must produce more requests"
    assert all(batch for batch in small), "no empty batches"
    # Even an absurdly small context still makes progress, one row at a time.
    assert all(len(batch) == 1 for batch in app.batch_rows(many, app.RULES, 1))
    assert app.batch_rows([], app.RULES, 64_000) == []

    # ── Triage is pure policy and must be exact at its boundaries. ──
    assert app.triage(0.65, 0.65, 0.2) == "contradiction", "the threshold is inclusive"
    assert app.triage(0.64, 0.65, 0.2) == "review"
    assert app.triage(0.45, 0.65, 0.2) == "review", "band floor is inclusive"
    assert app.triage(0.44, 0.65, 0.2) == "clean"
    assert app.triage(0.50, 0.65, 0.0) == "clean", "a zero-width band leaves no review tier"

    # ── The sample must actually contain the contradictions the README promises. ──
    sample = HERE / "sample_records.csv"
    assert sample.is_file(), "the app must run cold"
    with sample.open(encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    assert len(records) >= 30, len(records)
    assert set(records[0]) >= {field for rule in app.RULES for field in rule["fields"]}, \
        "the sample must exercise every rule"
    by_id = {record["record_id"]: record for record in records}
    assert by_id["R-004"]["city"] == "Lisbon" and by_id["R-004"]["country"] == "Spain"
    assert by_id["R-014"]["job_title"] == "Intern" and by_id["R-014"]["seniority"] == "C-level"
    assert by_id["R-008"]["currency"] == "USD" and by_id["R-008"]["country"] == "Italy"
    assert by_id["R-010"]["account_status"] == "churned" and "renewed" in by_id["R-010"]["description"]
    # ...and a control: every field of R-001 agrees with every other.
    assert by_id["R-001"]["city"] == "Berlin" and by_id["R-001"]["country"] == "Germany"


if __name__ == "__main__":
    check()
    print("ok")
