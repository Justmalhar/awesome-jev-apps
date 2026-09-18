"""Grant Fit -- which funding calls actually fit this proposal.

A PI with a half-written proposal faces a catalog of open calls: UKRI, Horizon,
NIH, three charities, two internal schemes. Each has a remit paragraph, an
eligibility paragraph and a deadline. Reading all of them properly takes a day,
so in practice nobody does -- people apply to the call they heard about, and the
one that would actually have funded them closes unnoticed.

The shape is relevance ranking over a catalog: one proposal, N calls, all scored
against the same state in ONE request. Nothing here is sequential, so nothing
here needs more than one round trip.

Three judgments per call, each with the primitive it deserves:

  * fit          -- a Score. Topical alignment is genuinely ordered, from
                    "outside every area named" to "aims, methods and outputs
                    all match". A Score also lands BETWEEN levels, which is what
                    ranking needs.
  * eligible     -- a Noul. Binary and independent of fit.
  * disqualified -- a Noul. Also binary, also independent, and a VETO: a perfect
                    topical match to a call you cannot apply to is worth zero,
                    not "worth a lot, slightly discounted".

Dates are handled in Python. `jev-1.13` cannot compare dates and is never asked
to -- deadlines are filtered with `datetime.date` before a single question is
built.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE_CALLS = HERE / "sample_calls.json"
SAMPLE_PROPOSAL = HERE / "sample_proposal.json"

# Ordered degrees of one property, each a situation a PI would recognise.
FIT_LEVELS = [
    "The proposal's subject matter falls outside every research area this call names.",
    "The proposal touches on an area the call names, but its central aim sits somewhere else entirely.",
    "The proposal's central aim sits inside the call's named areas, though its methods or intended outputs differ from what the call emphasises.",
    "The proposal's aims, methods and intended outputs all match what this call asks funded projects to deliver.",
]


def open_calls(calls: list[dict], today: date, min_lead_days: int) -> tuple[list[dict], list[dict]]:
    """Split the catalog on the deadline, in Python. The model never sees a date.

    A call you cannot physically write in time is not a ranking problem, it is
    a filter -- and filtering first also keeps irrelevant state out of the
    request, which `jev-1.13` is measurably sensitive to.
    """
    live, closed = [], []
    for call in calls:
        try:
            deadline = date.fromisoformat(call["deadline"])
        except (KeyError, ValueError):
            live.append(call)  # undated calls stay in; a human decides
            continue
        (live if (deadline - today).days >= min_lead_days else closed).append(call)
    return live, closed


def build_questions(proposal: dict, calls: list[dict]) -> dict:
    """One Score and two Nouls per call, all against one proposal, in one request."""
    questions: dict[str, dict] = {}
    proposal_ref = {
        "title": "`proposal.title`",
        "summary": "`proposal.summary`",
        "methods": "`proposal.methods`",
        "outputs": "`proposal.outputs`",
        "applicant": "`proposal.applicant`",
    }
    for index in range(len(calls)):
        call_ref = {
            "funder": f"`calls[{index}].funder`",
            "name": f"`calls[{index}].name`",
            "remit": f"`calls[{index}].remit`",
            "eligibility": f"`calls[{index}].eligibility`",
        }
        questions[f"fit_{index}"] = score(
            {
                "task": "How well does the proposal match what this funding call is asking for?",
                "proposal": proposal_ref,
                "call": call_ref,
            },
            FIT_LEVELS,
        )
        questions[f"eligible_{index}"] = noul(
            {
                "task": "Does the applicant described in the proposal satisfy the eligibility this call states?",
                "proposal": proposal_ref,
                "call": call_ref,
            },
            true="The applicant's role, institution and career stage are all of a kind the call says it accepts",
            false="The call states an eligibility requirement the applicant does not meet, such as a different institution type, career stage or country",
        )
        questions[f"disqualified_{index}"] = noul(
            {
                "task": "Does this call state a structural requirement or exclusion that this proposal fails outright?",
                "proposal": proposal_ref,
                "call": call_ref,
            },
            true="The call requires something the proposal plainly lacks, such as an industry partner, a multi-country consortium, clinical involvement, or work in an area the call excludes",
            false="No stated requirement or exclusion of the call is plainly failed by this proposal",
        )
    return questions


def triage(
    assessments: list[dict],
    eligible_at: float,
    disqualify_at: float,
    fit_floor: float,
) -> tuple[list[dict], list[dict]]:
    """Veto first, then rank. A perfect topical match you cannot apply to is out.

    `assessments` items carry: index, fit, eligible, disqualified. Returns the
    shortlist (best fit first) and the rejects, each carrying why.
    """
    shortlist, rejected = [], []
    for item in assessments:
        record = dict(item)
        if item["disqualified"] >= disqualify_at:
            record["reason"] = f"fails a stated requirement of the call (P={item['disqualified']:.2f})"
            rejected.append(record)
        elif item["eligible"] <= eligible_at:
            record["reason"] = f"applicant is not eligible (P(eligible)={item['eligible']:.2f})"
            rejected.append(record)
        elif item["fit"] < fit_floor:
            record["reason"] = f"topical fit {item['fit']:.2f} is below the floor"
            rejected.append(record)
        else:
            record["reason"] = f"eligible, fit {item['fit']:.2f}"
            shortlist.append(record)
    shortlist.sort(key=lambda r: r["fit"], reverse=True)
    rejected.sort(key=lambda r: r["fit"], reverse=True)
    return shortlist, rejected


def main() -> None:
    st.set_page_config(page_title="Grant Fit", page_icon="💰", layout="wide")
    st.title("💰 Grant Fit")
    st.caption("Rank a live catalog of funding calls against one proposal, in one request.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Policy")
        min_lead_days = st.slider("Minimum working days before the deadline", 0, 180, 30, 5)
        eligible_at = st.slider("Reject below this P(eligible)", 0.05, 0.8, 0.40, 0.01)
        disqualify_at = st.slider("Veto at or above this P(fails a requirement)", 0.3, 0.95, 0.60, 0.01)
        fit_floor = st.slider("Minimum fit to shortlist", 0.0, 3.0, 1.5, 0.1)
        st.caption("All four are Python-side. Re-ranking re-runs no inference.")

    proposal_text = st.text_area(
        "Proposal (JSON)",
        value=SAMPLE_PROPOSAL.read_text(encoding="utf-8"),
        height=260,
    )
    try:
        proposal = json.loads(proposal_text)
        assert proposal.get("summary")
    except (json.JSONDecodeError, AssertionError) as exc:
        st.error(f"Proposal must be JSON with at least a 'summary': {exc}")
        return

    uploaded = st.file_uploader("Call catalog (JSON list)", type=["json"])
    calls = (
        json.loads(uploaded.read().decode("utf-8"))
        if uploaded
        else json.loads(SAMPLE_CALLS.read_text(encoding="utf-8"))
    )

    today = date.today()
    live, closed = open_calls(calls, today, min_lead_days)
    st.caption(
        f"{len(calls)} calls in the catalog; {len(closed)} filtered out in Python on the "
        f"deadline (today is {today.isoformat()}); {len(live)} assessed."
    )
    if not live:
        st.warning("Every call is past the lead-time filter. Lower it in the sidebar.")
        return

    if not st.button(f"Rank {len(live)} calls", type="primary"):
        return

    questions = build_questions(proposal, live)
    try:
        with st.spinner(f"{len(questions)} judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"proposal": proposal, "calls": live}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    assessments = [
        {
            "index": index,
            "call": live[index],
            "fit": answers.score(f"fit_{index}"),
            "confidence": answers.confidence(f"fit_{index}"),
            "eligible": answers.noul(f"eligible_{index}"),
            "disqualified": answers.noul(f"disqualified_{index}"),
        }
        for index in range(len(live))
    ]
    shortlist, rejected = triage(assessments, eligible_at, disqualify_at, fit_floor)

    columns = st.columns(3)
    columns[0].metric("Worth writing", len(shortlist))
    columns[1].metric("Ruled out", len(rejected))
    columns[2].metric("Closed or too soon", len(closed))

    def table(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "funder": r["call"]["funder"],
                    "call": r["call"]["name"],
                    "deadline": r["call"].get("deadline", "—"),
                    "fit": round(r["fit"], 2),
                    "confidence": round(r["confidence"], 2),
                    "P(eligible)": round(r["eligible"], 2),
                    "why": r["reason"],
                }
                for r in rows
            ]
        )

    st.subheader("Shortlist")
    if shortlist:
        st.dataframe(table(shortlist), width="stretch", hide_index=True)
        best = shortlist[0]
        level = min(int(best["fit"]), len(FIT_LEVELS) - 1)
        st.caption(f"Top match sits near: *{FIT_LEVELS[level]}*")
    else:
        st.info("Nothing cleared the veto and the fit floor. Loosen the sliders or widen the catalog.")

    st.subheader("Ruled out")
    if rejected:
        st.dataframe(table(rejected), width="stretch", hide_index=True)
        st.caption(
            "Note the calls with high fit in this table: those are the ones a keyword search "
            "would have put at the top and a week would have been wasted on."
        )

    if closed:
        st.subheader("Filtered on the deadline (no inference spent)")
        st.dataframe(
            pd.DataFrame(
                [{"funder": c["funder"], "call": c["name"], "deadline": c.get("deadline", "—")} for c in closed]
            ),
            width="stretch",
            hide_index=True,
        )

    for row in shortlist + rejected:
        with st.expander(f"{row['call']['funder']} — {row['call']['name']} (fit {row['fit']:.2f})"):
            st.markdown(f"**Remit** — {row['call']['remit']}")
            st.markdown(f"**Eligibility** — {row['call']['eligibility']}")
            st.caption(
                f"fit {row['fit']:.2f} (confidence {row['confidence']:.2f}) · "
                f"P(eligible) {row['eligible']:.2f} · P(fails a requirement) {row['disqualified']:.2f}"
            )

    st.divider()
    st.caption(
        f"{len(live)} calls × 3 judgments = {len(questions)} questions, 1 request, "
        f"{answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
