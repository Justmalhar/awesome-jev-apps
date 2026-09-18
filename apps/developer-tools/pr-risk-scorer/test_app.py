"""Offline checks for pr-risk-scorer. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pr_risk as app

    # -- the diff splitter is Python's job, and must be exact --------------
    patch = (HERE / "sample_diff.patch").read_text(encoding="utf-8")
    files = app.split_diff(patch)
    paths = [f.path for f in files]
    assert paths == [
        "README.md",
        "shipyard/auth.py",
        "shipyard/migrations/0007_drop_legacy_jobs.py",
        "shipyard/worker.py",
        "shipyard/format.py",
    ], paths
    assert "compare_digest" in files[1].patch
    assert all(f.patch for f in files)
    assert app.split_diff("no diff headers here") == []
    truncated = app.split_diff(patch, max_chars=20)
    assert all(len(f.patch) <= 20 for f in truncated)

    # -- four independent judgments per file, one request ------------------
    questions = app.build_questions(files)
    assert len(questions) == 4 * len(files), len(questions)
    assert questions["depth_0"]["type"] == "score"
    for kind in ("security", "irreversible", "concurrency"):
        assert questions[f"{kind}_0"]["type"] == "noul", kind
        assert set(questions[f"{kind}_0"]["criteria"]) == {"true", "false"}

    # -- score levels are concrete situations, never labels -----------------
    assert len(app.DEPTH_LEVELS) == 4
    assert all(len(level) > 15 for level in app.DEPTH_LEVELS)
    assert questions["depth_0"]["criteria"] == app.DEPTH_LEVELS

    # -- batching respects a budget ----------------------------------------
    groups = app.batch(files, 200)
    assert sum(len(g) for g in groups) == len(files)
    assert len(app.batch(files, 10**9)) == 1

    # -- policy is pure Python: boundaries ---------------------------------
    assert app.route(0.0) == "skim and merge"
    assert app.route(2.49) == "skim and merge"
    assert app.route(2.5) == "careful review by a maintainer"
    assert app.route(4.99) == "careful review by a maintainer"
    assert app.route(5.0) == "two reviewers, one from the owning team"

    # -- reweighting is arithmetic over stored floats, no inference --------
    risk = app.FileRisk(path="a.py", depth=2.0, security=0.9, irreversible=0.1, concurrency=0.0)
    baseline = risk.weighted(app.DEFAULT_WEIGHTS)
    assert abs(baseline - (2.0 + 0.9 * 1.5 + 0.1 * 2.0)) < 1e-9, baseline
    heavier = risk.weighted({**app.DEFAULT_WEIGHTS, "security": 3.0})
    assert heavier > baseline
    assert app.route(heavier) != app.route(0.0)


if __name__ == "__main__":
    check()
    print("ok")
