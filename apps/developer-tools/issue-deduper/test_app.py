"""Offline checks for issue-deduper. No key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    issues = json.loads((HERE / "sample_issues.json").read_text(encoding="utf-8"))
    assert len(issues) >= 10
    total_pairs = len(issues) * (len(issues) - 1) // 2

    # -- blocking happens in Python and actually removes work ---------------
    pairs = app.candidate_pairs(issues, 0.22, 500)
    assert 0 < len(pairs) < total_pairs, (len(pairs), total_pairs)
    assert pairs == sorted(pairs, key=lambda p: -p.similarity)
    assert all(p.similarity >= 0.22 for p in pairs)
    # the two near-identical CSV-export reports must survive blocking
    keys = {p.key for p in pairs}
    assert "1421~1440" in keys, sorted(keys)
    # a floor of 1.0 keeps nothing; the cap is honoured
    assert app.candidate_pairs(issues, 1.0, 500) == []
    assert len(app.candidate_pairs(issues, 0.0, 5)) == 5
    # no pair is ever compared with itself
    assert all(p.left["id"] != p.right["id"] for p in app.candidate_pairs(issues, 0.0, 500))

    # the two Windows/macOS startup crashes are the most SIMILAR pair in the
    # sample and are different bugs -- blocking must not decide anything
    assert (pairs[0].left["id"], pairs[0].right["id"]) == (1388, 1390), pairs[0].key

    # -- two judgments per surviving pair, one request -----------------------
    questions = app.build_questions(pairs)
    assert len(questions) == 2 * len(pairs), len(questions)
    assert questions["action_0"]["type"] == "score"
    assert questions["a_is_richer_0"]["type"] == "noul"

    # -- the score's levels are the actions, and are concrete ---------------
    assert len(app.ACTIONS) == 3
    assert all(len(level) > 15 for level in app.ACTIONS)
    assert questions["action_0"]["criteria"] == app.ACTIONS

    # -- policy boundaries, pure Python -------------------------------------
    gates = {"merge_gate": 1.6, "separate_ceiling": 0.7}
    assert app.verdict(2.0, **gates) == "merge"
    assert app.verdict(1.6, **gates) == "merge"
    assert app.verdict(1.59, **gates) == "human_decides"
    assert app.verdict(0.71, **gates) == "human_decides"
    assert app.verdict(0.7, **gates) == "keep_separate"
    assert app.verdict(0.0, **gates) == "keep_separate"
    # retuning the gates re-decides stored scores with no inference
    assert app.verdict(1.2, merge_gate=1.0, separate_ceiling=0.5) == "merge"


if __name__ == "__main__":
    check()
    print("ok")
