"""Offline checks for form-validator."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    questions = app.build_questions(app.SAMPLE)
    assert len(questions) == len(app.RULES) == 6, "the whole form is one request"
    assert all(q["type"] == "noul" for q in questions.values()), "independent rules are Nouls"
    # Every Noul states both sides, because the model reads literally.
    assert all({"true", "false"} <= set(q["criteria"]) for q in questions.values())
    assert set(questions) == set(app.RULES), "every rule must have a question"
    assert all(field in app.FIELDS for field, _, _ in app.RULES.values())

    clean = {
        "name_is_a_person": 0.97,
        "title_is_a_real_job": 0.95,
        "company_is_an_org": 0.96,
        "city_in_country": 0.99,
        "email_matches_name": 0.93,
        "bio_is_placeholder": 0.02,
    }
    assert app.field_errors(clean, 0.8) == {}

    dirty = {**clean, "city_in_country": 0.05, "bio_is_placeholder": 0.91}
    marked = app.field_errors(dirty, 0.8)
    assert set(marked) == {"city", "bio"}, marked

    # Boundaries: a 0.8 threshold complains at P<=0.2 for a "below" rule.
    assert "job_title" in app.field_errors({**clean, "title_is_a_real_job": 0.20}, 0.8)
    assert "job_title" not in app.field_errors({**clean, "title_is_a_real_job": 0.21}, 0.8)
    assert "bio" in app.field_errors({**clean, "bio_is_placeholder": 0.80}, 0.8)
    assert "bio" not in app.field_errors({**clean, "bio_is_placeholder": 0.79}, 0.8)
    # Raising the bar to complain marks fewer fields, with no new inference.
    assert app.field_errors(dirty, 0.99) == {}, "an unreachable bar must mark nothing"
    assert len(app.field_errors(dirty, 0.5)) == 2

    # A missing answer must never silently mark a field.
    assert app.field_errors({}, 0.8) == {}


if __name__ == "__main__":
    check()
    print("ok")
