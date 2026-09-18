"""Offline checks for anomaly-narrator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    # ---- the statistics, at their boundaries ----
    flat = [10.0] * 20
    assert app.trailing_baseline(flat, 28)[app.MIN_HISTORY - 1] == (None, None), "too little history"
    median, mad = app.trailing_baseline(flat, 28)[app.MIN_HISTORY]
    assert (median, mad) == (10.0, 0.0)
    # a dead-flat window has no spread, so any movement is unprecedented, not NaN
    assert app.robust_z(10.0, 10.0, 0.0) == 0.0
    assert app.robust_z(0.0, 10.0, 0.0) == float("inf")
    assert abs(app.robust_z(20.0, 10.0, 1.0) - 10.0 / app.MAD_TO_SIGMA) < 1e-9

    # the baseline must be trailing and EXCLUSIVE, or a break normalises itself
    values = [10.0] * 20 + [500.0]
    assert app.trailing_baseline(values, 28)[20][0] == 10.0

    assert app.zero_run_length([1.0, 0.0, 0.0, 0.0], 3) == 3
    assert app.zero_run_length([1.0, 0.0, 0.0, 0.0], 0) == 0, "a non-zero point has no run"
    assert app.zero_run_length([0.0, 1.0, 0.0], 2) == 1

    # ---- detection over the bundled sample finds every planted anomaly ----
    frame = pd.read_csv(HERE / "sample_metrics.csv")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"])
    context = json.loads((HERE / "sample_context.json").read_text(encoding="utf-8"))
    assert frame["series"].nunique() >= 4 and frame["date"].nunique() >= 88, frame.shape

    flagged = app.detect(frame, context, window=28, threshold=4.0, event_radius=3)
    found = {(row["series"], row["date"].date().isoformat()) for row in flagged}
    for planted in (
        ("checkout_errors", "2023-11-08"),   # real incident
        ("signups", "2023-11-24"),           # expected Black Friday spike
        ("api_latency_p95", "2023-10-20"),   # pipeline flat-line to exactly 0
        ("revenue", "2023-11-23"),           # holiday dip
    ):
        assert planted in found, f"detector missed {planted}"

    # the day-of-week baseline is what keeps ordinary weekends out of the queue
    weekends = [r for r in flagged if r["weekday"] >= 5 and r["series"] == "revenue"]
    assert len(weekends) <= 2, f"routine weekends are flooding the queue: {len(weekends)}"
    factors = app.weekday_factors(
        frame[frame["series"] == "revenue"].sort_values("date").reset_index(drop=True), 28
    )
    assert factors[5] < 0.8 < factors[0], "revenue is weekday-heavy; the factors must show it"

    # a higher sensitivity threshold can only ever flag a subset
    loose = app.detect(frame, context, 28, 2.0, 3)
    assert len(loose) >= len(flagged)

    # ---- characterisation: prose, computed in Python, no raw pairs to divide ----
    zeros = next(r for r in flagged if r["series"] == "api_latency_p95")
    parts = zeros["characterisation"]
    assert "exact_zero_run" in parts, "an exact-zero run is the pipeline-failure tell"
    assert "MAD-sigmas" in parts["deviation"]
    assert all(isinstance(v, str) for v in parts.values()), "every statistic arrives pre-rendered"
    # the state sent to the model carries no raw numeric fields to reason over
    state = app.point_state(zeros)
    assert set(state) == {"series", "series_meaning", "characterisation", "context_events"}
    assert "z" not in state and "median" not in state

    # context is joined to dates by pandas; the model never compares two dates
    events = zeros["context_events"]
    assert events and any("same day" in e["timing"] for e in events), events
    assert all({"kind", "note", "timing"} == set(e) for e in events)
    lonely = app.detect(frame, context, 28, 4.0, event_radius=0)
    assert sum(len(r["context_events"]) for r in lonely) < sum(
        len(r["context_events"]) for r in flagged
    ), "a narrower radius must attach fewer events"

    # ---- questions: one Score whose levels are actions + two independent Nouls ----
    questions = app.build_questions(flagged)
    assert len(questions) == 3 * len(flagged), "action + pipeline + explained per point"
    assert questions["action_0"]["type"] == "score"
    assert questions["action_0"]["criteria"] == app.LEVELS
    assert len(app.LEVELS) == 3
    assert all(len(level) > 15 for level in app.LEVELS), "levels are situations, not labels"
    for prefix in ("pipeline_0", "explained_0"):
        assert questions[prefix]["type"] == "noul", prefix
        assert set(questions[prefix]["criteria"]) == {"true", "false"}, prefix

    # ---- batching is sized off the provider window ----
    assert len(app.batch_points(flagged, 64_000)) == 1
    split = app.batch_points(flagged, 3_000)
    assert len(split) > 1 and sum(len(b) for b in split) == len(flagged)
    assert all(b for b in split)

    # ---- routing: pure policy, exercised at its boundaries ----
    # pipeline failures outrank the action score, whatever the score says
    assert app.route(2.0, 0.9, 0.0, page_at=1.5, flag_gate=0.6) == "pipeline"
    assert app.route(2.0, 0.6, 0.0, page_at=1.5, flag_gate=0.6) == "pipeline", "gate is inclusive"
    assert app.route(2.0, 0.59, 0.0, page_at=1.5, flag_gate=0.6) == "page"
    assert app.route(1.5, 0.0, 0.0, page_at=1.5, flag_gate=0.6) == "page", "page_at is inclusive"
    assert app.route(1.49, 0.0, 0.9, page_at=1.5, flag_gate=0.6) == "explained"
    assert app.route(1.49, 0.0, 0.1, page_at=1.5, flag_gate=0.6) == "review"
    # an explained point still pages if the score is high enough -- a deploy
    # explaining an outage does not make the outage stop
    assert app.route(2.0, 0.0, 0.99, page_at=1.5, flag_gate=0.6) == "page"
    # raising the paging threshold can only shrink the page list, no inference involved
    assert app.route(1.6, 0.0, 0.0, page_at=1.8, flag_gate=0.6) == "review"

    assert (HERE / "sample_metrics.csv").is_file()
    assert (HERE / "sample_context.json").is_file()


if __name__ == "__main__":
    check()
    print("ok")
