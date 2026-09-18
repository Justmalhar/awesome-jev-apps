"""Offline checks for statement-categorizer."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    rows = [{"description": "TESCO 4471", "amount": -12.0}] * 4
    questions = app.build_questions(rows)
    assert len(questions) == 12, "category + subscription + business per row"

    assert "other" in app.CATEGORIES, "a Choice needs a no-match category"
    # Descriptions carry the boundary cases; bare labels are a documented trap.
    assert all(len(v) > 20 for v in app.CATEGORIES.values())

    header = (HERE / "sample_transactions.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "description" in header and "amount" in header, header


if __name__ == "__main__":
    check()
    print("ok")
