"""Renewal Risk -- which accounts are drifting, composed from raw judgments.

The composite risk score in this app is never asked for. It is built in Python
out of judgments that each mean something on their own:

    risk = Σ (weight × normalised evidence) , with vetoes that bypass the sum

That matters because the weights are the part that is wrong. Every CS team
argues about whether a departed champion outranks a support backlog, and the
answer differs by segment. If the weighting is baked into a prompt, every
argument costs a re-run of the whole book. Here it is a slider, and the raw
judgments stay valid: they are evidence, and evidence does not change when your
opinion of it does.

Same judgments, three consumers: the risk ranking, the "why" shown next to each
account, and the CSV your data team joins to product usage.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_accounts.json"

# Graded evidence. Each rubric level is a situation someone could recognise.
DIMENSIONS = {
    "adoption": {
        "label": "Depth of adoption",
        "weight": 1.0,
        "levels": [
            "The account is live across the teams it was bought for and uses it daily",
            "One team uses it properly and the planned rollout never reached the others",
            "A handful of people log in occasionally for a narrow task",
            "The notes describe the product as effectively unused",
        ],
    },
    "value_story": {
        "label": "Can they articulate the value",
        "weight": 1.0,
        "levels": [
            "They describe a specific outcome in their own numbers or their own words",
            "They believe it helps but cannot point to anything concrete",
            "They are unsure what it has changed since they bought it",
            "They state plainly that it has not delivered what they expected",
        ],
    },
    "relationship": {
        "label": "Strength of the relationship",
        "weight": 0.8,
        "levels": [
            "An engaged sponsor takes our calls and brings colleagues along",
            "A working contact engages when there is something to do",
            "Contact is sporadic and mostly initiated by us",
            "Nobody on their side reliably responds to us",
        ],
    },
    "support_burden": {
        "label": "How much friction they are living with",
        "weight": 0.7,
        "levels": [
            "Issues are rare and resolved without drama",
            "Ordinary issues arrive and get closed at a normal pace",
            "The same problems recur and the customer has noticed the pattern",
            "Unresolved problems are actively shaping how they talk about us",
        ],
    },
    "competitive_pressure": {
        "label": "Pressure from alternatives",
        "weight": 0.9,
        "levels": [
            "No alternative is under consideration",
            "An alternative was mentioned in passing without intent",
            "They are actively comparing us with a named alternative",
            "A formal evaluation, tender, or replacement project is underway",
        ],
    },
}

# Vetoes: independent, each escalates on its own, so never levels on a rubric.
FLAGS = {
    "champion_departed": (
        "Do the notes indicate the person who sponsored this purchase has left the company or changed role?",
        "Their sponsor is described as having left, moved teams, or handed the account over",
        "The original sponsor is still in place",
    ),
    "exit_enquiry": (
        "Do the notes record the customer asking about cancelling, notice periods, or exporting their data?",
        "They ask about cancellation, notice, contract exit, or getting their data out",
        "No question about leaving is recorded",
    ),
    "budget_review": (
        "Do the notes record this spend being formally questioned by finance or procurement?",
        "A cost review, procurement challenge, or demand to justify the spend is described",
        "The spend is not described as being challenged",
    ),
    "exec_dissatisfaction": (
        "Do the notes record an executive on the customer side expressing dissatisfaction?",
        "A VP, director, or C-level person is described as unhappy or questioning the tool",
        "No executive-level dissatisfaction is recorded",
    ),
    "expansion_in_flight": (
        "Do the notes describe the customer wanting more seats, volume, or teams onboarded?",
        "Growth in their own usage is being actively discussed",
        "No growth in usage is being discussed",
    ),
}


def build_questions(accounts: list[dict]) -> dict:
    """Five graded rubrics and five flags per account, in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(accounts)):
        reference = {
            "account": f"`accounts[{index}].name`",
            "segment": f"`accounts[{index}].segment`",
            "notes": f"`accounts[{index}].notes`",
        }
        for key, spec in DIMENSIONS.items():
            questions[f"{key}__{index}"] = score(
                {"task": spec["label"], "account": reference}, spec["levels"]
            )
        for key, (task, yes, no) in FLAGS.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "account": reference}, true=yes, false=no
            )
    return questions


def composite(judgment: dict, weights: dict[str, float]) -> float:
    """0 = healthy, 100 = every rubric at its worst level. Pure arithmetic, ours."""
    weight_sum = sum(weights.values()) or 1.0
    raw = sum(
        weights[key] * (judgment[key] / (len(DIMENSIONS[key]["levels"]) - 1))
        for key in DIMENSIONS
    )
    return 100.0 * raw / weight_sum


def band(
    judgment: dict, weights: dict[str, float], flag_gate: float, red: float, amber: float
) -> tuple[str, float, str]:
    """Composite plus vetoes. Expansion intent can pull an account back one band."""
    total = composite(judgment, weights)
    fired = [k for k in FLAGS if k != "expansion_in_flight" and judgment[k] >= flag_gate]
    if len(fired) >= 2:
        return "red", total, f"two or more hard flags: {', '.join(fired)}"
    if total >= red:
        return "red", total, f"risk {total:.0f}"
    if total >= amber or fired:
        reason = f"risk {total:.0f}" if total >= amber else f"flag: {fired[0]}"
        if judgment["expansion_in_flight"] >= flag_gate and total < red:
            return "green", total, reason + ", offset by expansion in flight"
        return "amber", total, reason
    return "green", total, f"risk {total:.0f}"


def portfolio(frame: pd.DataFrame) -> pd.DataFrame:
    """Revenue at risk per band. Sums belong in pandas, never in a question."""
    table = frame.groupby("band").agg(
        accounts=("account", "size"),
        arr=("arr", "sum"),
        mean_risk=("risk", "mean"),
    )
    table["arr"] = table["arr"].round(0)
    table["mean_risk"] = table["mean_risk"].round(1)
    return table.reindex(["red", "amber", "green"]).dropna(how="all")


def main() -> None:
    st.set_page_config(page_title="Renewal Risk", page_icon="🔄", layout="wide")
    st.title("🔄 Renewal Risk")
    st.caption("Composite risk built in code from raw, reusable judgments.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Weights")
        st.caption("The scoring model is these sliders. Changing it costs nothing.")
        weights = {
            key: st.slider(spec["label"], 0.0, 2.0, spec["weight"], 0.1)
            for key, spec in DIMENSIONS.items()
        }
        st.divider()
        st.subheader("Bands")
        red = st.slider("Red at risk ≥", 20, 100, 60, 1)
        amber = st.slider("Amber at risk ≥", 0, 90, 35, 1)
        flag_gate = st.slider("Hard flag fires at P(yes) ≥", 0.3, 0.95, 0.60, 0.05)

    upload = st.file_uploader(
        "Accounts (JSON list of {name, segment, arr, renewal_quarter, notes})", type=["json"]
    )
    accounts = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Assess {len(accounts)} accounts", type="primary"):
        questions = build_questions(accounts)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"accounts": accounts}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["assessed"] = {
            "accounts": accounts,
            "judgments": [
                {
                    **{k: answers.score(f"{k}__{i}") for k in DIMENSIONS},
                    **{k: answers.noul(f"{k}__{i}") for k in FLAGS},
                }
                for i in range(len(accounts))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    assessed = st.session_state.get("assessed")
    if not assessed:
        st.info(f"{len(accounts)} sample accounts bundled. Hit the button.")
        return

    rows = []
    for account, judgment in zip(assessed["accounts"], assessed["judgments"]):
        verdict, total, reason = band(judgment, weights, flag_gate, red, amber)
        rows.append(
            {
                "account": account["name"],
                "band": verdict,
                "risk": round(total, 1),
                "arr": account.get("arr", 0),
                "renews": account.get("renewal_quarter", ""),
                "segment": account.get("segment", ""),
                "why": reason,
                **{k: round(judgment[k], 2) for k in DIMENSIONS},
                **{k: round(judgment[k], 2) for k in FLAGS},
            }
        )
    frame = pd.DataFrame(rows).sort_values("risk", ascending=False)

    columns = st.columns(4)
    for column, verdict in zip(columns, ["red", "amber", "green"]):
        subset = frame[frame["band"] == verdict]
        column.metric(verdict.title(), len(subset), f"${subset['arr'].sum():,.0f} ARR")
    columns[3].metric("Book ARR", f"${frame['arr'].sum():,.0f}")

    st.subheader("Portfolio")
    st.dataframe(portfolio(frame), width="stretch")

    st.subheader("Accounts")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download renewal_risk.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="renewal_risk.csv",
        mime="text/csv",
    )

    st.subheader("What is driving risk across the book")
    st.bar_chart(frame[list(DIMENSIONS)].mean().sort_values(ascending=False))

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", assessed["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{assessed['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${assessed['cost']:.6f}")
    st.caption(
        f"{assessed['tokens']:,} input tokens for {len(frame)} accounts × "
        f"{len(DIMENSIONS) + len(FLAGS)} judgments. Every weight and band change "
        "above rebuilt the model for $0.00."
    )


if __name__ == "__main__":
    main()
