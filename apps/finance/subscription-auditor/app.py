"""Subscription Auditor -- find every recurring charge in a statement,
including the ones you forgot you were paying for.

The division of labour is the whole point:

  pandas   normalises merchant names, groups the charges, measures the gap
           between them, and computes every pound on screen.
  Jev      answers the one thing pandas cannot: *is this pattern a subscription,
           and does it look abandoned?*

A regular 30-day gap is evidence, not proof -- a weekly shop is regular too, and
an annual domain renewal is a subscription with two data points. Regularity is
arithmetic; "is this a subscription" is judgment. So the measured cadence is
handed to the model as state, and the model is never asked to derive it.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_transactions.csv"

KINDS = {
    "streaming": "Video, music, audiobook or news streaming.",
    "software": "Software, cloud storage, hosting, domains, developer or AI tooling.",
    "fitness": "Gym, studio, class pass, fitness or wellbeing app.",
    "insurance": "Insurance or warranty premium paid on a recurring basis.",
    "membership": "Club, association, charity, loyalty or professional membership.",
    "utility_contract": "Mobile, broadband, energy or another household service contract.",
    "not_a_subscription": "Repeat spending at the same merchant that is not a subscription at all, such as a weekly grocery shop or a regular commute fare.",
}

# Levels are the operator's three real situations, not adjectives.
CANCEL_LEVELS = [
    "Cancelling is self-service: a button in the account settings ends it immediately.",
    "Cancelling needs a form, an email, or notice given before the next billing date.",
    "Cancelling is contractually locked: a minimum term, an exit fee, or a phone-only retention process.",
]

MERCHANT_NOISE = re.compile(r"[0-9]{2,}|#|\*|REF[: ]?\S*|TXN\S*|\bPOS\b|\bDD\b|\bGBP\b|\bUSD\b")


def merchant_key(description: str) -> str:
    """Collapse 'NETFLIX.COM 4471' and 'NETFLIX.COM*8812' onto one merchant.

    Deliberately crude and deliberately in Python: string surgery is not
    judgment, and paying an API to uppercase things would be silly.
    """
    cleaned = MERCHANT_NOISE.sub(" ", str(description).upper())
    cleaned = re.sub(r"[^A-Z. ]", " ", cleaned)
    tokens = [t for t in cleaned.split() if len(t) > 1]
    return " ".join(tokens[:3]) or str(description).upper()[:24]


def group_merchants(frame: pd.DataFrame, min_charges: int) -> pd.DataFrame:
    """Every number in this function is pandas'. The model sees the output, not the workings."""
    spend = frame[frame["amount"] < 0].copy()
    spend["date"] = pd.to_datetime(spend["date"], errors="coerce")
    spend["paid"] = spend["amount"].abs()
    spend["merchant"] = spend["description"].map(merchant_key)
    spend = spend.dropna(subset=["date"]).sort_values("date")

    rows = []
    for merchant, part in spend.groupby("merchant"):
        gaps = part["date"].diff().dt.days.dropna()
        rows.append(
            {
                "merchant": merchant,
                "charges": len(part),
                "typical_amount": round(float(part["paid"].median()), 2),
                "amount_spread": round(float(part["paid"].max() - part["paid"].min()), 2),
                "charged_since": part["date"].min().date().isoformat(),
                "last_charged": part["date"].max().date().isoformat(),
                "typical_gap_days": round(float(gaps.median()), 1) if len(gaps) else None,
                "gap_spread_days": round(float(gaps.max() - gaps.min()), 1) if len(gaps) > 1 else 0.0,
                "paid_so_far": round(float(part["paid"].sum()), 2),
                "descriptions": sorted(part["description"].unique())[:4],
            }
        )
    out = pd.DataFrame(rows)
    return out[out["charges"] >= min_charges].sort_values("paid_so_far", ascending=False).reset_index(drop=True)


def build_questions(merchants: list[dict]) -> dict:
    """Four independent judgments per merchant, all in one request.

    Recurring and abandoned are separate properties -- a gym you still use is
    recurring and not abandoned -- so they are separate Nouls with separate
    sliders, not levels on one rubric.
    """
    questions: dict[str, dict] = {}
    for index in range(len(merchants)):
        reference = {
            "merchant": f"`merchants[{index}].merchant`",
            "statement_lines": f"`merchants[{index}].descriptions`",
            "measured_cadence": f"`merchants[{index}].typical_gap_days`",
            "cadence_variation": f"`merchants[{index}].gap_spread_days`",
            "usual_charge": f"`merchants[{index}].typical_amount`",
            "charge_variation": f"`merchants[{index}].amount_spread`",
        }
        questions[f"recurring_{index}"] = noul(
            {
                "task": "Is this merchant billing the account holder on an ongoing subscription or contract, rather than being somewhere they choose to spend repeatedly?",
                "merchant": reference,
                "note": "The cadence fields are measurements already taken from the statement. Read them; do not re-derive them.",
            },
            true="An automatic ongoing charge the account holder must act to stop: subscription, membership, contract, premium, or licence renewal.",
            false="Repeat discretionary spending they choose each time, such as a weekly food shop, a commute fare, or a favourite coffee shop.",
        )
        questions[f"abandoned_{index}"] = noul(
            {
                "task": "Does this look like a subscription the household has stopped getting value from but is still being billed for?",
                "merchant": reference,
            },
            true="An obscure, single-purpose, trial-shaped or duplicate service of the kind people sign up for once and forget, or one clearly overlapping another service they already pay for.",
            false="A service a household would notice losing straight away, such as their main phone line, home insurance, or the streaming service everyone uses.",
        )
        questions[f"kind_{index}"] = choice(
            {"task": "What kind of recurring commitment is this merchant?", "merchant": reference},
            KINDS,
        )
        questions[f"cancel_{index}"] = score(
            {
                "task": "If the account holder wanted out today, what would ending this commitment involve?",
                "merchant": reference,
            },
            CANCEL_LEVELS,
        )
    return questions


def verdict(recurring: float, abandoned: float, recurring_gate: float, abandoned_gate: float) -> str:
    """Pure policy, in Python, so moving a slider re-derives everything for free."""
    if recurring < recurring_gate:
        return "not_recurring"
    return "cancel_candidate" if abandoned >= abandoned_gate else "active"


def annualise(typical_amount: float, gap_days: float | None) -> float:
    """Annual run-rate, computed here because jev-1.13 cannot multiply."""
    if not gap_days or gap_days <= 0:
        return 0.0
    return round(typical_amount * (365.0 / gap_days), 2)


def load_frame(uploaded) -> pd.DataFrame | None:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()))
    return pd.read_csv(SAMPLE) if SAMPLE.is_file() else None


def main() -> None:
    st.set_page_config(page_title="Subscription Auditor", page_icon="🔁", layout="wide")
    st.title("🔁 Subscription Auditor")
    st.caption("Every recurring charge on the statement, including the forgotten ones.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Thresholds")
        recurring_gate = st.slider("Treat as recurring above P=", 0.0, 1.0, 0.6, 0.05)
        abandoned_gate = st.slider("Flag as forgotten above P=", 0.0, 1.0, 0.55, 0.05)
        min_charges = st.slider("Minimum charges to consider a merchant", 2, 6, 2, 1)
        st.caption("Sliders re-derive the table without a new request.")

    uploaded = st.file_uploader("Statement CSV (needs `date`, `description`, `amount`)", type=["csv"])
    frame = load_frame(uploaded)
    if frame is None:
        st.warning("Upload a CSV, or keep sample_transactions.csv next to this app.")
        return

    missing = {"date", "description", "amount"} - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    merchants = group_merchants(frame, min_charges)
    if uploaded is None:
        st.caption(f"Bundled sample: {len(frame)} transactions collapsing to {len(merchants)} repeat merchants.")
    st.dataframe(merchants.drop(columns=["descriptions"]).head(10), width="stretch")

    if merchants.empty:
        st.info("No merchant charged more than once. Nothing to audit.")
        return

    if not st.button(f"Audit {len(merchants)} repeat merchants", type="primary"):
        return

    records = merchants.to_dict("records")
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"merchants": records}, build_questions(records))
    except JevError as exc:
        st.error(str(exc))
        return

    merchants["recurring"] = [answers.noul(f"recurring_{i}") for i in range(len(records))]
    merchants["forgotten"] = [answers.noul(f"abandoned_{i}") for i in range(len(records))]
    merchants["kind"] = [answers.choice(f"kind_{i}") for i in range(len(records))]
    merchants["kind_confidence"] = [answers.confidence(f"kind_{i}") for i in range(len(records))]
    merchants["exit_friction"] = [answers.score(f"cancel_{i}") for i in range(len(records))]
    merchants["verdict"] = [
        verdict(r, f, recurring_gate, abandoned_gate)
        for r, f in zip(merchants["recurring"], merchants["forgotten"])
    ]
    merchants["annual_run_rate"] = [
        annualise(a, g) for a, g in zip(merchants["typical_amount"], merchants["typical_gap_days"])
    ]

    subscriptions = merchants[merchants["verdict"] != "not_recurring"]
    forgotten = merchants[merchants["verdict"] == "cancel_candidate"]

    columns = st.columns(4)
    columns[0].metric("Recurring merchants", len(subscriptions))
    columns[1].metric("Annual run-rate", f"{subscriptions['annual_run_rate'].sum():,.2f}")
    columns[2].metric("Forgotten", len(forgotten))
    columns[3].metric("Recoverable per year", f"{forgotten['annual_run_rate'].sum():,.2f}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Recurring charges")
        st.dataframe(
            subscriptions[
                ["merchant", "kind", "typical_amount", "typical_gap_days", "annual_run_rate",
                 "recurring", "forgotten", "exit_friction", "last_charged"]
            ].sort_values("annual_run_rate", ascending=False),
            width="stretch", hide_index=True,
        )
    with right:
        st.subheader("By kind")
        st.bar_chart(subscriptions.groupby("kind")["annual_run_rate"].sum())

    st.subheader(f"💸 Cancel candidates ({len(forgotten)})")
    st.caption(
        "Two separate probabilities had to clear two separate gates: it is billing "
        "you automatically, AND it looks like something you stopped using. Collapsing "
        "those into one number is how auditors end up recommending you cancel your car insurance."
    )
    st.dataframe(
        forgotten[["merchant", "kind", "annual_run_rate", "forgotten", "exit_friction", "last_charged"]]
        .sort_values("annual_run_rate", ascending=False),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download audit CSV",
        merchants.drop(columns=["descriptions"]).to_csv(index=False).encode("utf-8"),
        file_name="subscription_audit.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {len(build_questions(records))} questions, {answers.input_tokens:,} input tokens, "
        f"${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s."
    )


if __name__ == "__main__":
    main()
