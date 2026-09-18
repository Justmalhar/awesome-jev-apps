"""Sales Call Tagger -- objections, competitors and next steps from call notes.

Three kinds of signal come out of a discovery call and they behave differently:

  * Objections are independent -- a prospect can raise price AND security AND
    a missing integration in the same call. Separate Nouls, one per objection.
  * Competitors are independent -- "we're also looking at X and Y". Separate
    Nouls, one per name in your battlecard list, generated from config.
  * The next step is exclusive -- there is one agreed next action, or none.
    That is a Choice, and it needs a no-match option because plenty of calls
    end with nothing agreed.

Collapsing objections into "primary objection" is the mistake this app exists
to avoid: it throws away everything after the first one, which is where the
pattern across a quarter of calls lives.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_calls.json"

OBJECTIONS = {
    "price": (
        "Did the prospect raise cost, budget, or value for money as a concern?",
        "They say it is expensive, ask for a discount, or question the value at this price",
        "Cost is not raised as a concern",
    ),
    "timing": (
        "Did the prospect say this is the wrong moment to buy?",
        "They point to a freeze, a reorganisation, a busy period, or another project taking priority",
        "No timing obstacle is raised",
    ),
    "incumbent_satisfaction": (
        "Did the prospect indicate they are content with what they already use?",
        "They say their current tool is adequate, or that switching is not worth the disruption",
        "They do not defend their current solution",
    ),
    "security_compliance": (
        "Did the prospect raise security, privacy, data residency, or compliance as a blocker?",
        "They mention a security review, a certification, data location, or a regulator",
        "No security or compliance concern is raised",
    ),
    "authority": (
        "Did the prospect indicate they cannot make this decision themselves?",
        "They refer the decision to a committee, a manager, procurement, or another team",
        "They speak as someone who can decide",
    ),
    "integration_gap": (
        "Did the prospect say the product does not connect to something they need?",
        "They name a system we do not integrate with, or an API limitation that blocks them",
        "No integration obstacle is raised",
    ),
    "switching_cost": (
        "Did the prospect raise the effort of migrating or retraining as an obstacle?",
        "They mention migration work, retraining, or historical data that would have to move",
        "No migration or retraining concern is raised",
    ),
    "trust_in_vendor": (
        "Did the prospect question our company's size, stability, or track record?",
        "They ask about funding, longevity, references, or whether we are big enough",
        "No doubt about our company is expressed",
    ),
}

DEFAULT_COMPETITORS = ["Datadome", "Riverbank", "Northstar Suite", "an in-house build", "Excel or spreadsheets"]

NEXT_STEPS = {
    "demo_booked": "A demo or technical session is agreed, with a person or a date named",
    "pilot_agreed": "A trial, proof of concept, or pilot is agreed",
    "proposal_requested": "They asked for pricing, a quote, or a written proposal",
    "intro_to_stakeholder": "They agreed to bring in a colleague, a manager, or another team",
    "security_review_started": "They agreed to begin a security, legal, or procurement review",
    "customer_will_revert": "The next move is theirs with nothing specific committed by us",
    "no_next_step": "The call ended without any next action being agreed",
}

BUYING_SIGNALS = {
    "budget_confirmed": (
        "Did the prospect indicate that money is available for this?",
        "They state a budget exists, name a figure, or say it is funded",
        "Funding is not confirmed",
    ),
    "problem_owned": (
        "Did the prospect describe a problem they personally own?",
        "They describe a pain that lands on their team, in their words",
        "The problem is described abstractly or as somebody else's",
    ),
    "champion_language": (
        "Did the prospect speak as an advocate for moving forward internally?",
        "They offer to push it internally, build a case, or bring others along",
        "They remain a neutral evaluator",
    ),
}


def build_questions(calls: list[dict], competitors: list[str]) -> dict:
    """Independent Nouls per signal, one exclusive Choice for the next step."""
    questions: dict[str, dict] = {}
    for index in range(len(calls)):
        reference = {
            "account": f"`calls[{index}].account`",
            "notes": f"`calls[{index}].notes`",
        }
        for key, (task, yes, no) in {**OBJECTIONS, **BUYING_SIGNALS}.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "call": reference}, true=yes, false=no
            )
        for position, competitor in enumerate(competitors):
            questions[f"comp{position}__{index}"] = noul(
                {
                    "task": "Did the prospect mention this alternative during the call?",
                    "alternative": competitor,
                    "call": reference,
                },
                true="This alternative is named as something they use or are considering",
                false="This alternative is not mentioned",
            )
        questions[f"next__{index}"] = choice(
            {"task": "What next step was agreed on this call?", "call": reference}, NEXT_STEPS
        )
    return questions


STRONG_NEXT_STEPS = {"demo_booked", "pilot_agreed", "proposal_requested", "security_review_started"}


def qualify(judgment: dict, fire_at: float) -> str:
    """Policy in Python: a call with a real next step and an owned problem is live."""
    strong = judgment["next_step"] in STRONG_NEXT_STEPS
    owned = judgment["problem_owned"] >= fire_at
    blocked = judgment["timing"] >= fire_at or judgment["incumbent_satisfaction"] >= fire_at
    if strong and owned and not blocked:
        return "advancing"
    if judgment["next_step"] == "no_next_step" and blocked:
        return "stalled"
    return "working"


def objection_matrix(frame: pd.DataFrame, keys: list[str], fire_at: float) -> pd.DataFrame:
    """Which objections travel together. pandas, not the model."""
    fired = frame[keys].ge(fire_at)
    return fired.astype(int).T.dot(fired.astype(int))


def main() -> None:
    st.set_page_config(page_title="Sales Call Tagger", page_icon="📞", layout="wide")
    st.title("📞 Sales Call Tagger")
    st.caption("Objections, competitors and next steps — every signal independently tagged.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Tagging threshold")
        fire_at = st.slider("Tag applies at P(yes) ≥", 0.3, 0.95, 0.60, 0.05)
        st.caption("Re-tagging from stored probabilities costs nothing.")
        st.divider()
        competitor_text = st.text_area(
            "Battlecard competitors (one per line)", value="\n".join(DEFAULT_COMPETITORS), height=140
        )
    competitors = [line.strip() for line in competitor_text.splitlines() if line.strip()]

    upload = st.file_uploader("Call notes (JSON list of {id, account, rep, notes})", type=["json"])
    calls = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    per_call = len(OBJECTIONS) + len(BUYING_SIGNALS) + len(competitors) + 1
    st.caption(f"{len(calls)} calls × {per_call} judgments = {len(calls) * per_call} questions, one request.")

    if st.button("Tag the calls", type="primary"):
        questions = build_questions(calls, competitors)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"calls": calls}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["tagged"] = {
            "calls": calls,
            "competitors": competitors,
            "judgments": [
                {
                    **{k: answers.noul(f"{k}__{i}") for k in {**OBJECTIONS, **BUYING_SIGNALS}},
                    **{c: answers.noul(f"comp{p}__{i}") for p, c in enumerate(competitors)},
                    "next_step": answers.choice(f"next__{i}"),
                    "next_conf": answers.confidence(f"next__{i}"),
                }
                for i in range(len(calls))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    tagged = st.session_state.get("tagged")
    if not tagged:
        st.info(f"{len(calls)} sample call notes bundled. Hit the button.")
        return

    names = tagged["competitors"]
    frame = pd.DataFrame(
        [
            {
                "id": call["id"],
                "account": call["account"],
                "rep": call.get("rep", ""),
                "state": qualify(judgment, fire_at),
                "next step": judgment["next_step"],
                **{k: round(judgment[k], 2) for k in OBJECTIONS},
                **{k: round(judgment[k], 2) for k in BUYING_SIGNALS},
                **{c: round(judgment[c], 2) for c in names},
            }
            for call, judgment in zip(tagged["calls"], tagged["judgments"])
        ]
    )

    columns = st.columns(4)
    for column, state in zip(columns, ["advancing", "working", "stalled"]):
        column.metric(state.title(), int((frame["state"] == state).sum()))
    columns[3].metric("Objections per call", f"{frame[list(OBJECTIONS)].ge(fire_at).sum(axis=1).mean():.2f}")

    st.subheader("Objection frequency")
    st.bar_chart(frame[list(OBJECTIONS)].ge(fire_at).mean().sort_values(ascending=False))

    if names:
        st.subheader("Competitors named")
        st.bar_chart(frame[names].ge(fire_at).sum().sort_values(ascending=False))

    st.subheader("Next steps agreed")
    st.dataframe(
        frame.groupby("next step").size().rename("calls").reset_index(),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Which objections travel together")
    st.caption("Diagonal is how often each fired; off-diagonal is co-occurrence.")
    st.dataframe(objection_matrix(frame, list(OBJECTIONS), fire_at), width="stretch")

    st.subheader("Per call")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download tagged_calls.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="tagged_calls.csv",
        mime="text/csv",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", tagged["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{tagged['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${tagged['cost']:.6f}")
    st.caption(
        f"{tagged['tokens']:,} input tokens for {len(frame)} calls. "
        "Changing the threshold or the rollups above costs $0.00."
    )


if __name__ == "__main__":
    main()
