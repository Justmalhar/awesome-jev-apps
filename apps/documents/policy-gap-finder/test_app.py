"""Offline checks for policy-gap-finder. No key, no network."""

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

    controls = json.loads(app.SAMPLE_CONTROLS.read_text(encoding="utf-8"))
    assert len(controls) >= 10
    assert len({c["domain"] for c in controls}) > 1, "domain rollup needs more than one domain"
    assert app.SAMPLE_POLICY.read_text(encoding="utf-8").strip(), "bundled policy is empty"

    questions = app.build_questions(controls)
    assert len(questions) == len(controls) * app.QUESTIONS_PER_CONTROL

    for index in range(len(controls)):
        # Three independent properties -> three Nouls, never one rubric.
        for prefix in ("covered", "specific", "owned"):
            question = questions[f"{prefix}_{index}"]
            assert question["type"] == "noul"
            assert set(question["criteria"]) == {"true", "false"}
            assert question["instructions"]["policy"] == "`policy`"
            assert question["instructions"]["control_requirement"] == (
                f"`controls[{index}].requirement`"
            )
        # The Choice must have an escape hatch, or it nominates the least-wrong gap.
        gap = questions[f"gap_{index}"]
        assert gap["type"] == "choice"
        assert "none" in gap["criteria"]
        assert set(gap["criteria"]) == set(app.GAP_TYPES)
        assert all(len(text) > 15 for text in gap["criteria"].values())

    # Policy lives in Python: no threshold may reach the model.
    serialised = json.dumps(questions).lower()
    for leaked in ("0.75", "0.25", "threshold"):
        assert leaked not in serialised, f"{leaked!r} leaked into a question"

    accept, reject = 0.75, 0.25
    assert app.assess(0.95, 0.95, 0.95, accept, reject)[0] == "met"
    assert app.assess(0.05, 0.99, 0.99, accept, reject)[0] == "open", "no rule cannot be rescued"
    assert app.assess(0.95, 0.05, 0.99, accept, reject)[0] == "vague"
    assert app.assess(0.95, 0.95, 0.05, accept, reject)[0] == "unowned"
    assert app.assess(0.60, 0.90, 0.90, accept, reject)[0] == "vague", "borderline is never met"
    # Inclusive boundaries on both sides.
    assert app.assess(0.25, 0.9, 0.9, accept, reject)[0] == "open"
    assert app.assess(0.75, 0.75, 0.75, accept, reject)[0] == "met"

    assert app.disagrees("met", "absent") is True
    assert app.disagrees("open", "none") is True
    assert app.disagrees("met", "none") is False
    assert app.disagrees("vague", "aspirational") is False

    rows = [
        {"domain": "Access Control", "status": "met"},
        {"domain": "Access Control", "status": "met"},
        {"domain": "Supplier", "status": "open"},
        {"domain": "People", "status": "unowned"},
    ]
    summary = {entry["domain"]: entry for entry in app.summarise_domains(rows)}
    assert summary["Access Control"]["coverage"] == 1.0
    assert summary["Supplier"]["gaps"] == 1 and summary["Supplier"]["met"] == 0
    # Worst domain first, so the remediation list opens on the real problem.
    assert app.summarise_domains(rows)[0]["domain"] == "Supplier"
    assert app.summarise_domains(rows)[-1]["domain"] == "Access Control"


if __name__ == "__main__":
    check()
    print("ok")
