"""Budget Variance -- pandas computes the variance, Jev classifies the stated cause.

The cleanest split in this whole category.

  pandas   budget minus actual, the percentage, the direction, materiality, the
           annualised run-rate impact, every subtotal on screen. All of it.
  Jev      reads the owner's commentary and answers the question a spreadsheet
           cannot: what is this variance being BLAMED on, is that cause going to
           repeat, was it within the department's control, and did anybody commit
           to doing something about it.

Note the second-order economy: only MATERIAL variances get questions. Materiality
is two comparisons in pandas, costs nothing, and typically halves the number of
lines that reach the model. Filtering in code before judging in the model is the
pattern, not an optimisation.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE_BUDGET = HERE / "sample_budget.csv"
SAMPLE_NOTES = HERE / "sample_notes.csv"

NO_CAUSE = "no_cause_stated"

CAUSES = {
    "timing_shift": "Spending moved between periods. The commentary says the money was always going to be spent, just in a different quarter.",
    "price_increase": "The same thing was bought at a higher unit price than the budget assumed, such as a contract renewal, a rate rise or inflation.",
    "volume_change": "More or less of the thing was bought than planned, at the planned price.",
    "headcount_change": "People joined, left, or were not replaced, changing the staffing cost against plan.",
    "substitution_of_resource": "A different and differently-priced resource was used in place of the planned one, such as contractors covering vacant permanent roles.",
    "project_delay": "Planned work did not start or finish, so the associated spend did not happen.",
    "unplanned_event": "Something outside the plan happened and had to be paid for, such as an incident, a claim or an emergency.",
    "fx_movement": "The commentary attributes the difference to exchange rates.",
    "waste_or_oversight": "Money was spent on something nobody needed or nobody stopped, such as resources left running.",
    "budget_set_wrong": "The commentary says the original budget figure was unrealistic or based on a wrong assumption.",
    NO_CAUSE: "The commentary gives no cause at all, or there is no commentary.",
}

# Three different conversations a finance business partner would have next.
SPECIFICITY_LEVELS = [
    "The commentary restates that there is a difference without saying what caused it.",
    "The commentary names a cause in general terms, but nothing in it could be checked against another record.",
    "The commentary names specific events, decisions, dates or approvals that could be verified against other records.",
]


def reconcile(budget: pd.DataFrame, notes: pd.DataFrame) -> pd.DataFrame:
    """Every number in this app is computed here. The model sees none of them
    derived -- it sees the line item, the owner's note, and nothing else."""
    frame = budget.merge(notes, on="line_id", how="left")
    frame["note"] = frame["note"].fillna("")
    frame["owner"] = frame["owner"].fillna("")
    frame["variance"] = frame["actual"] - frame["budget"]
    frame["variance_pct"] = (frame["variance"] / frame["budget"] * 100).round(2)
    frame["direction"] = frame["variance"].map(lambda v: "overspend" if v > 0 else ("underspend" if v < 0 else "on_budget"))
    frame["abs_variance"] = frame["variance"].abs()
    return frame


def material(frame: pd.DataFrame, min_value: float, min_pct: float) -> pd.DataFrame:
    """Two comparisons, in pandas, free. Halves what reaches the model."""
    return frame[(frame["abs_variance"] >= min_value) & (frame["variance_pct"].abs() >= min_pct)].reset_index(drop=True)


def build_questions(lines: list[dict]) -> dict:
    """One Choice and three independent judgments per material variance.

    The cause, whether it repeats, whether it was controllable and whether anyone
    committed to fixing it are four separate things -- an uncontrollable cause can
    still come with a corrective action, and a one-off can still be somebody's
    fault. Separate questions, separate gates.
    """
    questions: dict[str, dict] = {}
    for index in range(len(lines)):
        reference = {
            "department": f"`lines[{index}].department`",
            "line_item": f"`lines[{index}].line_item`",
            "direction": f"`lines[{index}].direction`",
            "owner_commentary": f"`lines[{index}].note`",
        }
        questions[f"cause_{index}"] = choice(
            {
                "task": "What cause does the owner's commentary give for this budget line coming out different from plan?",
                "variance": reference,
                "note": "Classify the cause the commentary states. Do not supply a cause it does not give.",
            },
            CAUSES,
        )
        questions[f"recurring_{index}"] = noul(
            {
                "task": "Does the commentary indicate this difference will still be there in future periods?",
                "variance": reference,
            },
            true="The commentary describes a new ongoing level: a permanent change, a renewed contract, a standing decision, or something that repeats every period.",
            false="The commentary describes something confined to this period, or spending that simply lands in a different period.",
        )
        questions[f"controllable_{index}"] = noul(
            {
                "task": "Does the commentary describe a cause the department could have decided differently?",
                "variance": reference,
            },
            true="The cause is the department's own choice, plan or oversight: a decision it made, work it did not do, or something it failed to stop.",
            false="The cause came from outside the department: a market rate, a third party, an external event, or a decision made elsewhere in the business.",
        )
        questions[f"action_{index}"] = noul(
            {
                "task": "Does the commentary commit to a specific action in response?",
                "variance": reference,
            },
            true="A named corrective step is described as done or scheduled, such as a control put in place, a process changed, or a date by which it resolves.",
            false="The commentary explains the difference without committing to doing anything about it.",
        )
        questions[f"specificity_{index}"] = score(
            {
                "task": "How much of this commentary could be checked against another record?",
                "variance": reference,
            },
            SPECIFICITY_LEVELS,
        )
    return questions


TIMING_CAUSES = {"timing_shift", "project_delay"}


def action(cause: str, recurring: float, controllable: float, recurring_gate: float, control_gate: float) -> str:
    """What finance does with this line. Pure policy, in Python.

    Ordering is deliberate: an unexplained material variance is a conversation
    regardless of anything else, and a permanent change to the run rate needs the
    forecast moved whether or not anyone is at fault.
    """
    if cause == NO_CAUSE:
        return "explanation_required"
    if recurring >= recurring_gate:
        return "reforecast" if controllable < control_gate else "reforecast_and_challenge"
    if controllable >= control_gate:
        return "challenge_owner"
    if cause in TIMING_CAUSES:
        return "phasing_correction"
    return "accept"


def run_rate_impact(variance: float, periods_remaining: int) -> float:
    """If it repeats, this is what it costs for the rest of the year. Multiplication,
    so it happens in Python -- jev-1.13 cannot do it and is never asked."""
    return round(variance * periods_remaining, 2)


def load_csv(uploaded, fallback: Path) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(uploaded.read())) if uploaded else pd.read_csv(fallback)


def main() -> None:
    st.set_page_config(page_title="Budget Variance", page_icon="📉", layout="wide")
    st.title("📉 Budget Variance")
    st.caption("pandas computes every variance. Jev classifies what the owner says caused it.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Materiality (pure pandas)")
        min_value = st.number_input("Minimum absolute variance", value=25000.0, step=5000.0)
        min_pct = st.slider("Minimum variance percent", 0.0, 50.0, 5.0, 0.5)
        st.divider()
        st.subheader("Gates")
        recurring_gate = st.slider("Treat as permanent above P=", 0.0, 1.0, 0.6, 0.05)
        control_gate = st.slider("Treat as controllable above P=", 0.0, 1.0, 0.6, 0.05)
        periods_remaining = st.slider("Periods left in the year", 0, 6, 2, 1)

    uploaded_budget = st.file_uploader("Budget CSV", type=["csv"])
    uploaded_notes = st.file_uploader("Notes CSV", type=["csv"])
    budget = load_csv(uploaded_budget, SAMPLE_BUDGET)
    notes = load_csv(uploaded_notes, SAMPLE_NOTES)

    missing = {"line_id", "department", "line_item", "budget", "actual"} - set(budget.columns)
    if missing:
        st.error(f"Budget CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    frame = reconcile(budget, notes)
    lines = material(frame, min_value, min_pct)

    columns = st.columns(4)
    columns[0].metric("Budget lines", len(frame))
    columns[1].metric("Total variance", f"{frame['variance'].sum():,.0f}")
    columns[2].metric("Material lines", len(lines))
    columns[3].metric("Material variance", f"{lines['abs_variance'].sum():,.0f}")

    st.subheader("Variance, computed before anything is asked")
    st.dataframe(
        frame[["line_id", "department", "line_item", "budget", "actual", "variance", "variance_pct", "direction"]],
        width="stretch", hide_index=True,
    )
    st.caption(
        f"Materiality filtered {len(frame)} lines down to {len(lines)}. That filter is two "
        f"comparisons in pandas and cost nothing — it is also the cheapest thing in this app."
    )

    if lines.empty:
        st.info("No material variances at this threshold.")
        return

    if not st.button(f"Classify {len(lines)} material variances", type="primary"):
        return

    records = lines.to_dict("records")
    questions = build_questions(records)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"lines": records}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, line in enumerate(records):
        cause = answers.choice(f"cause_{index}")
        recurring = answers.noul(f"recurring_{index}")
        controllable = answers.noul(f"controllable_{index}")
        rows.append(
            {
                "line_id": line["line_id"],
                "department": line["department"],
                "line_item": line["line_item"],
                "variance": float(line["variance"]),
                "variance_pct": float(line["variance_pct"]),
                "owner": line["owner"],
                "stated_cause": cause,
                "cause_confidence": answers.confidence(f"cause_{index}"),
                "recurring": recurring,
                "controllable": controllable,
                "action_committed": answers.noul(f"action_{index}"),
                "specificity": answers.score(f"specificity_{index}"),
                "next_step": action(cause, recurring, controllable, recurring_gate, control_gate),
                "note": line["note"],
            }
        )

    out = pd.DataFrame(rows)
    permanent = out[out["recurring"] >= recurring_gate]
    out["forecast_impact"] = [
        run_rate_impact(v, periods_remaining) if r >= recurring_gate else 0.0
        for v, r in zip(out["variance"], out["recurring"])
    ]

    result_columns = st.columns(4)
    result_columns[0].metric("Material variances", len(out))
    result_columns[1].metric("Judged permanent", len(permanent))
    result_columns[2].metric("Forecast impact", f"{out['forecast_impact'].sum():,.0f}")
    result_columns[3].metric("Unexplained", int((out["stated_cause"] == NO_CAUSE).sum()))

    st.subheader("Classified variances")
    st.dataframe(
        out[["line_id", "department", "line_item", "variance", "variance_pct", "stated_cause",
             "cause_confidence", "recurring", "controllable", "action_committed", "next_step"]]
        .sort_values("variance", key=abs, ascending=False),
        width="stretch", hide_index=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Variance by stated cause")
        st.caption("The model labelled causes. pandas added them up. Never the other way round.")
        st.bar_chart(out.groupby("stated_cause")["variance"].sum())
    with right:
        st.subheader("Permanent versus timing")
        st.caption(
            "This split is the one finance actually needs, and it is invisible in the "
            "numbers alone: a 138k events overspend that is Q3 landing early and a 42k "
            "energy overspend that repeats forever look identical in a variance column."
        )
        st.dataframe(
            out[["line_id", "line_item", "variance", "recurring", "forecast_impact"]]
            .sort_values("forecast_impact", key=abs, ascending=False),
            width="stretch", hide_index=True,
        )

    st.subheader("Needs a conversation")
    st.dataframe(
        out[out["next_step"].isin({"explanation_required", "challenge_owner", "reforecast_and_challenge"})][
            ["line_id", "department", "line_item", "variance", "next_step", "specificity", "owner", "note"]
        ],
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download variance analysis CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="budget_variance.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {len(questions)} questions over {len(records)} material lines "
        f"(of {len(frame)} total), {answers.input_tokens:,} input tokens, "
        f"${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s."
    )


if __name__ == "__main__":
    main()
