"""Offline checks for column-inferrer."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    # ---- the profile is what gets sent, so it must actually reduce the data ----
    frame = pd.read_csv(HERE / "sample_table.csv", dtype=str, keep_default_na=False, na_values=[""])
    assert frame.shape[1] >= 20 and frame.shape[0] >= 25, frame.shape

    profile = app.profile_column(frame, "h", examples=8)
    assert profile["column_name"] == "h"
    assert len(profile["example_values"]) == 8, "examples must be capped, not the whole column"
    assert profile["uniqueness"] == "every filled value is distinct", profile["uniqueness"]

    # every statistic is a pandas-rendered string; the model is never asked to derive one
    assert profile["parses_as_number"].endswith("of filled values")
    assert app.profile_column(frame, "n_amt")["parses_as_number"] == "100% of filled values"
    assert app.profile_column(frame, "d1")["parses_as_date"] == "100% of filled values"
    assert app.profile_column(frame, "txt")["parses_as_date"] == "0% of filled values"

    # a column with real nulls reports them, and a constant column repeats
    assert app.profile_column(frame, "note2")["empty_or_null"] != "0% of rows"
    constant = app.profile_column(frame, "k")
    assert constant["uniqueness"] == "values repeat heavily", constant
    assert constant["most_repeated_values"][0]["share_of_rows"] == "100%"

    # a column where nothing repeats says so rather than echoing the column back
    assert app.profile_column(frame, "col_a")["most_repeated_values"] == "no value occurs more than once"

    # ---- questions: one Choice + three independent Nouls per column ----
    profiles = app.profile_table(frame)
    assert len(profiles) == frame.shape[1]
    questions = app.build_questions(profiles)
    assert len(questions) == 4 * frame.shape[1], "type + opaque + misnamed + placeholder per column"
    assert questions["type_0"]["type"] == "choice"
    for prefix in ("opaque_0", "misnamed_0", "placeholder_0"):
        assert questions[prefix]["type"] == "noul", prefix
        assert set(questions[prefix]["criteria"]) == {"true", "false"}, prefix

    # a Choice must have an escape hatch, with a real description
    assert "unknown" in questions["type_0"]["criteria"]
    assert len(app.SEMANTIC_TYPES["unknown"]) > 30
    assert len(app.SEMANTIC_TYPES) == 16, "15 semantic types plus the no-match option"
    assert all(len(v) > 25 for v in app.SEMANTIC_TYPES.values()), "bare labels leave boundaries undefined"

    # ---- batching is sized against the provider window, never a constant ----
    small = app.batch_profiles(profiles, context_tokens=64_000)
    assert len(small) == 1, "25 profiles fit one 64k request"
    assert sum(len(b) for b in small) == len(profiles)
    tiny = app.batch_profiles(profiles, context_tokens=2_000)
    assert len(tiny) > 1, "a narrow window must split"
    assert sum(len(b) for b in tiny) == len(profiles), "splitting must not lose a column"
    assert all(b for b in tiny), "no empty batch"
    # one profile bigger than the whole budget still ships, alone
    assert len(app.batch_profiles(profiles, context_tokens=1)) == len(profiles)

    # ---- the gate, at its boundaries ----
    assert app.decide(0.70, 0.70) == "accepted", "a gate is inclusive at the boundary"
    assert app.decide(0.6999, 0.70) == "review"
    assert app.decide(0.0, 0.0) == "accepted"
    assert app.decide(0.99, 1.0) == "review"

    assert (HERE / "sample_table.csv").is_file()


if __name__ == "__main__":
    check()
    print("ok")
