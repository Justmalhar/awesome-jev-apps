"""Offline checks for nps-coder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    comments = json.loads((HERE / "sample_comments.json").read_text(encoding="utf-8"))
    assert len(comments) >= 15
    assert all({"id", "rating", "text"} <= set(c) for c in comments)

    codebook = app.DEFAULT_CODEBOOK
    questions = app.build_questions([0, 1], codebook)
    assert len(questions) == 2 * (len(codebook) + 2)
    # a theme is a Noul, because several apply at once
    for theme in codebook:
        assert questions[f"{theme}__0"]["type"] == "noul"
    # the Choice needs an escape hatch
    assert "no_content" in questions["sentiment__0"]["criteria"]
    assert len(app.SENTIMENT_OPTIONS) >= 3

    # batching must respect the provider's context window, not a constant
    small = app.batch_comments(comments, 4000, len(codebook))
    large = app.batch_comments(comments, 200000, len(codebook))
    assert len(large) == 1, "everything fits a huge window in one request"
    assert len(small) > 1, "a small window must split"
    assert sorted(i for chunk in small for i in chunk) == list(range(len(comments)))
    assert all(chunk for chunk in small), "no empty chunks"

    # NPS arithmetic is ours, never the model's
    assert app.nps_bucket(10) == "promoter"
    assert app.nps_bucket(9) == "promoter"
    assert app.nps_bucket(8) == "passive"
    assert app.nps_bucket(7) == "passive"
    assert app.nps_bucket(6) == "detractor"
    assert app.nps_bucket(0) == "detractor"
    assert app.nps_score([10, 10, 0, 0]) == 0.0
    assert app.nps_score([9, 9, 9, 9]) == 100.0
    assert app.nps_score([]) == 0.0

    frame = pd.DataFrame(
        [
            {"rating": 10, "price_value": 0.9, "onboarding": 0.1, "actionable": 0.9},
            {"rating": 2, "price_value": 0.9, "onboarding": 0.1, "actionable": 0.2},
            {"rating": 9, "price_value": 0.1, "onboarding": 0.1, "actionable": 0.5},
        ]
    )
    table = app.theme_table(frame, {"price_value": "", "onboarding": ""}, 0.6)
    price = table[table["theme"] == "price_value"].iloc[0]
    assert price["comments"] == 2
    assert price["nps when mentioned"] == 0.0
    onboarding = table[table["theme"] == "onboarding"].iloc[0]
    assert onboarding["comments"] == 0 and onboarding["nps when mentioned"] == 0.0


if __name__ == "__main__":
    check()
    print("ok")
