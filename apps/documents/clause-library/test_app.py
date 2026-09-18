"""Offline checks for clause-library. No key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    # Each app ships its own module named `app`; the shared runner imports them
    # into one process, so drop any predecessor before resolving ours.
    sys.modules.pop("app", None)
    sys.modules.pop("jev_provider", None)
    import app

    library = app.load_library((HERE / "sample_library.json").read_text(encoding="utf-8"))
    assert len(library) == 10, len(library)
    assert {e["id"] for e in library} >= {"liability", "governing_law", "assignment"}

    # A library without id/title/text, or one that squats on 'none', must be rejected.
    for bad in ('[]', '[{"id": "x", "title": "t"}]', '[{"id": "none", "title": "t", "text": "b"}]'):
        try:
            app.load_library(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"load_library accepted {bad!r}")

    clauses = app.split_clauses((HERE / "sample_incoming.txt").read_text(encoding="utf-8"))
    assert len(clauses) == 10, f"expected 10 clauses, got {len(clauses)}"
    assert clauses[0].startswith("8.1 LIMITATION"), clauses[0][:40]
    assert clauses[-1].startswith("8.10 MOST FAVOURED"), clauses[-1][:40]
    # The two-line title block is below the clause floor and must be dropped.
    assert not any(c.startswith("COUNTERPARTY PAPER") for c in clauses)
    assert app.split_clauses("short line\n\nanother short one") == []

    questions = app.build_questions(clauses, library)
    assert len(questions) == 4 * len(clauses), len(questions)
    assert sum(1 for q in questions.values() if q["type"] == "choice") == len(clauses)
    assert sum(1 for q in questions.values() if q["type"] == "noul") == 3 * len(clauses)

    match = questions["match_0"]
    # Every library id is an option, and the escape hatch is present and described.
    for entry in library:
        assert entry["id"] in match["criteria"], entry["id"]
    assert "none" in match["criteria"]
    assert len(match["criteria"]["none"]) > 15
    assert len(match["criteria"]) == len(library) + 1

    # Independent properties stay independent: three separate Nouls, each with
    # both criteria written out so the boundary case is stated, not implied.
    for prefix in ("deviates", "worse", "novel"):
        question = questions[f"{prefix}_0"]
        assert question["type"] == "noul", prefix
        assert set(question["criteria"]) == {"true", "false"}, prefix
        assert all(len(v) > 15 for v in question["criteria"].values()), prefix

    # No banned phrase reaches the model via the library text we splice in.
    assert "approved_library" in json.dumps(questions["deviates_0"]["instructions"])

    # -- routing policy, at its boundaries -------------------------------------
    high, low = 0.95, 0.05
    approve = app.route("liability", high, low, low, low, 0.45, 0.60, 0.60)
    assert approve[0] == app.ROUTE_APPROVE, approve

    # Novel wins over everything, even a confident match.
    assert app.route("liability", high, low, low, high, 0.45, 0.60, 0.60)[0] == app.ROUTE_LEGAL
    # The no-match option escalates even when it wins outright.
    assert app.route("none", high, low, low, low, 0.45, 0.60, 0.60)[0] == app.ROUTE_LEGAL
    # A confident-looking id we do not actually believe in escalates too.
    assert app.route("liability", 0.30, low, low, low, 0.45, 0.60, 0.60)[0] == app.ROUTE_LEGAL
    # Deviation alone redlines; deviation against us redlines with a louder reason.
    neutral = app.route("liability", high, high, low, low, 0.45, 0.60, 0.60)
    adverse = app.route("liability", high, high, high, low, 0.45, 0.60, 0.60)
    assert neutral[0] == adverse[0] == app.ROUTE_REDLINE
    assert "against us" in adverse[1] and "against us" not in neutral[1]

    # Exactly-at-threshold counts as fired, on every threshold.
    assert app.route("liability", 0.45, low, low, low, 0.45, 0.60, 0.60)[0] == app.ROUTE_APPROVE
    assert app.route("liability", high, 0.60, low, low, 0.45, 0.60, 0.60)[0] == app.ROUTE_REDLINE
    assert app.route("liability", high, low, low, 0.60, 0.45, 0.60, 0.60)[0] == app.ROUTE_LEGAL

    # Thresholds are policy, not prompt: moving one re-routes with no inference.
    assert app.route("liability", high, 0.50, low, low, 0.45, 0.40, 0.60)[0] == app.ROUTE_REDLINE
    assert app.route("liability", high, 0.50, low, low, 0.45, 0.90, 0.60)[0] == app.ROUTE_APPROVE


if __name__ == "__main__":
    check()
    print("ok")
