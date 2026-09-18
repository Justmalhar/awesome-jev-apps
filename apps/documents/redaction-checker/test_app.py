"""Offline checks for redaction-checker. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# The same guard scripts/test_apps.py applies to literal strings inside
# noul()/choice()/score(). Our criteria live in a module-level table, so assert
# it here rather than leaving the table unchecked.
BANNED = (
    "how many", "count the", "number of times", "total of", "sum of",
    "add up", "calculate", "how much is", "days between", "earlier than",
)


def check() -> None:
    # Each app ships its own module named `app`; the shared runner imports them
    # into one process, so drop any predecessor before resolving ours.
    sys.modules.pop("app", None)
    sys.modules.pop("jev_provider", None)
    import app

    paragraphs = app.split_paragraphs((HERE / "sample_document.txt").read_text(encoding="utf-8"))
    assert len(paragraphs) == 21, f"expected 21 paragraphs, got {len(paragraphs)}"
    # Newlines inside a paragraph are normalised away; blank lines split.
    assert "\n" not in "".join(paragraphs)
    assert app.split_paragraphs("  \n\n  ") == []
    # No length floor: a short line survives, because it can be a phone number.
    assert app.split_paragraphs("+1 415 555 0182") == ["+1 415 555 0182"]

    # Hazard classes are independent properties, so one Noul each -- never one
    # Choice and never levels on the severity rubric.
    keys = [key for key, _l, _q, _t, _f in app.HAZARDS]
    assert len(keys) == len(set(keys)) == 8, keys
    assert set(keys) == {
        "personal_identity", "contact_details", "financial_personal", "health",
        "customer_confidential", "unreleased_product", "legal_privilege",
        "security_sensitive",
    }
    for key, label, question, true_criteria, false_criteria in app.HAZARDS:
        assert 0 < len(label) <= 40, (key, label)  # a sidebar slider label
        for field in (question, true_criteria, false_criteria):
            assert len(field) > 15, (key, field)
        for field in (label, question, true_criteria, false_criteria):
            assert not any(b in field.lower() for b in BANNED), (key, field)

    # Score levels must be concrete situations, not labels, and ordered.
    assert len(app.SEVERITY_LEVELS) == 4
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS)
    assert not any(level.strip().lower() in {"low", "medium", "high"} for level in app.SEVERITY_LEVELS)
    assert all(not any(b in level.lower() for b in BANNED) for level in app.SEVERITY_LEVELS)

    questions = app.build_questions(paragraphs)
    assert len(questions) == (len(app.HAZARDS) + 1) * len(paragraphs), len(questions)
    assert sum(1 for q in questions.values() if q["type"] == "noul") == 8 * len(paragraphs)
    assert sum(1 for q in questions.values() if q["type"] == "score") == len(paragraphs)
    assert questions["severity_0"]["criteria"] == app.SEVERITY_LEVELS
    # Every Noul states both sides, so the boundary case is written down.
    for key in keys:
        criteria = questions[f"{key}_3"]["criteria"]
        assert set(criteria) == {"true", "false"}, key
    # Paragraphs are referenced by path, not restated -- one ingest, not N copies.
    assert questions["health_7"]["instructions"]["paragraph"] == "`paragraphs[7].text`"

    # -- per-class thresholding, the whole point of separate Nouls --------------
    thresholds = {key: 0.5 for key in keys}
    probabilities = {key: 0.0 for key in keys}
    assert app.fired_classes(probabilities, thresholds) == []

    probabilities["legal_privilege"] = 0.35
    assert app.fired_classes(probabilities, thresholds) == []
    # Drop only the privilege dial: privilege fires, nothing else moves.
    thresholds["legal_privilege"] = 0.30
    assert app.fired_classes(probabilities, thresholds) == ["legal_privilege"]

    # At-threshold counts as fired; just under does not.
    probabilities["health"] = 0.50
    assert "health" in app.fired_classes(probabilities, thresholds)
    probabilities["health"] = 0.49
    assert "health" not in app.fired_classes(probabilities, thresholds)

    # Several classes can fire on one paragraph -- they are not mutually exclusive.
    probabilities.update({"personal_identity": 0.9, "contact_details": 0.9, "health": 0.9})
    fired = app.fired_classes(probabilities, thresholds)
    assert {"personal_identity", "contact_details", "health", "legal_privilege"} <= set(fired)
    # fired_classes preserves HAZARDS order, so the UI reads consistently.
    assert fired == [k for k in keys if k in set(fired)]

    # -- flagging: either signal is enough -------------------------------------
    assert app.needs_review([], 0.0, 2.0) is False
    assert app.needs_review(["health"], 0.0, 2.0) is True      # class, low severity
    assert app.needs_review([], 2.0, 2.0) is True              # severity at threshold
    assert app.needs_review([], 1.99, 2.0) is False
    assert app.needs_review([], 3.0, 4.0) is False             # dial above the top level

    # -- counting happens in Python, never in a question -----------------------
    rows = [
        {"fired": ["health", "legal_privilege"]},
        {"fired": ["health"]},
        {"fired": []},
    ]
    counts = app.class_counts(rows)
    assert counts["health"] == 2 and counts["legal_privilege"] == 1
    assert counts["security_sensitive"] == 0
    assert set(counts) == set(keys)


if __name__ == "__main__":
    check()
    print("ok")
