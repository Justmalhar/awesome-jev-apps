"""Offline checks for ml-features.

The interesting one is the last block: it proves the BUNDLED SAMPLE actually
carries signal in the text that the structured columns do not, using crude
keyword proxies instead of Jev. If that lift were not there, the app would be a
machine for producing a convincing zero, and the offline checks would never
notice. No key, no network.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # ── Feature design: independent properties, separate Nouls. ──
    assert len(app.NOUL_FEATURES) >= 6, "too few text features to be worth a request"
    assert len({f["id"] for f in app.NOUL_FEATURES}) == len(app.NOUL_FEATURES)
    for feature in app.NOUL_FEATURES:
        assert len(feature["true"]) > 40 and len(feature["false"]) > 40, \
            f"{feature['id']}: criteria must draw the boundary, not restate the label"

    # ── The Score is a degree, and its levels are situations that stand alone. ──
    assert len(app.TEMPERATURE_LEVELS) == 3
    assert all(len(level) > 15 for level in app.TEMPERATURE_LEVELS)
    assert all(level[0].isupper() and level.endswith(".") for level in app.TEMPERATURE_LEVELS)

    # ── The Choice has its escape hatch, with a real description. ──
    assert "other" in app.ISSUE_TYPES, "a Choice without a no-match option files everything somewhere"
    assert len(app.ISSUE_TYPES["other"]) > 15
    assert all(len(text) > 15 for text in app.ISSUE_TYPES.values())

    # ── One batched ask(): every question for every row, no loop per ticket. ──
    rows = [{"ticket_text": "a"}, {"ticket_text": "b"}]
    questions = app.build_questions(rows)
    assert len(questions) == 2 * app.questions_per_row() == 2 * (len(app.NOUL_FEATURES) + 2)
    assert questions["temperature_0"]["type"] == "score"
    assert questions["issue_1"]["type"] == "choice"
    assert questions[f"{app.NOUL_FEATURES[0]['id']}_0"]["type"] == "noul"
    assert questions["issue_0"]["instructions"]["ticket_text"] == "`tickets[0].ticket_text`", \
        "point at the field; jev-1.13 degrades on indirection"
    assert app.build_questions([]) == {}

    # ── Batching follows the provider's context, never a hardcoded size. ──
    many = [{"ticket_text": "x" * 400} for _ in range(60)]
    small = app.batch_rows(many, 8_000)
    large = app.batch_rows(many, 64_000)
    assert sum(len(b) for b in small) == 60 and sum(len(b) for b in large) == 60
    assert len(small) > len(large) >= 1
    assert all(len(b) == 1 for b in app.batch_rows(many, 1)), "a tiny context still makes progress"
    assert app.batch_rows([], 64_000) == []

    # ── Answers -> a numeric design block, including the one-hot done in pandas. ──
    records = [
        {**{f["id"]: 0.5 for f in app.NOUL_FEATURES}, "temperature": 1.2,
         "issue": "billing_account", "issue_confidence": 0.8},
        {**{f["id"]: 0.1 for f in app.NOUL_FEATURES}, "temperature": 0.3,
         "issue": "other", "issue_confidence": 0.4},
    ]
    features = app.jev_frame(records)
    assert len(features) == 2
    assert features["issue_billing_account"].tolist() == [1.0, 0.0], "the Choice is one-hot in pandas"
    assert list(features.dtypes.unique()) == [np.dtype("float64")], "every feature must be numeric"
    # Every category gets a column even when unseen, so the matrix is stable run to run.
    assert all(f"issue_{name}" in features.columns for name in app.ISSUE_TYPES)

    # ── The two design matrices must differ by exactly the text block. ──
    frame = pd.read_csv(HERE / "sample_tickets.csv")
    assert {app.TEXT_COLUMN, app.TARGET_COLUMN} <= set(frame.columns)
    assert len(frame) >= 150, "too few rows for a cross-validated AUC to mean anything"
    baseline_X = app.design_matrix(frame)
    enriched_X = app.design_matrix(frame, app.jev_frame(records * (len(frame) // 2)))
    assert enriched_X.shape[1] - baseline_X.shape[1] == features.shape[1]
    assert list(baseline_X.columns) == list(enriched_X.columns[: baseline_X.shape[1]])
    assert app.TEXT_COLUMN not in baseline_X.columns, "raw text must never reach the model matrix"

    # ── Lift is pure arithmetic, and Brier is flipped because lower is better. ──
    base = {"auc": 0.70, "average_precision": 0.4, "accuracy": 0.6, "brier": 0.20, "n_features": 7}
    rich = {"auc": 0.78, "average_precision": 0.5, "accuracy": 0.65, "brier": 0.17, "n_features": 23}
    delta = app.lift(base, rich)
    assert abs(delta["auc"] - 0.08) < 1e-9
    assert abs(delta["brier"] - 0.03) < 1e-9, "a Brier drop must read as a positive gain"
    assert delta["features_added"] == 16

    # ── The verdict must be willing to say no. ──
    assert app.verdict(0.05, 0.03) == "worth it"
    assert app.verdict(0.03, 0.03) == "worth it", "the threshold is inclusive"
    assert app.verdict(0.01, 0.03) == "marginal"
    assert app.verdict(0.0, 0.03) == "not worth it"
    assert app.verdict(-0.04, 0.03) == "not worth it"

    # ── The evaluation loop itself runs offline, on the real baseline block. ──
    y = frame[app.TARGET_COLUMN].astype(int).to_numpy()
    assert set(np.unique(y)) == {0, 1} and 0.15 < y.mean() < 0.55, "the sample must be learnable"
    measured = app.evaluate(baseline_X, y, seed=20260919, folds=5, regularization=1.0)
    assert 0.0 <= measured["auc"] <= 1.0 and measured["n_features"] == baseline_X.shape[1]
    assert len(measured["predicted"]) == len(frame), "out-of-fold prediction for every row"

    # ── THE ONE THAT MATTERS: the sample's text really does carry extra signal. ──
    # Crude keyword proxies stand in for the Nouls. If these lift AUC, real
    # calibrated features can only do better; if they did not, the app would be
    # demonstrating nothing and no live run would tell us.
    proxies = pd.DataFrame(
        {
            name: frame[app.TEXT_COLUMN].str.contains(pattern, case=False, regex=True).astype(float)
            for name, pattern in {
                "leaving": r"renewal|elsewhere|reconsider|justify keeping|won't stay|not sure we'll stay",
                "competitor": r"zendrift|coalesce|brightloom|other vendor",
                "repeat": r"again|same thing|came back|round this loop|fourth time",
                "blocked": r"blocked|stopping|cannot work|cannot ship|seeing errors",
                "exit": r"terminate|notice period|closing the account|export",
                "calm": r"no rush|not urgent|whenever",
            }.items()
        }
    )
    assert proxies.sum().min() > 0, "every planted text signal must appear in the sample"
    proxied = app.evaluate(app.design_matrix(frame, proxies), y, 20260919, 5, 1.0)
    gain = app.lift(measured, proxied)["auc"]
    assert gain > 0.05, (
        f"the bundled sample carries no usable text signal (AUC {measured['auc']:.3f} -> "
        f"{proxied['auc']:.3f}); the app would report a convincing zero"
    )
    assert re.search(r"reconsidering the renewal|looking elsewhere", " ".join(frame[app.TEXT_COLUMN]))


if __name__ == "__main__":
    check()
    print("ok")
