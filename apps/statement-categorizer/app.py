"""Statement Categorizer -- categorize years of bank/card transactions.

The point this app makes: **Jev does the judgment, Python does the arithmetic.**

`jev-1.13` cannot count and cannot add -- that is a documented failure mode, not
a tuning problem. So the model never sees a total, never sums a column, and is
never asked "how much did I spend on groceries?". It answers one question per
row ("what kind of spending is this?") and pandas does every number on screen.

The second point is the confidence gate. A Choice always returns something, so
the interesting signal is not the label -- it is how concentrated the
distribution was. Low-confidence rows go to a review queue instead of being
silently filed under the model's best guess.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_transactions.csv"

CATEGORIES = {
    "groceries": "Supermarkets, food markets, greengrocers. Not restaurants or takeaway.",
    "eating_out": "Restaurants, cafes, bars, takeaway, food delivery.",
    "transport": "Fuel, public transport, taxis, ride-hailing, parking, tolls, vehicle servicing.",
    "housing": "Rent, mortgage, council tax, service charge, home insurance.",
    "utilities": "Electricity, gas, water, broadband, mobile phone.",
    "software": "Software subscriptions, cloud services, developer tools, domains, hosting.",
    "shopping": "Clothing, electronics, household goods, general retail.",
    "health": "Pharmacy, dentist, optician, gym, medical appointments, health insurance.",
    "entertainment": "Streaming, cinema, games, books, music, events, hobbies.",
    "travel": "Flights, hotels, trains booked for travel, holiday accommodation.",
    "income": "Salary, refunds, transfers in, interest received.",
    "transfer": "Movement between the owner's own accounts, savings, credit card payments.",
    "other": "Genuinely does not fit any category above.",
}


def build_questions(rows: list[dict]) -> dict:
    """Per row: category, plus two independent properties worth thresholding."""
    questions: dict[str, dict] = {}
    for index in range(len(rows)):
        reference = {
            "description": f"`transactions[{index}].description`",
            "amount": f"`transactions[{index}].amount`",
        }
        questions[f"cat_{index}"] = choice(
            {"task": "Which spending category does this transaction belong to?",
             "transaction": reference},
            CATEGORIES,
        )
        # Independent labels: a transaction can be a business expense in any
        # category, and a subscription in several. One Noul each.
        questions[f"sub_{index}"] = noul(
            {"task": "Is this a recurring subscription rather than a one-off purchase?",
             "transaction": reference},
            true="A recurring membership, subscription, or standing monthly charge",
            false="A one-off or irregular purchase",
        )
        questions[f"biz_{index}"] = noul(
            {"task": "Does this look like a business expense rather than personal spending?",
             "transaction": reference},
            true="Professional tools, services, or costs a freelancer would claim",
            false="Ordinary personal or household spending",
        )
    return questions


def load_frame(uploaded) -> pd.DataFrame | None:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()))
    if SAMPLE.is_file():
        return pd.read_csv(SAMPLE)
    return None


def main() -> None:
    st.set_page_config(page_title="Statement Categorizer", page_icon="🏦", layout="wide")
    st.title("🏦 Statement Categorizer")
    st.caption("Categorize a bank or card export. Judgment by Jev, every number by pandas.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        confidence_gate = st.slider(
            "Auto-file at confidence above", 0.0, 1.0, 0.7, 0.05,
            help="Rows below this land in the review queue instead of being filed.",
        )
        batch_size = st.slider("Rows per request", 20, 200, 80, 10)

    uploaded = st.file_uploader("Transactions CSV (needs `description` and `amount`)", type=["csv"])
    frame = load_frame(uploaded)
    if frame is None:
        st.warning("Upload a CSV, or add sample_transactions.csv next to this app.")
        return

    missing = {"description", "amount"} - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    if uploaded is None:
        st.caption(f"Using the bundled sample: {len(frame)} transactions.")
    st.dataframe(frame.head(8), width="stretch")

    if not st.button(f"Categorize {len(frame)} transactions", type="primary"):
        return

    records = frame.to_dict("records")
    batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]

    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    progress = st.progress(0.0, text="Categorizing...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, rows in enumerate(batches):
                answers = client.ask({"transactions": rows}, build_questions(rows))
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                for index, row in enumerate(rows):
                    results.append(
                        {
                            **row,
                            "category": answers.choice(f"cat_{index}"),
                            "confidence": answers.confidence(f"cat_{index}"),
                            "subscription": answers.noul(f"sub_{index}"),
                            "business": answers.noul(f"biz_{index}"),
                        }
                    )
                progress.progress((batch_index + 1) / len(batches), text="Categorizing...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    out["needs_review"] = out["confidence"] < confidence_gate

    # ── Every number below is computed by pandas. The model saw none of them. ──
    auto = (~out["needs_review"]).sum()
    columns = st.columns(4)
    columns[0].metric("Transactions", len(out))
    columns[1].metric("Auto-filed", f"{auto} ({auto / len(out) * 100:.0f}%)")
    columns[2].metric("Need review", int(out["needs_review"].sum()))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    spend = out[out["amount"] < 0].copy()
    spend["spend"] = spend["amount"].abs()

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Spend by category")
        by_category = (
            spend.groupby("category")["spend"].agg(["sum", "count"])
            .sort_values("sum", ascending=False)
            .rename(columns={"sum": "total", "count": "n"})
        )
        st.bar_chart(by_category["total"])
        st.dataframe(by_category, width="stretch")

    with right:
        st.subheader("Recurring subscriptions")
        subscriptions = spend[spend["subscription"] > 0.6]
        if subscriptions.empty:
            st.caption("None detected above P=0.6.")
        else:
            st.metric("Monthly-ish total", f"{subscriptions['spend'].sum():,.2f}")
            st.dataframe(
                subscriptions[["description", "spend", "subscription"]]
                .sort_values("spend", ascending=False),
                width="stretch", hide_index=True,
            )

        st.subheader("Claimable as business")
        business = spend[spend["business"] > 0.6]
        st.metric("Total", f"{business['spend'].sum():,.2f}")

    st.subheader(f"⚠️ Review queue ({int(out['needs_review'].sum())})")
    st.caption(
        "The model returned a label for every one of these — it always does. "
        "What it also returned was a flat distribution, which is the signal that "
        "it was guessing. That is what confidence is for."
    )
    st.dataframe(
        out[out["needs_review"]][["description", "amount", "category", "confidence"]]
        .sort_values("confidence"),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download categorized CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="categorized_transactions.csv",
        mime="text/csv",
    )
    st.caption(
        f"{len(batches)} request(s), {total_tokens:,} input tokens, ${total_cost:.6f} total — "
        f"${total_cost / len(out):.8f} per transaction."
    )


if __name__ == "__main__":
    main()
