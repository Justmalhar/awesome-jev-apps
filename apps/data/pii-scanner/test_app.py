"""Offline checks for pii-scanner."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SAMPLE = HERE / "sample_warehouse_columns.json"


def check() -> None:
    import app

    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert SAMPLE.is_file() and len(raw) >= 30, "sample schema ships and is big enough to be a scan"
    assert len({row["table"] for row in raw}) == 5, "five tables"

    # ── the privacy toggle must genuinely remove values from the state ──
    email = next(r for r in raw if r["column"] == "email")
    with_values = app.profile_column(email, include_examples=True)
    without = app.profile_column(email, include_examples=False)
    assert "examples" in with_values and "examples" not in without
    assert "@" not in json.dumps(without), "no sample value may survive the toggle"

    # ── Python does the statistics, not the model ──
    assert with_values["uniqueness"] == round(48391 / 48500, 4)
    assert "email_shape" in with_values["regex_shapes_found"]
    # regex sees shape, never meaning: it calls a Chrome version an IP address,
    # and it has nothing at all to say about an absence code.
    ua = app.profile_column(next(r for r in raw if r["column"] == "ua_string"), include_examples=True)
    assert ua["regex_shapes_found"] == ["ip_shape"], "a version number is not an IP -- the model has to see that"
    absence = app.profile_column(next(r for r in raw if r["column"] == "absence_reason_code"),
                                 include_examples=True)
    assert absence["regex_shapes_found"] == ["none"] and absence["uniqueness"] < 0.01

    # ── questions: one Choice + three independent Nouls per column ──
    profiles = [app.profile_column(row, include_examples=True) for row in raw[:3]]
    questions = app.build_questions(profiles, include_examples=True)
    assert len(questions) == 12, "4 questions per column, one request"
    assert "not_personal_data" in questions["cat_0"]["criteria"], "Choice needs its no-match option"
    assert len(app.CATEGORIES) == 10
    assert all(len(text) > 40 for text in app.CATEGORIES.values()), "boundaries live in the criteria"
    assert {questions[f"{k}_0"]["type"] for k in ("direct", "quasi", "special")} == {"noul"}
    assert questions["cat_0"]["type"] == "choice"
    # with values withheld, no question may reference the examples path
    lean = app.build_questions([app.profile_column(raw[1], include_examples=False)],
                               include_examples=False)
    assert "examples" not in json.dumps(lean)

    # ── batching reads the provider's window, never a hardcoded constant ──
    assert len(app.batch_profiles(profiles, 10_000)) == 1
    assert len(app.batch_profiles(profiles, 1)) == 3
    assert sum(len(g) for g in app.batch_profiles(profiles, 30)) == 3

    # ── the tier policy, at its boundaries ──
    tier = lambda cat, d, q, s: app.decide(cat, d, q, s, mask_p=0.7, restrict_p=0.35)
    assert tier("not_personal_data", 0.05, 0.05, 0.02) == "free"
    assert tier("contact_detail", 0.95, 0.9, 0.01) == "mask"
    assert tier("behavioural_record", 0.02, 0.10, 0.80) == "mask", "special category alone masks"
    assert tier("financial_instrument", 0.05, 0.1, 0.0) == "mask", "policy masks these outright"
    assert tier("device_or_online_identifier", 0.3, 0.55, 0.05) == "restrict"
    assert tier("not_personal_data", 0.34, 0.34, 0.34) == "free", "just under the restrict line"
    assert tier("not_personal_data", 0.35, 0.10, 0.0) == "restrict", "just over it"
    assert tier("behavioural_record", 0.0, 0.0, 0.0) == "restrict", "still about a person"
    # thresholds are policy: same judgments, different tier
    assert app.decide("location", 0.5, 0.5, 0.0, mask_p=0.4, restrict_p=0.2) == "mask"
    assert app.decide("location", 0.5, 0.5, 0.0, mask_p=0.9, restrict_p=0.6) == "restrict"


if __name__ == "__main__":
    check()
    print("ok")
