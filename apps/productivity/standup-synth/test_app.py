"""Offline checks for standup-synth. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    items = app.load_activity(None)
    assert len(items) >= 20
    assert {"commit", "pr", "ticket"} <= {item["kind"] for item in items}
    assert any(app.is_done(item) for item in items) and not all(app.is_done(item) for item in items)

    questions = app.build_questions(items)
    assert len(questions) == len(items) * 5, len(questions)
    assert "other" in questions["theme_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.THEMES.values())
    assert questions["notable_0"]["type"] == "noul"

    # done-ness is read from the tracker, never judged
    assert app.is_done({"state": "merged"}) and app.is_done({"state": "Closed"})
    assert not app.is_done({"state": "open"}) and not app.is_done({})

    gates = {"notable_gate": 0.55, "noul_gate": 0.6}
    loud = {"notable": 0.9, "visible": 0.9, "blocked": 0.0, "risky": 0.0}
    quiet = {"notable": 0.1, "visible": 0.0, "blocked": 0.0, "risky": 0.0}
    assert app.section({"state": "merged"}, loud, **gates) == "shipped"
    assert app.section({"state": "open"}, loud, **gates) == "in_progress"
    assert app.section({"state": "open"}, quiet, **gates) == "dropped"
    # blocked beats the notability gate: a blocker nobody hears about is the point of standup
    assert app.section({"state": "open"}, {**quiet, "blocked": 0.9}, **gates) == "blocked"
    # ...but finished work is never "blocked", whatever the text said on the way
    assert app.section({"state": "done"}, {**loud, "blocked": 0.9}, **gates) == "shipped"

    grouped = {
        "shipped": [({"id": "#1", "title": "Ship it"}, loud)],
        "in_progress": [({"id": "", "title": "Halfway"}, {**loud, "risky": 0.9})],
        "blocked": [({"id": "PAY-1", "title": "Waiting on creds"}, {**quiet, "blocked": 0.9})],
        "dropped": [({"id": "x", "title": "typo"}, quiet)],
    }
    text = app.render_standup(grouped, 0.6)
    assert "**Shipped**" in text and "`#1` Ship it" in text
    assert "user-visible" in text and "needs care" in text
    assert "Halfway" in text and text.count("- ") == 3, "dropped items never reach the update"
    assert "1 routine item(s) left out." in text
    # no id means no stray backticks
    assert "`` Halfway" not in text
    assert "Nothing this week" in app.render_standup({}, 0.6)


if __name__ == "__main__":
    check()
    print("ok")
