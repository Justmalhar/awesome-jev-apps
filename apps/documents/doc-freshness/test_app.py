"""Offline checks for doc-freshness."""
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

    doc = (HERE / "sample_docs.md").read_text(encoding="utf-8")
    reality = (HERE / "sample_reality.txt").read_text(encoding="utf-8")
    assert reality.strip(), "evidence sample must not be empty"

    claims = app.split_claims(doc)
    assert len(claims) == 23, len(claims)
    # Headings supply context, and must not become claims themselves.
    assert not any(c["text"].startswith("#") for c in claims)
    assert claims[0]["section"] == "Endpoints"
    assert "POST /v1/jobs" in claims[0]["text"]
    assert claims[-1]["section"] == "Deprecated"
    # Prose without bullets still splits, so a plain .txt doc works.
    prose = app.split_claims("## S\nFirst fact here. Second fact here.")
    assert len(prose) == 2 and prose[0]["section"] == "S"

    questions = app.build_questions(claims)
    assert len(questions) == len(claims) * app.QUESTIONS_PER_CLAIM
    assert app.QUESTIONS_PER_CLAIM == len(app.HAZARDS) + 1

    # A Choice must have somewhere to land when nothing changed.
    criteria = questions["change_0"]["criteria"]
    assert "still_accurate" in criteria and "not_determinable" in criteria
    assert all(len(text) > 15 for text in criteria.values())
    for key in app.HAZARDS:
        question = questions[f"{key}_0"]
        assert question["type"] == "noul"
        assert set(question["criteria"]) == {"true", "false"}

    removed_at, contradicted_at, unsupported_at = 0.70, 0.65, 0.80
    quiet = {"contradicted": 0.05, "unsupported": 0.05, "describes_removed": 0.05}

    def verdict(probabilities, change="still_accurate"):
        return app.classify(
            probabilities, change, contradicted_at, unsupported_at, removed_at
        )

    assert verdict(quiet) == "current"
    # Removal outranks contradiction even when both fire.
    assert verdict({**quiet, "describes_removed": 0.9, "contradicted": 0.9}) == "stale"
    assert verdict({**quiet, "contradicted": 0.9}) == "wrong"
    # Contradiction outranks 'nobody can tell'.
    assert verdict({**quiet, "contradicted": 0.9, "unsupported": 0.9}) == "wrong"
    assert verdict({**quiet, "unsupported": 0.85}) == "unverifiable"
    # Exactly at a threshold counts as fired.
    assert verdict({**quiet, "contradicted": contradicted_at}) == "wrong"
    # The Choice alone never condemns a claim, but it does earn a human look.
    assert verdict(quiet, change="renamed") == "check"
    assert verdict(quiet, change="not_determinable") == "current"

    ranked = app.rank(
        [
            {"verdict": "current", "probabilities": quiet},
            {"verdict": "stale", "probabilities": {**quiet, "describes_removed": 0.8}},
            {"verdict": "wrong", "probabilities": {**quiet, "contradicted": 0.99}},
            {"verdict": "stale", "probabilities": {**quiet, "describes_removed": 0.95}},
        ]
    )
    assert [r["verdict"] for r in ranked] == ["stale", "stale", "wrong", "current"]
    # Within a bucket, the more certain claim comes first.
    assert ranked[0]["probabilities"]["describes_removed"] == 0.95


if __name__ == "__main__":
    check()
    print("ok")
