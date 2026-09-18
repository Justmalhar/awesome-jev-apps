"""Anomaly Narrator -- statistics find the outliers, Jev decides which ones matter.

Every monitoring system you have ever used flags the same three things: the real
incident, the Black Friday spike, and the weekend. Two of those are noise, and
the reason nobody reads the alert channel is that it does not know which.

THE SPLIT THIS APP EXISTS TO DEMONSTRATE:

  Python answers "is this point unusual?"   -- rolling median, MAD, robust
      z-score, day-of-week baseline, exact-zero run length, and the join of the
      deploy/campaign calendar onto each date. Every number, every date
      comparison.

  Jev answers "does anyone need to do something about it?" -- which is judgment
      over the *characterisation*, not over the numbers. The model is handed
      strings pandas already produced ("the value is 4.2x the 28-day trailing
      median for this series", "a campaign event is recorded on the same day")
      and never a sum, a count, or two dates to compare.

The Score levels ARE the three actions: ignore it, note it, page someone. There
is no severity scale to map onto a runbook afterwards, because the rubric is
already the runbook.

And because the paging threshold is a Python slider over stored scores, moving
it re-derives the whole page list with NO new inference.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
import statistics
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE_METRICS = HERE / "sample_metrics.csv"
SAMPLE_CONTEXT = HERE / "sample_context.json"

MIN_HISTORY = 14          # points of trailing history before a baseline means anything
MAD_TO_SIGMA = 1.4826     # MAD -> standard-deviation-equivalent for a normal series

# The rubric IS the decision. Read these as instructions to whoever is on call.
LEVELS = [
    "An expected movement with an obvious benign explanation in the context given -- a "
    "known campaign, a public holiday, a planned change. Nobody needs to act on it.",
    "Unexplained, but within the range of things this series does on its own. Worth a "
    "line in the weekly review rather than interrupting anybody today.",
    "A genuine break in the thing being measured. Customers, revenue or reliability are "
    "affected right now and someone should investigate today.",
]


# ---------------------------------------------------------------------------
# Statistics -- all of it. The model sees none of these numbers as numbers.
# ---------------------------------------------------------------------------


def trailing_baseline(values: list[float], window: int) -> list[tuple[float | None, float | None]]:
    """Median and MAD over the `window` points BEFORE each point.

    Trailing and exclusive on purpose: a point must not contribute to the
    baseline it is judged against, or a sustained break quietly becomes normal.
    """
    out: list[tuple[float | None, float | None]] = []
    for index in range(len(values)):
        past = values[max(0, index - window) : index]
        if len(past) < MIN_HISTORY:
            out.append((None, None))
            continue
        median = statistics.median(past)
        out.append((median, statistics.median([abs(v - median) for v in past])))
    return out


def robust_z(value: float, baseline: float, mad: float) -> float:
    """Deviation in MAD-sigmas. A zero MAD means a perfectly flat window, where
    any movement at all is infinitely surprising -- that is the flat-line case."""
    spread = mad * MAD_TO_SIGMA
    if spread <= 0:
        return 0.0 if value == baseline else float("inf")
    return (value - baseline) / spread


def weekday_factors(frame: pd.DataFrame, window: int = 28) -> dict[int, float]:
    """Typical value on each weekday, relative to that day's trailing median.

    Without this, a weekly series floods the queue with Saturdays: a trailing
    median is day-of-week blind, so every weekend looks like a 40% crash. The
    factor makes "low for a Tuesday" and "low for a Sunday" different claims.
    """
    ratios: dict[int, list[float]] = {}
    values = [float(v) for v in frame["value"]]
    for (median, _), value, day in zip(
        trailing_baseline(values, window), values, frame["date"].dt.weekday
    ):
        if median:
            ratios.setdefault(int(day), []).append(value / median)
    return {day: statistics.median(r) for day, r in ratios.items() if r}


def zero_run_length(values: list[float], index: int) -> int:
    """How far back an unbroken stretch of exactly 0.0 reaches, ending here."""
    if values[index] != 0:
        return 0
    length = 0
    while index - length >= 0 and values[index - length] == 0:
        length += 1
    return length


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def characterise(row: dict) -> dict:
    """Render the already-computed statistics as sentences.

    This is the hinge of the whole app: the model receives prose it can reason
    about qualitatively, and is never handed two numbers with the expectation
    that it will divide them.
    """
    value, median, expected, z = row["value"], row["median"], row["expected"], row["z"]
    day = DAY_NAMES[row["weekday"]]
    parts = {
        "observed_value": f"{value:,.2f}",
        "versus_baseline": (
            f"the value is {value / median:.2f}x the {row['window']}-day trailing median for this "
            f"series, which stood at {median:,.2f}"
            if median
            else f"the {row['window']}-day trailing median for this series was itself {median:,.2f}"
        ),
        "versus_this_weekday": (
            f"a typical {day} for this series runs at {row['weekday_factor']:.2f}x that trailing "
            f"median, so a {day} here would ordinarily land near {expected:,.2f}"
            if expected
            else f"a typical {day} for this series runs at {row['weekday_factor']:.2f}x that trailing median"
        ),
        "direction": (
            f"the value sits {'above' if value > expected else 'below'} what this series normally "
            f"does on a {day}"
        ),
        "deviation": (
            "the recent window was perfectly flat, so any movement at all is unprecedented here"
            if z in (float("inf"), float("-inf"))
            else f"a robust deviation of {abs(z):.1f} MAD-sigmas from that day-adjusted expectation"
        ),
        "recent_range": (
            f"over the {row['window']} days before this point the series ran between "
            f"{row['recent_low']:,.2f} and {row['recent_high']:,.2f}"
        ),
    }
    if row["zero_run"]:
        parts["exact_zero_run"] = (
            f"this point reads exactly 0, and the unbroken stretch of exact zeros ending here "
            f"is {row['zero_run']} day(s) long"
        )
    return parts


def detect(
    frame: pd.DataFrame, context: dict, window: int = 28, threshold: float = 4.0, event_radius: int = 3
) -> list[dict]:
    """Every candidate outlier, fully characterised, with its context joined on.

    The date arithmetic that attaches events to points happens here, in pandas.
    """
    events = pd.DataFrame(context.get("events", []))
    if not events.empty:
        events["date"] = pd.to_datetime(events["date"])

    flagged: list[dict] = []
    for name, group in frame.groupby("series", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        values = [float(v) for v in group["value"]]
        factors = weekday_factors(group, window)
        baseline = trailing_baseline(values, window)

        for index, (median, mad) in enumerate(baseline):
            if median is None or mad is None:
                continue
            stamp = group.loc[index, "date"]
            weekday = int(stamp.weekday())
            factor = factors.get(weekday, 1.0)
            # Judge against what this weekday normally does, not against the
            # week's median -- otherwise every Saturday is an incident.
            expected = median * factor
            z = robust_z(values[index], expected, mad)
            if abs(z) < threshold:
                continue
            past = values[max(0, index - window) : index]
            row = {
                "series": name,
                "date": stamp,
                "value": values[index],
                "median": median,
                "expected": expected,
                "mad": mad,
                "z": z,
                "window": window,
                "weekday": weekday,
                "weekday_factor": factor,
                "zero_run": zero_run_length(values, index),
                "recent_low": min(past),
                "recent_high": max(past),
            }
            row["characterisation"] = characterise(row)
            row["context_events"] = nearby_events(events, stamp, event_radius)
            row["series_meaning"] = context.get("series", {}).get(name, {})
            flagged.append(row)
    return sorted(flagged, key=lambda r: (r["series"], r["date"]))


def nearby_events(events: pd.DataFrame, stamp: pd.Timestamp, radius: int) -> list[dict]:
    """Join the deploy/campaign calendar onto a date. Pandas does the date maths."""
    if events.empty:
        return []
    offsets = (events["date"] - stamp).dt.days
    near = events[offsets.abs() <= radius]
    out = []
    for offset, (_, event) in zip(offsets[offsets.abs() <= radius], near.iterrows()):
        offset = int(offset)
        when = (
            "recorded on the same day as this point"
            if offset == 0
            else f"recorded {abs(offset)} day(s) {'after' if offset > 0 else 'before'} this point"
        )
        out.append({"kind": event["kind"], "note": event["note"], "timing": when})
    return out


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def build_questions(points: list[dict]) -> dict:
    """One Score whose levels are the actions, plus two independent Nouls."""
    questions: dict[str, dict] = {}
    for index in range(len(points)):
        reference = {
            "series_name": f"`points[{index}].series`",
            "what_the_series_measures": f"`points[{index}].series_meaning`",
            "how_this_point_behaved": f"`points[{index}].characterisation`",
            "what_was_happening_around_it": f"`points[{index}].context_events`",
        }
        questions[f"action_{index}"] = score(
            {
                "task": "A statistical detector flagged this point in a daily metric. What should "
                        "the team do about it?",
                "point": reference,
                "note": "The statistics are already settled and are given to you as prose. Judge "
                        "only whether this deserves someone's attention.",
            },
            LEVELS,
        )
        questions[f"pipeline_{index}"] = noul(
            {
                "task": "Is this movement more likely a failure of the data pipeline that produces "
                        "this series than a real change in the thing it measures?",
                "point": reference,
            },
            true="The collector, job or export broke: physically impossible readings, an exact "
                 "zero where zero cannot occur, a dead flat line, or a gap filled with a constant",
            false="The underlying thing being measured genuinely moved, and the pipeline reported "
                  "it correctly",
        )
        questions[f"explained_{index}"] = noul(
            {
                "task": "Does the context supplied alongside this point already account for the "
                        "movement?",
                "point": reference,
            },
            true="A listed deploy, campaign, public holiday or planned change is a sufficient "
                 "reason for a move in this direction, of roughly this magnitude",
            false="Nothing in the supplied context is a plausible reason for it, or the context "
                  "is empty",
        )
    return questions


def batch_points(points: list[dict], context_tokens: int, reserve: float = 0.55) -> list[list[dict]]:
    """Split flagged points so one request fits the provider's window.

    Sized off `provider.context_tokens` -- TypeSafe serves 64k for this model,
    OpenRouter serves 32k, and hardcoding either breaks the other.
    """
    budget = max(1, int(context_tokens * reserve))
    batches: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for point in points:
        cost = len(json.dumps(point_state(point), default=str)) // 4 + 260
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(point)
        used += cost
    if current:
        batches.append(current)
    return batches


def point_state(point: dict) -> dict:
    """Exactly what the model is allowed to see. Note the raw value is absent."""
    return {
        "series": point["series"],
        "series_meaning": point["series_meaning"],
        "characterisation": point["characterisation"],
        "context_events": point["context_events"],
    }


# ---------------------------------------------------------------------------
# Routing -- pure policy, pure Python, no inference
# ---------------------------------------------------------------------------


def route(action: float, pipeline: float, explained: float, page_at: float, flag_gate: float) -> str:
    """Which queue a judged point lands in.

    Pipeline failures outrank the action score deliberately: a flat-lined series
    is a job for whoever owns the collector, not for the product on-call, and
    paging the wrong team is how alerts get muted.
    """
    if pipeline >= flag_gate:
        return "pipeline"
    if action >= page_at:
        return "page"
    if explained >= flag_gate:
        return "explained"
    return "review"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def load_inputs(metrics_upload, context_upload) -> tuple[pd.DataFrame, dict, bool]:
    if metrics_upload is not None:
        frame = pd.read_csv(io.BytesIO(metrics_upload.read()))
        bundled = False
    else:
        frame = pd.read_csv(SAMPLE_METRICS)
        bundled = True
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"])

    if context_upload is not None:
        context = json.loads(context_upload.read().decode("utf-8"))
    elif SAMPLE_CONTEXT.is_file():
        context = json.loads(SAMPLE_CONTEXT.read_text(encoding="utf-8"))
    else:
        context = {}
    return frame, context, bundled


def main() -> None:
    st.set_page_config(page_title="Anomaly Narrator", page_icon="📉", layout="wide")
    st.title("📉 Anomaly Narrator")
    st.caption("Statistics flag the outliers. Jev says which ones are actually a problem.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token context", language=None)

        st.divider()
        st.subheader("Detector (pure Python)")
        window = st.slider("Trailing baseline window (days)", 14, 60, 28, 1)
        threshold = st.slider(
            "Flag beyond this many MAD-sigmas", 2.0, 10.0, 4.0, 0.5,
            help="Lower = more candidates sent for judgment = higher recall and higher cost.",
        )
        event_radius = st.slider("Attach events within (days)", 0, 7, 3, 1)
        st.caption("Changing anything here changes which points exist, so it needs a fresh run.")

        st.divider()
        st.subheader("Policy (no inference)")
        page_at = st.slider(
            "Page someone at an action score above", 0.0, 2.0, 1.5, 0.05,
            help="2.0 is 'investigate today'. Re-derives from stored scores — costs nothing.",
        )
        flag_gate = st.slider("Raise a Noul flag at probability above", 0.0, 1.0, 0.6, 0.05)

    left, right = st.columns(2)
    metrics_upload = left.file_uploader("Metrics CSV (`date`, `series`, `value`)", type=["csv"])
    context_upload = right.file_uploader("Context JSON (series descriptions + events)", type=["json"])

    try:
        frame, context, bundled = load_inputs(metrics_upload, context_upload)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        st.error(f"Could not read those inputs: {exc}")
        return

    missing = {"date", "series", "value"} - set(frame.columns)
    if missing:
        st.error(f"Metrics CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    if bundled:
        st.caption(
            f"Using the bundled sample: {frame['series'].nunique()} daily series over "
            f"{frame['date'].nunique()} days, with a deploy/campaign calendar."
        )

    flagged = detect(frame, context, window, threshold, event_radius)
    batches = batch_points(flagged, provider.context_tokens) if flagged else []

    columns = st.columns(4)
    columns[0].metric("Points in file", f"{len(frame):,}")
    columns[1].metric("Series", frame["series"].nunique())
    columns[2].metric("Flagged by statistics", len(flagged))
    columns[3].metric("Requests needed", len(batches))

    chart = frame.pivot_table(index="date", columns="series", values="value")
    st.line_chart(chart, height=260)

    if not flagged:
        st.info("The detector flagged nothing. Lower the MAD-sigma threshold.")
        return

    with st.expander(f"What the model will actually see for point 1 of {len(flagged)}"):
        st.json(point_state(flagged[0]))

    signature = (window, threshold, event_radius, len(frame), float(frame["value"].sum()))
    cached = st.session_state.get("judged")
    stale = cached is not None and cached["signature"] != signature

    if stale:
        st.warning("Detector settings changed since the last run — the flagged points are new. Re-run to judge them.")

    if st.button(f"Judge {len(flagged)} flagged point(s)", type="primary"):
        judged: list[dict] = []
        usage = {"cost": 0.0, "tokens": 0, "elapsed": 0.0, "questions": 0, "requests": len(batches)}
        progress = st.progress(0.0, text="Judging...")
        try:
            with JevClient(provider=provider) as client:
                for batch_index, group in enumerate(batches):
                    questions = build_questions(group)
                    answers = client.ask({"points": [point_state(p) for p in group]}, questions)
                    usage["cost"] += answers.cost_usd
                    usage["tokens"] += answers.input_tokens
                    usage["elapsed"] += answers.elapsed_s
                    usage["questions"] += len(questions)
                    for index, point in enumerate(group):
                        judged.append(
                            {
                                "series": point["series"],
                                "date": point["date"].date().isoformat(),
                                "value": point["value"],
                                "versus_baseline": point["characterisation"]["versus_baseline"],
                                "action_score": answers.score(f"action_{index}"),
                                "action_confidence": answers.confidence(f"action_{index}"),
                                "pipeline_failure": answers.noul(f"pipeline_{index}"),
                                "context_explains": answers.noul(f"explained_{index}"),
                                "context_events": "; ".join(
                                    e["note"] for e in point["context_events"]
                                ) or "nothing on the calendar nearby",
                            }
                        )
                    progress.progress((batch_index + 1) / len(batches), text="Judging...")
        except JevError as exc:
            progress.empty()
            st.error(str(exc))
            return
        progress.empty()
        st.session_state["judged"] = {"signature": signature, "rows": judged, "usage": usage}
        cached, stale = st.session_state["judged"], False

    if cached is None:
        return

    out = pd.DataFrame(cached["rows"])
    # ── Every line below re-derives from stored answers. Moving the sliders in
    #    the Policy group costs nothing and calls nothing. ──
    out["queue"] = [
        route(r.action_score, r.pipeline_failure, r.context_explains, page_at, flag_gate)
        for r in out.itertuples()
    ]

    st.divider()
    if stale:
        st.caption("⚠️ Showing the previous run. The detector settings above no longer match it.")

    queues = {
        "page": ("🚨 Page someone today", "The rubric's top level. These are breaks, not movements."),
        "pipeline": (
            "🔧 Data pipeline, not the product",
            "The most useful flag in practice: the metric moved because the collector broke, "
            "so this goes to whoever owns the pipeline and never to the product on-call.",
        ),
        "review": ("📋 Weekly review", "Unexplained but unremarkable. A line in a doc, not an interruption."),
        "explained": ("✅ Explained, no action", "The context supplied already accounts for these."),
    }
    columns = st.columns(4)
    for column, (key, (title, _)) in zip(columns, queues.items()):
        column.metric(title, int((out["queue"] == key).sum()))

    for key, (title, blurb) in queues.items():
        subset = out[out["queue"] == key]
        st.subheader(f"{title} ({len(subset)})")
        st.caption(blurb)
        if subset.empty:
            st.caption("— empty at the current thresholds —")
            continue
        st.dataframe(
            subset[
                ["series", "date", "value", "versus_baseline", "action_score",
                 "pipeline_failure", "context_explains", "context_events"]
            ].sort_values("action_score", ascending=False),
            width="stretch", hide_index=True,
        )

    st.download_button(
        "Download judged anomalies (CSV)",
        out.to_csv(index=False).encode("utf-8"),
        file_name="judged_anomalies.csv",
        mime="text/csv",
    )

    usage = cached["usage"]
    st.divider()
    st.caption(
        f"{usage['questions']} questions over {len(out)} flagged point(s) in "
        f"{usage['requests']} request(s) · {usage['tokens']:,} input tokens · "
        f"{usage['elapsed']:.2f}s · ${usage['cost']:.6f} total — "
        f"${usage['cost'] / max(len(out), 1):.8f} per point. "
        "Re-tuning the paging threshold above adds nothing to that figure."
    )


if __name__ == "__main__":
    main()
