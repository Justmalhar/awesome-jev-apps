"""Churn Signals -- extract early-warning FEATURES from customer conversations.

This app deliberately does not tell you who will churn. It extracts a fixed
vector of independent, reusable signals per conversation and hands you the
matrix. What you do with it -- feed a logistic regression, trigger a play, sort
a CSM's morning -- is your problem, and that is the point.

Asking a model "will this account churn?" produces a number nobody can act on,
that nobody can audit, and that cannot be joined to anything. Asking it fifteen
concrete yes/no questions produces columns. Columns compose with the usage data,
the ticket data and the billing data you already have; a verdict does not.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_conversations.json"

# Each signal is one independent Noul. They can all be true at once, so they are
# never options on a Choice and never levels on a rubric.
SIGNALS = {
    "competitor_named": (
        "Does the customer mention evaluating, piloting, or talking to another vendor?",
        "They name a competitor, a bake-off, an RFP, or say they are looking at alternatives",
        "No other vendor or evaluation is mentioned",
    ),
    "champion_gone": (
        "Does the conversation indicate that the person who sponsored or drove this product internally has left or changed role?",
        "Someone says their sponsor left the company, moved teams, or handed the account over",
        "No change of internal sponsor is described",
    ),
    "repeat_unresolved": (
        "Does the customer say they have raised this same problem before without it being resolved?",
        "They reference a previous ticket, an earlier conversation, or say this keeps happening",
        "This reads as the first time they have raised this problem",
    ),
    "exit_mechanics": (
        "Does the customer ask about cancelling, exporting their data, contract terms, or notice periods?",
        "They ask how to cancel, how to get their data out, or what the exit terms are",
        "No question about leaving, exporting, or contract exit is raised",
    ),
    "usage_declining": (
        "Does the customer describe using the product less than they used to?",
        "They say a team stopped using it, a workflow moved elsewhere, or seats are sitting idle",
        "No reduction in their own usage is described",
    ),
    "budget_pressure": (
        "Does the customer describe cost pressure on this spend specifically?",
        "They mention budget cuts, procurement review, a price increase they cannot absorb, or being asked to justify the spend",
        "No cost pressure on this spend is described",
    ),
    "blocking_gap": (
        "Does the customer describe a missing capability that is blocking them from getting value?",
        "They name something the product cannot do that is stopping their work",
        "No blocking capability gap is described",
    ),
    "escalated_internally": (
        "Has this been raised to management on the customer's side?",
        "They mention their VP, director, CFO, or leadership being involved or asking about it",
        "Only the day-to-day contact is involved",
    ),
    "onboarding_stalled": (
        "Does the conversation indicate the customer never finished getting set up?",
        "They describe an implementation that stalled, a rollout that never happened, or still being on a pilot",
        "They describe themselves as up and running",
    ),
    "expansion_intent": (
        "Does the customer describe wanting more of the product?",
        "They ask about more seats, more volume, another team adopting it, or a higher plan",
        "No growth in their own usage is described",
    ),
    "advocacy": (
        "Does the customer say something positive about the product or the team unprompted?",
        "They praise the product, a feature, or a person, outside of ordinary politeness",
        "Nothing positive beyond ordinary politeness is expressed",
    ),
}

# Two graded features, because tone and trust are degrees, not yes/no.
GRADED = {
    "tone": [
        "The customer is warm, patient, and collaborative throughout",
        "The customer is businesslike with no visible frustration",
        "The customer is visibly frustrated about a specific issue",
        "The customer is hostile, or expresses that their patience has run out",
    ],
    "trust": [
        "The customer treats our statements as reliable and plans around them",
        "The customer asks for extra confirmation before believing a commitment",
        "The customer cites a promise that was not kept",
        "The customer states plainly that they no longer believe what we tell them",
    ],
}


def build_questions(conversations: list[dict]) -> dict:
    """One Noul per signal per conversation, plus two graded features. One request."""
    questions: dict[str, dict] = {}
    for index in range(len(conversations)):
        reference = {
            "channel": f"`conversations[{index}].channel`",
            "transcript": f"`conversations[{index}].text`",
        }
        for key, (task, yes, no) in SIGNALS.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "conversation": reference}, true=yes, false=no
            )
        for key, levels in GRADED.items():
            questions[f"{key}__{index}"] = score(
                {"task": f"Rate the customer's {key} in this conversation.", "conversation": reference},
                levels,
            )
    return questions


def to_frame(conversations: list[dict], judgments: list[dict]) -> pd.DataFrame:
    """The deliverable: a feature matrix, one row per conversation."""
    rows = []
    for conversation, judgment in zip(conversations, judgments):
        rows.append(
            {
                "account": conversation["account"],
                "channel": conversation["channel"],
                "date": conversation.get("date", ""),
                **{key: round(judgment[key], 3) for key in SIGNALS},
                **{key: round(judgment[key], 3) for key in GRADED},
            }
        )
    return pd.DataFrame(rows)


def account_rollup(frame: pd.DataFrame, fire_at: float) -> pd.DataFrame:
    """Aggregate in pandas, never in the model. Max over conversations per account."""
    feature_columns = list(SIGNALS) + list(GRADED)
    rollup = frame.groupby("account")[feature_columns].max().round(3)
    fired = (frame[list(SIGNALS)] >= fire_at).groupby(frame["account"]).max()
    rollup.insert(0, "signals fired", fired.sum(axis=1).astype(int))
    rollup.insert(1, "conversations", frame.groupby("account").size())
    return rollup.sort_values("signals fired", ascending=False)


def main() -> None:
    st.set_page_config(page_title="Churn Signals", page_icon="📉", layout="wide")
    st.title("📉 Churn Signals")
    st.caption("Features, not a verdict. Thirteen reusable columns per conversation.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Display threshold")
        st.caption(
            "This only decides what counts as 'fired' in the rollup below. "
            "The exported features are the raw probabilities."
        )
        fire_at = st.slider("Signal fired at P(yes) ≥", 0.3, 0.95, 0.60, 0.05)

    st.info(
        "**This app does not predict churn.** It produces a feature matrix. "
        "Fit your own model on it, or join it to usage and billing data — a "
        "single model-generated 'churn score' cannot be joined to anything.",
        icon="ℹ️",
    )

    upload = st.file_uploader(
        "Conversations (JSON list of {account, channel, date, text})", type=["json"]
    )
    conversations = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Extract features from {len(conversations)} conversations", type="primary"):
        questions = build_questions(conversations)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"conversations": conversations}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["features"] = {
            "conversations": conversations,
            "judgments": [
                {
                    **{key: answers.noul(f"{key}__{i}") for key in SIGNALS},
                    **{key: answers.score(f"{key}__{i}") for key in GRADED},
                }
                for i in range(len(conversations))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    stored = st.session_state.get("features")
    if not stored:
        st.caption(f"Bundled sample: {len(conversations)} conversations across several accounts.")
        return

    frame = to_frame(stored["conversations"], stored["judgments"])

    st.subheader("Feature matrix (one row per conversation)")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download features.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="churn_features.csv",
        mime="text/csv",
    )

    st.subheader("Account rollup")
    st.caption("Max over each account's conversations. All aggregation is pandas.")
    st.dataframe(account_rollup(frame, fire_at), width="stretch")

    st.subheader("How often each signal fires")
    rates = (frame[list(SIGNALS)] >= fire_at).mean().sort_values(ascending=False)
    st.bar_chart(rates)

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", stored["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{stored['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${stored['cost']:.6f}")
    st.caption(
        f"{stored['tokens']:,} input tokens → {len(frame)} × {len(SIGNALS) + len(GRADED)} "
        "feature matrix. Changing the threshold above recomputed everything for $0.00."
    )


if __name__ == "__main__":
    main()
