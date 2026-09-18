"""Offline checks for reproducibility-auditor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    papers = json.loads((HERE / "sample_papers.json").read_text(encoding="utf-8"))
    assert papers

    questions = app.build_questions(papers)
    per_paper = len(app.CRITERIA) + 2  # criteria + one score + one choice
    assert len(questions) == len(papers) * per_paper, len(questions)

    kinds = {q["type"] for q in questions.values()}
    assert kinds == {"noul", "score", "choice"}, kinds

    # The Choice must be able to say "nothing here fits".
    access = questions["access_0"]
    assert "unclear" in access["criteria"], access["criteria"].keys()
    assert len(access["criteria"]) >= 3

    # Score levels are concrete situations, not labels.
    assert len(app.ENVIRONMENT_LEVELS) >= 3
    assert all(len(level) > 15 for level in app.ENVIRONMENT_LEVELS), app.ENVIRONMENT_LEVELS
    assert questions["env_0"]["criteria"] == app.ENVIRONMENT_LEVELS

    assert app.classify(0.75, 0.75, 0.25) == "met"
    assert app.classify(0.25, 0.75, 0.25) == "unmet"
    assert app.classify(0.4, 0.75, 0.25) == "unclear"

    met = {c["key"]: "met" for c in app.CRITERIA}

    # Clean pass.
    assert app.tier(met, 3.0, "open")[0] == "likely"
    # Environment floor is a human tier, not a block.
    assert app.tier(met, 1.4, "open")[0] == "human"
    assert app.tier(met, 2.0, "open")[0] == "likely", "floor is inclusive"
    # Ambiguity on a mandatory criterion goes to a human.
    assert app.tier(dict(met, data="unclear"), 3.0, "open")[0] == "human"
    # A clearly unmet mandatory criterion vetoes everything else.
    assert app.tier(dict(met, data="unmet"), 3.0, "open")[0] == "blocked"
    # No route to the artifacts is its own veto.
    assert app.tier(met, 3.0, "unavailable")[0] == "blocked"
    assert app.tier(met, 3.0, "unclear")[0] == "human"
    # A non-mandatory gap never blocks, and mandatory status is tunable policy.
    assert app.tier(dict(met, hardware="unmet"), 3.0, "open")[0] == "likely"
    assert app.tier(dict(met, hardware="unmet"), 3.0, "open", mandatory=["hardware"])[0] == "blocked"


if __name__ == "__main__":
    check()
    print("ok")
