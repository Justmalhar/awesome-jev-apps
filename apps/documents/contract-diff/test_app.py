"""Offline checks for contract-diff. No key, no network."""

from __future__ import annotations

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

    # --- Score levels must be concrete situations, not labels -------------
    assert len(app.SEVERITY_LEVELS) == 3
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS)
    assert not {"low", "medium", "high"} & {l.lower() for l in app.SEVERITY_LEVELS}

    # --- Clause splitting and number-stripping ----------------------------
    old_text = (HERE / "sample_terms_v1.txt").read_text(encoding="utf-8")
    new_text = (HERE / "sample_terms_v2.txt").read_text(encoding="utf-8")
    old = app.split_clauses(old_text)
    new = app.split_clauses(new_text)
    assert len(old) == 14 and len(new) == 14, (len(old), len(new))
    assert old[0].startswith("1. ACCEPTANCE")
    # renumbering must not register as a change: bodies compare without the number
    assert app.body("9. CONFIDENTIALITY. Each party  will  protect.") == app.body(
        "10. CONFIDENTIALITY. Each party will protect."
    )

    # --- The diff is mechanical; assert exactly what it found -------------
    changes = app.diff_clauses(old, new)
    kinds = [c["kind"] for c in changes]
    assert kinds.count("modified") == 4, kinds
    assert kinds.count("removed") == 1, kinds
    assert kinds.count("added") == 1, kinds
    assert len(changes) == 6, changes
    removed = next(c for c in changes if c["kind"] == "removed")
    assert "THIRD PARTY SERVICES" in removed["before"] and removed["after"] == ""
    added = next(c for c in changes if c["kind"] == "added")
    assert "EXPORT CONTROL" in added["after"] and added["before"] == ""
    # the refund clause is an edit, not a delete+add, so it must be paired
    assert any("REFUNDS" in c["before"] and "REFUNDS" in c["after"] for c in changes)

    # identical documents produce no changes, and therefore no request at all
    assert app.diff_clauses(old, old) == []

    # a punitive cutoff turns every edit into a delete + an add
    strict = app.diff_clauses(old, new, cutoff=0.999)
    assert not any(c["kind"] == "modified" for c in strict), strict

    # --- Question design ---------------------------------------------------
    questions = app.build_questions(changes)
    assert len(questions) == 3 * len(changes) == 18
    assert set(questions) >= {"adverse_0", "obligation_0", "severity_0"}
    for qid, question in questions.items():
        if question["type"] == "choice":
            # every Choice must carry an escape hatch
            assert {"none", "other", "unclear"} & set(question["criteria"]), qid
        if question["type"] == "score":
            assert all(len(level) > 15 for level in question["criteria"]), qid
        if question["type"] == "noul":
            assert {"true", "false"} <= set(question["criteria"]), qid
    # independent properties stayed independent
    assert questions["adverse_0"]["type"] == "noul"
    assert questions["obligation_0"]["type"] == "noul"
    assert questions["severity_0"]["type"] == "score"

    # --- Policy boundaries -------------------------------------------------
    assert app.decide(0.9, 0.1, 1.8, 0.6, 0.7, 1.0)[0] == "worse"
    assert app.decide(0.6, 0.1, 1.0, 0.6, 0.7, 1.0)[0] == "worse"  # >= is inclusive
    assert app.decide(0.9, 0.1, 0.2, 0.6, 0.7, 1.0)[0] == "watch"  # adverse, but trivial
    assert app.decide(0.1, 0.95, 0.4, 0.6, 0.7, 1.0)[0] == "watch"  # obligation alone
    assert app.decide(0.59, 0.69, 1.9, 0.6, 0.7, 1.0)[0] == "benign"  # both just under
    assert app.decide(0.0, 0.0, 0.0, 0.6, 0.7, 1.0)[0] == "benign"
    assert set(app.VERDICT_ORDER) == set(app.VERDICT_ICON) == {"worse", "watch", "benign"}


if __name__ == "__main__":
    check()
    print("ok")
