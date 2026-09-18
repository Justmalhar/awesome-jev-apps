"""Offline checks for rag-passage-filter. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    retrieval = app.load_retrieval(None)
    question, passages = retrieval["question"], retrieval["passages"]
    assert question and len(passages) >= 8, "sample must show several kinds of defect"
    assert {"id", "source", "text"} <= set(passages[0])
    # The sample has to actually contain the injection case, or the security
    # branch of decide() is never exercised against real data.
    assert any("<!--" in p["text"] for p in passages), "sample lost its embedded instruction"

    questions = app.build_questions(question, passages)
    assert len(questions) == len(passages) * (len(app.DEFECTS) + 1)

    # Four kinds of damage are four Nouls, not four levels on one rubric.
    for name in app.DEFECTS:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["damage_0"]["type"] == "score"
    assert questions["damage_0"]["criteria"] == app.DAMAGE_LEVELS
    assert app.SECURITY_DEFECT in app.DEFECTS

    # Score levels must be concrete situations, not labels.
    assert all(len(level) > 15 for level in app.DAMAGE_LEVELS)
    assert len(set(app.DAMAGE_LEVELS)) == len(app.DAMAGE_LEVELS)

    # The question travels with every candidate: a defect is only a defect
    # relative to what was asked.
    assert questions["damage_0"]["instructions"]["candidate"]["question_the_user_asked"] == question

    gates = {"drop_p": 0.65, "warn_p": 0.40, "drop_damage": 2.2}
    clean = dict.fromkeys(app.DEFECTS, 0.02)

    def verdict(overrides, damage):
        return app.decide({**clean, **overrides}, damage, **gates)[0]

    assert verdict({}, 0.1) == "keep"
    assert verdict({"contradicts_the_premise": 0.7}, 1.0) == "drop"
    assert verdict({"superseded_by_another_passage": 0.5}, 1.0) == "warn"
    assert verdict({"misleading_without_context": 0.39}, 2.19) == "keep"
    # high damage drops on its own, even with no single defect over the bar
    assert verdict({"misleading_without_context": 0.5}, 2.2) == "drop"
    # an embedded instruction drops at the *warn* threshold: it is a security
    # event, and must not need to clear the quality bar first
    assert verdict({app.SECURITY_DEFECT: 0.41}, 0.0) == "drop"
    assert verdict({app.SECURITY_DEFECT: 0.39}, 0.0) == "keep"
    # the reason string names the defect that caused the verdict
    _, reason = app.decide({**clean, app.SECURITY_DEFECT: 0.9}, 0.0, **gates)
    assert app.SECURITY_DEFECT in reason
    assert app.decide(clean, 0.0, **gates)[0] in app.VERDICTS

    # Batching respects a context budget rather than hardcoding one.
    assert len(app.batch(passages, 10**6)) == 1
    assert sum(len(g) for g in app.batch(passages, 60)) == len(passages)
    assert all(len(g) == 1 for g in app.batch(passages, 1))


if __name__ == "__main__":
    check()
    print("ok")
