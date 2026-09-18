"""Offline checks for alert-router."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import router as app

    questions = app.build_questions(app.ALERTS[0])
    assert len(questions) == 3, "one request per alert, three questions"
    assert "unclear" in questions["route"]["criteria"], "a Choice needs a no-match option"
    assert set(questions["route"]["criteria"]) == set(app.ROUTES)
    assert questions["customer_impact"]["type"] == "noul"
    assert questions["self_healing"]["type"] == "noul"

    high, low = 0.9, 0.1
    # Live customer impact overrides whatever the model routed to.
    assert app.decide("ticket", high, low, 0.6, 0.7) == "page"
    assert app.decide("ignore", high, low, 0.6, 0.7) == "page"
    # Already recovered, nobody affected: never page.
    assert app.decide("page", low, high, 0.6, 0.7) == "ticket"
    assert app.decide("ignore", low, high, 0.6, 0.7) == "ignore"
    assert app.decide("unclear", low, high, 0.6, 0.7) == "ignore"
    # Nothing decisive either way: the model's route stands.
    assert app.decide("ticket", low, low, 0.6, 0.7) == "ticket"
    assert app.decide("page", low, low, 0.6, 0.7) == "page"
    # An unreadable alert becomes a ticket, never a page and never silence.
    assert app.decide("unclear", low, low, 0.6, 0.7) == "ticket"
    # Impact AND self-healing both high: neither branch fires, route stands.
    assert app.decide("ticket", high, high, 0.6, 0.7) == "ticket"
    # Boundaries.
    assert app.decide("ticket", 0.6, 0.69, 0.6, 0.7) == "page"
    assert app.decide("ticket", 0.59, 0.69, 0.6, 0.7) == "ticket"

    # Percentiles are Python arithmetic.
    assert app.percentile([], 0.95) == 0.0
    assert app.percentile([5.0], 0.95) == 5.0
    assert app.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert app.percentile(list(range(1, 101)), 0.95) == 95
    assert app.percentile([9.0, 1.0, 5.0], 0.95) == 9.0, "unsorted input must still work"

    assert len(app.ALERTS) >= 10, "enough alerts to make a p95 mean something"
    assert {a["env"] for a in app.ALERTS} > {"prod"}, "non-prod alerts must be represented"


if __name__ == "__main__":
    check()
    print("ok")
