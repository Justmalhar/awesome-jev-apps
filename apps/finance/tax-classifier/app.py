"""Tax Classifier -- which business expenses are plausibly deductible.

NOT TAX ADVICE. This sorts a shoebox into piles so a human spends their time on
the interesting pile. It does not file anything and it is not a substitute for an
accountant. See the README.

The structural idea: THE CONFIDENCE IS THE ROUTING, NOT THE ANSWER.

A Choice always returns a category. `Adobe Creative Cloud` and `suit for the
pitch meeting` both get one, with the same air of certainty. What separates them
is how concentrated the distribution was -- and expenses are a domain where the
genuinely ambiguous ones (dual-purpose phone bills, the home energy bill, the
extra private night on a work trip) are exactly the ones that get people in
trouble. So low confidence routes to a human, and two independent Nouls --
mixed personal use, and capital rather than revenue -- route there too, because
both need apportionment and apportionment is a judgment nobody should automate.

Every figure is pandas. The model is never told an amount matters and never
shown a total.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_expenses.csv"

UNCLEAR = "unclear"

CATEGORIES = {
    "office_and_software": "Software subscriptions, stationery, postage, business phone and internet, cloud services.",
    "travel_and_subsistence": "Business journeys, fares, parking, and meals taken while travelling for work.",
    "professional_services": "Accountancy, legal, bookkeeping, filing fees, and professional insurance.",
    "marketing": "Advertising, website, portfolio, promotional materials, and sponsorship.",
    "training_and_publications": "Courses, conferences, books and memberships that maintain or extend the skills of the trade.",
    "premises": "Rent, business rates, utilities, and the working-from-home proportion of household costs.",
    "equipment": "Computers, tools, furniture and other kit expected to last more than a year.",
    "client_entertaining": "Hospitality provided to clients or prospects, whatever the commercial motive.",
    "personal": "Ordinary private spending with no connection to the trade.",
    "tax_and_drawings": "Payments of tax itself, personal pension or protection policies, dividends and drawings.",
    UNCLEAR: "The description does not say enough to place this anywhere.",
}

# Levels are three things a person does next, not three adjectives.
EVIDENCE_LEVELS = [
    "The description names nothing business-related; a reviewer would have to ask what this was for.",
    "The description states a business purpose but names no client, project or event that could be checked against records.",
    "The description names the client, project or event, so it could be tied to a job in the records without asking anyone.",
]


def build_questions(expenses: list[dict]) -> dict:
    """One Choice and three independent judgments per expense, in one request.

    Category, personal element, capital nature and evidence quality are four
    different things. An expense can be correctly categorised AND part-personal
    AND capital -- so they are four questions, not levels on one scale.
    """
    questions: dict[str, dict] = {}
    for index in range(len(expenses)):
        reference = {
            "vendor": f"`expenses[{index}].vendor`",
            "description": f"`expenses[{index}].description`",
            "payment_method": f"`expenses[{index}].method`",
        }
        questions[f"cat_{index}"] = choice(
            {
                "task": "Which kind of cost is this for a self-employed designer's accounts?",
                "expense": reference,
            },
            CATEGORIES,
        )
        questions[f"mixed_{index}"] = noul(
            {
                "task": "Does this cost serve a private purpose as well as a business one?",
                "expense": reference,
            },
            true="The same spending also benefits the person privately, or would have been incurred anyway: a shared phone line, a household bill, clothing wearable outside work, or a trip with a private element.",
            false="The cost exists only because of the trade and has no private use.",
        )
        questions[f"capital_{index}"] = noul(
            {
                "task": "Is this the purchase of something lasting, rather than a running cost consumed within the year?",
                "expense": reference,
            },
            true="Equipment, machinery, furniture, vehicles, or a lasting improvement to premises that the business will still hold next year.",
            false="A running cost used up as the business operates: subscriptions, fares, professional fees, consumables, insurance for the period.",
        )
        questions[f"evidence_{index}"] = score(
            {
                "task": "How well would this description stand up if a reviewer asked what the cost was for?",
                "expense": reference,
            },
            EVIDENCE_LEVELS,
        )
    return questions


DISALLOWED = {"personal", "tax_and_drawings", "client_entertaining"}


def route(
    category: str,
    confidence: float,
    mixed_use: float,
    capital: float,
    confidence_gate: float,
    mixed_gate: float,
    capital_gate: float,
) -> str:
    """Where each expense goes. Pure Python, so every gate is a slider.

    Ordering matters: an expense that is disallowed outright never reaches the
    apportionment question, and anything capital leaves the profit-and-loss
    route entirely rather than being claimed in full.
    """
    if category in DISALLOWED:
        return "excluded"
    if category == UNCLEAR or confidence < confidence_gate:
        return "ask_a_human"
    if mixed_use >= mixed_gate:
        return "apportion_with_a_human"
    if capital >= capital_gate:
        return "capital_allowances"
    return "claim_in_full"


def relief(claimable: float, tax_rate: float) -> float:
    """The only multiplication in the app, and it happens here."""
    return round(claimable * tax_rate, 2)


def load_frame(uploaded) -> pd.DataFrame | None:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()))
    return pd.read_csv(SAMPLE) if SAMPLE.is_file() else None


def batch(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    st.set_page_config(page_title="Tax Classifier", page_icon="🧮", layout="wide")
    st.title("🧮 Tax Classifier")
    st.caption("Sort expenses into plausibly-deductible piles. Not tax advice — see the README.")
    st.warning(
        "This is a triage tool, not tax advice. It sorts spending so an accountant "
        "spends their hour on the ambiguous 20% instead of the obvious 80%. Nothing "
        "here is a filing position.",
        icon="⚠️",
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Routing gates")
        confidence_gate = st.slider("Auto-file above category confidence", 0.0, 1.0, 0.7, 0.05)
        mixed_gate = st.slider("Send for apportionment above P(mixed use)", 0.0, 1.0, 0.4, 0.05,
                               help="Deliberately low. Apportionment is a human's job.")
        capital_gate = st.slider("Treat as capital above P=", 0.0, 1.0, 0.55, 0.05)
        st.divider()
        tax_rate = st.slider("Marginal rate for the relief estimate", 0.0, 0.6, 0.29, 0.01)
        rows_per_request = st.slider("Expenses per request", 5, 40, 25, 5)

    uploaded = st.file_uploader("Expenses CSV (needs `vendor`, `amount`, `description`)", type=["csv"])
    frame = load_frame(uploaded)
    if frame is None:
        st.warning("Upload a CSV, or keep sample_expenses.csv next to this app.")
        return

    missing = {"vendor", "amount", "description"} - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    if uploaded is None:
        st.caption(f"Bundled sample: {len(frame)} expenses from a sole trader's first quarter.")
    st.dataframe(frame.head(8), width="stretch", hide_index=True)

    if not st.button(f"Classify {len(frame)} expenses", type="primary"):
        return

    records = frame.to_dict("records")
    groups = batch(records, rows_per_request)
    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    progress = st.progress(0.0, text="Classifying...")

    try:
        with JevClient(provider=provider) as client:
            for group_index, group in enumerate(groups):
                answers = client.ask({"expenses": group}, build_questions(group))
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                for index, expense in enumerate(group):
                    category = answers.choice(f"cat_{index}")
                    confidence = answers.confidence(f"cat_{index}")
                    mixed_use = answers.noul(f"mixed_{index}")
                    capital = answers.noul(f"capital_{index}")
                    results.append(
                        {
                            **expense,
                            "category": category,
                            "confidence": confidence,
                            "mixed_use": mixed_use,
                            "capital": capital,
                            "evidence": answers.score(f"evidence_{index}"),
                            "route": route(category, confidence, mixed_use, capital,
                                           confidence_gate, mixed_gate, capital_gate),
                        }
                    )
                progress.progress((group_index + 1) / len(groups), text="Classifying...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    out["amount"] = out["amount"].astype(float)
    claimable = out[out["route"] == "claim_in_full"]
    human = out[out["route"].isin({"ask_a_human", "apportion_with_a_human"})]

    columns = st.columns(4)
    columns[0].metric("Expenses", len(out))
    columns[1].metric("Claim in full", f"{claimable['amount'].sum():,.2f}")
    columns[2].metric("Needs a human", f"{len(human)} ({len(human) / len(out) * 100:.0f}%)")
    columns[3].metric("Relief estimate", f"{relief(float(claimable['amount'].sum()), tax_rate):,.2f}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("By category")
        by_category = (
            out.groupby("category")["amount"].agg(["sum", "count"])
            .rename(columns={"sum": "total", "count": "n"})
            .sort_values("total", ascending=False)
        )
        st.dataframe(by_category, width="stretch")
    with right:
        st.subheader("Where everything went")
        st.bar_chart(out.groupby("route")["amount"].sum())

    st.subheader(f"👤 Reaching a human ({len(human)})")
    st.caption(
        "Three different reasons land here. Low confidence means the model was "
        "genuinely torn between categories. High mixed-use means the cost is part "
        "private and somebody has to decide the split — a number no model should "
        "pick. Both are routing decisions made in Python from probabilities, not "
        "instructions given to the model."
    )
    st.dataframe(
        human[["expense_id", "vendor", "amount", "description", "category", "confidence", "mixed_use", "route"]]
        .sort_values("amount", ascending=False),
        width="stretch", hide_index=True,
    )

    st.subheader("Weak evidence trail")
    st.caption("Low score means the description would not survive a question about what it was for.")
    st.dataframe(
        out[out["evidence"] < 1.0][["expense_id", "vendor", "amount", "description", "evidence"]]
        .sort_values("evidence"),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download classified CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="classified_expenses.csv",
        mime="text/csv",
    )
    st.caption(
        f"{len(groups)} request(s), {total_tokens:,} input tokens, ${total_cost:.6f} total — "
        f"${total_cost / len(out):.8f} per expense."
    )


if __name__ == "__main__":
    main()
