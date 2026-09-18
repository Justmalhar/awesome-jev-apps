"""Offline checks for ticket-triage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    tickets = json.loads((HERE / "sample_tickets.json").read_text(encoding="utf-8"))
    assert len(tickets) >= 8
    assert all({"id", "subject", "body"} <= set(t) for t in tickets)

    questions = app.build_questions(tickets)
    assert len(questions) == len(tickets) * 6, len(questions)

    # every Choice needs a no-match option
    assert "other" in questions["issue__0"]["criteria"]
    assert "unclear" in questions["area__0"]["criteria"]
    # Score levels describe situations
    assert all(len(level) > 15 for level in app.URGENCY_LEVELS)
    assert all(len(level) > 15 for level in app.CHURN_LEVELS)
    # independent flags are Nouls, not levels
    assert questions["security__0"]["type"] == "noul"
    assert questions["data_loss__0"]["type"] == "noul"

    base = {"issue": "how_to", "area": "dashboard", "urgency": 0.2, "churn": 0.1,
            "security": 0.02, "data_loss": 0.01}
    assert app.route(base, 0.55, 2.4, 2.5) == {
        "queue": "tier-1-support", "priority": "P3", "reason": "no specialist rule matched"
    }
    # security vetoes the issue-type rule and forces P1
    hot = {**base, "issue": "billing", "security": 0.9}
    assert app.route(hot, 0.55, 2.4, 2.5)["queue"] == "security"
    assert app.route(hot, 0.55, 2.4, 2.5)["priority"] == "P1"
    # ... and not below its threshold
    assert app.route(hot, 0.95, 2.4, 2.5)["queue"] == "finance"
    # relationship risk promotes an otherwise sleepy ticket
    angry = {**base, "churn": 2.9}
    assert app.route(angry, 0.55, 2.4, 2.5)["priority"] == "P1"
    # urgency bands
    assert app.route({**base, "urgency": 2.9}, 0.55, 2.4, 2.5)["priority"] == "P1"
    assert app.route({**base, "urgency": 1.8}, 0.55, 2.4, 2.5)["priority"] == "P2"
    # area falls back when the issue type carries no rule
    assert app.route({**base, "issue": "other", "area": "api"}, 0.55, 2.4, 2.5)["queue"] == "engineering"


if __name__ == "__main__":
    check()
    print("ok")
