"""Offline checks for fraud-signals. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    cases = json.loads((HERE / "sample_cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 10
    assert all(c["customer_narrative"].strip() for c in cases)
    assert len({c["case_id"] for c in cases}) == len(cases)

    # -- features are independent properties, so every one is its own Noul --
    assert len(app.FEATURE_IDS) == len(set(app.FEATURE_IDS))
    for feature in app.FEATURES:
        assert len(feature["true"]) > 15 and len(feature["false"]) > 15, feature["id"]
    assert all(len(level) > 15 for level in app.CORROBORATION_LEVELS)

    questions = app.build_questions(cases)
    assert len(questions) == len(cases) * (len(app.FEATURES) + 1)
    assert sum(1 for q in questions.values() if q["type"] == "noul") == len(cases) * len(app.FEATURES)
    assert sum(1 for q in questions.values() if q["type"] == "score") == len(cases)
    assert not any(q["type"] == "choice" for q in questions.values()), (
        "several of these are true at once in any real scam; a Choice would force one"
    )

    # -- no verdict: nothing in this app decides whether a payment is fraud --
    source = (HERE / "app.py").read_text(encoding="utf-8")
    for banned in ("is_fraud", "risk_score", "def verdict", "def decide"):
        assert banned not in source, f"{banned!r} would make this a decision tool"

    # -- the exported schema must be stable and never ragged --
    probabilities = {feature_id: 0.5 for feature_id in app.FEATURE_IDS}
    row = app.feature_row("PC-1", probabilities, 1.4, 0.8)
    assert list(app.feature_frame([row]).columns) == app.COLUMNS
    assert row["schema_version"] == app.SCHEMA_VERSION
    try:
        app.feature_row("PC-2", {"urgency_pressure": 0.9}, 1.0, 0.5)
    except KeyError:
        pass
    else:
        raise AssertionError("a missing feature must fail loudly, not ship a ragged matrix")

    # probabilities go downstream raw, not bucketed
    exact = app.feature_row("PC-3", {**probabilities, "urgency_pressure": 0.6234}, 0.0, 0.0)
    assert exact["urgency_pressure"] == 0.6234

    # -- prevalence is a feature-quality diagnostic, not a fraud call --
    frame = app.feature_frame([
        app.feature_row("A", {**probabilities, "crypto_or_offramp": 0.95}, 0.0, 0.0),
        app.feature_row("B", {**probabilities, "crypto_or_offramp": 0.05}, 0.0, 0.0),
    ])
    rates = app.prevalence(frame, 0.5)
    assert rates["crypto_or_offramp"] == 0.5
    assert set(rates.index) == set(app.FEATURE_IDS)
    assert app.prevalence(frame, 0.99).sum() == 0.0


if __name__ == "__main__":
    check()
    print("ok")
