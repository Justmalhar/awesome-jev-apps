"""Offline checks for spec-ambiguity."""
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

    text = (HERE / "sample_spec.txt").read_text(encoding="utf-8")
    sentences = app.split_sentences(text)
    assert len(sentences) == 32, len(sentences)
    # The title line is not numbered, so it must not survive the split.
    assert not any(s.startswith("CHECKOUT") for s in sentences)
    assert sentences[0].startswith("The checkout page must load quickly")

    # Unnumbered prose still splits, so the app works on a pasted paragraph.
    assert len(app.split_sentences("One thing happens. Another thing happens.")) == 2

    questions = app.build_questions(sentences)
    assert len(questions) == len(sentences) * app.QUESTIONS_PER_SENTENCE
    assert app.QUESTIONS_PER_SENTENCE == len(app.HAZARDS) + 2

    # Every Choice needs an escape hatch, or it nominates the least-wrong family.
    assert "unambiguous" in questions["family_0"]["criteria"]
    assert len(questions["family_0"]["criteria"]["unambiguous"]) > 15
    # Score levels must be situations, not labels.
    assert all(len(level) > 15 for level in app.COST_LEVELS)
    assert len(app.COST_LEVELS) >= 2
    # Each hazard is its own Noul with both criteria filled in.
    for key in app.HAZARDS:
        question = questions[f"{key}_0"]
        assert question["type"] == "noul"
        assert set(question["criteria"]) == {"true", "false"}
    assert questions["cost_0"]["type"] == "score"

    thresholds = {key: 0.65 for key in app.HAZARDS}
    none_fired = {key: 0.1 for key in app.HAZARDS}
    one_fired = {**none_fired, "unhandled_failure": 0.9}

    assert app.flagged_hazards(one_fired, thresholds) == ["unhandled_failure"]
    assert app.flagged_hazards(none_fired, thresholds) == []
    # Exactly at the threshold counts as fired.
    assert app.flagged_hazards({**none_fired, "two_readings": 0.65}, thresholds) == ["two_readings"]

    # No hazard fired -> clear, whatever the Choice and Score said.
    assert app.triage(none_fired, 3.0, "unhandled_failure", thresholds, 2.0) == "clear"
    # Hazard fired and expensive to find late -> rewrite.
    assert app.triage(one_fired, 2.0, "unhandled_failure", thresholds, 2.0) == "rewrite"
    # Hazard fired but cheap, and the Choice says unambiguous -> clear.
    assert app.triage(one_fired, 0.5, "unambiguous", thresholds, 2.0) == "clear"
    # Hazard fired, cheap, but the Choice still names a family -> human review.
    assert app.triage(one_fired, 0.5, "vague_quality", thresholds, 2.0) == "review"

    # Raising one slider must silence only that hazard.
    strict = {**thresholds, "unhandled_failure": 0.95}
    assert app.triage(one_fired, 3.0, "unhandled_failure", strict, 2.0) == "clear"


if __name__ == "__main__":
    check()
    print("ok")
