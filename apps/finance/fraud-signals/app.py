"""Fraud Signals -- turn payment narratives into FEATURES for a downstream model.

This app deliberately does not tell you whether a payment is fraud.

That is not modesty, it is architecture. A fraud decision is a calibrated
trade-off between a false positive (a furious customer whose completion money is
held on the day of their house move) and a false negative (a pensioner's life
savings). That trade-off is set by a model trained on YOUR labelled outcomes and
YOUR loss function -- not by an LLM, and not by a threshold somebody picked in a
prompt.

What a scam narrative contains, though, is a set of properties: pressure applied
by a third party, an instruction to keep quiet, an authority being impersonated,
a promised return. Those properties are real, they are readable, and they are
exactly what a gradient-boosted model wants as input columns alongside your
velocity, device and beneficiary features.

So: eleven independent Nouls, one Score, a stable column schema, and a CSV.
No verdict. No risk score. No threshold anywhere in the app.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_cases.json"

# Downstream models break when columns move. Pin the schema and version it.
SCHEMA_VERSION = "fraud-signals/1"

FEATURES = [
    {
        "id": "third_party_instruction",
        "label": "Payment made on someone else's instruction",
        "true": "The customer is following instructions from another person about who to pay, how much, or when.",
        "false": "The customer decided to make this payment for their own reasons.",
    },
    {
        "id": "authority_impersonation",
        "label": "Counterparty claims official authority",
        "true": "Someone in the account presents themselves as a bank, police, tax authority, utility, court or other official body.",
        "false": "No claim of official authority appears anywhere in the account.",
    },
    {
        "id": "urgency_pressure",
        "label": "Externally imposed deadline",
        "true": "A deadline, closing window, or threatened consequence is being applied by the other party to make the payment happen now.",
        "false": "The timing is the customer's own, or ordinary and unpressured.",
    },
    {
        "id": "secrecy_instruction",
        "label": "Told to keep it quiet",
        "true": "The account includes an instruction or reason not to discuss the payment with the bank, family, or anyone who might question it.",
        "false": "Nothing in the account discourages the customer from discussing it.",
    },
    {
        "id": "safe_account_narrative",
        "label": "Moving money somewhere safer",
        "true": "The stated purpose is protecting the money by moving it to a safe, secure, holding or new account.",
        "false": "The money is being sent for a purpose other than protecting it.",
    },
    {
        "id": "promised_return",
        "label": "Returns or earnings promised",
        "true": "The account describes profit, returns, commission or earnings that follow from making the payment.",
        "false": "No financial gain to the customer is described as following from the payment.",
    },
    {
        "id": "unmet_relationship",
        "label": "Relationship exists only remotely",
        "true": "The counterparty is someone the customer knows through messages, calls or online contact and has never met in person.",
        "false": "The counterparty is met in person, an established business, or a relationship with real-world contact.",
    },
    {
        "id": "advance_payment_for_goods",
        "label": "Paying before receiving",
        "true": "Money is sent ahead of goods, tickets, an animal or a service the customer has not yet received or inspected.",
        "false": "The customer already has the thing, or is paying for something delivered over time by an established provider.",
    },
    {
        "id": "inspection_refused",
        "label": "Inspection or escrow refused",
        "true": "The other party gives a reason why the customer cannot visit, inspect, use escrow, or pay by a method with buyer protection.",
        "false": "Inspection or a protected payment method is available, or the question does not arise.",
    },
    {
        "id": "crypto_or_offramp",
        "label": "Destination converts out of banking",
        "true": "The money is heading into cryptocurrency, an exchange, a wallet top-up, or a remittance channel that leaves the banking system.",
        "false": "The destination is an ordinary bank account, card merchant or domestic business.",
    },
    {
        "id": "payment_details_changed",
        "label": "Beneficiary details changed late",
        "true": "The account describes account details that changed, were revised, or arrived by email or message after the arrangement was made.",
        "false": "The details were established at the outset, or verified through a channel the customer chose themselves.",
    },
]

# Concrete situations a reviewer would recognise, not an adjective scale.
CORROBORATION_LEVELS = [
    "The account rests entirely on what the other party said, with nothing the customer checked independently.",
    "The customer describes some detail of their own, but nothing they verified through a channel they chose themselves.",
    "The customer describes verifying the counterparty or the details through a channel they picked independently, such as a number they looked up or an in-person meeting.",
]


def build_questions(cases: list[dict]) -> dict:
    """Eleven Nouls and one Score per case, all in one request.

    Every one of these is an independent property of the narrative. Several are
    true at once in almost every real scam, which is precisely why they cannot be
    a Choice, and why collapsing them into a single 'risk' Score would destroy
    the information the downstream model is being given.
    """
    questions: dict[str, dict] = {}
    for index in range(len(cases)):
        reference = {
            "customer_account": f"`cases[{index}].customer_narrative`",
            "payee_name": f"`cases[{index}].payee_name`",
            "payment_reference": f"`cases[{index}].reference`",
            "channel": f"`cases[{index}].channel`",
        }
        for feature in FEATURES:
            questions[f"{feature['id']}_{index}"] = noul(
                {
                    "task": "Judge whether this one property is present in the customer's account of the payment.",
                    "property": feature["label"],
                    "payment": reference,
                    "note": "Judge only what the account says. Do not infer whether the payment is fraudulent; that is decided elsewhere.",
                },
                true=feature["true"],
                false=feature["false"],
            )
        questions[f"corroboration_{index}"] = score(
            {
                "task": "How much of this account did the customer verify for themselves, rather than take from the other party?",
                "payment": reference,
            },
            CORROBORATION_LEVELS,
        )
    return questions


FEATURE_IDS = [f["id"] for f in FEATURES]
COLUMNS = ["case_id", "schema_version", *FEATURE_IDS, "corroboration", "corroboration_confidence"]


def feature_row(case_id: str, probabilities: dict[str, float], corroboration: float, confidence: float) -> dict:
    """One row of the feature matrix, in a fixed column order.

    Probabilities go downstream RAW. No rounding, no bucketing, no thresholding --
    a 0.62 and a 0.98 are different evidence, and flattening them to `True` throws
    away the only thing that made this worth doing.
    """
    missing = set(FEATURE_IDS) - set(probabilities)
    if missing:
        raise KeyError(f"feature matrix would be ragged, missing: {sorted(missing)}")
    return {
        "case_id": case_id,
        "schema_version": SCHEMA_VERSION,
        **{feature_id: float(probabilities[feature_id]) for feature_id in FEATURE_IDS},
        "corroboration": float(corroboration),
        "corroboration_confidence": float(confidence),
    }


def feature_frame(rows: list[dict]) -> pd.DataFrame:
    """Stable schema, every time, so a downstream model can pin its inputs."""
    return pd.DataFrame(rows, columns=COLUMNS)


def prevalence(frame: pd.DataFrame, presence_gate: float) -> pd.Series:
    """Diagnostics only. A feature that is never present, or always present,
    carries no information for a downstream model and should be dropped or
    rewritten -- so the gate here is about FEATURE QUALITY, not about fraud."""
    return (frame[FEATURE_IDS] >= presence_gate).mean().sort_values(ascending=False)


def main() -> None:
    st.set_page_config(page_title="Fraud Signals", page_icon="📡", layout="wide")
    st.title("📡 Fraud Signals")
    st.caption("Payment narratives in, feature matrix out. No verdict — that belongs downstream.")
    st.info(
        "This app produces **features, not decisions**. There is no risk score and no "
        "threshold in it anywhere. Weighting these columns against your own labelled "
        "outcomes is the job of a model you own and can calibrate.",
        icon="📎",
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
        st.subheader("Feature diagnostics")
        presence_gate = st.slider("Count a feature as present above P=", 0.0, 1.0, 0.5, 0.05,
                                  help="Used for prevalence diagnostics only. The exported matrix is raw probabilities.")
        st.caption(f"schema: `{SCHEMA_VERSION}` · {len(FEATURES)} features + 1 score")

    uploaded = st.file_uploader("Payment cases JSON", type=["json"])
    cases = (
        json.loads(uploaded.read().decode("utf-8"))
        if uploaded
        else json.loads(SAMPLE.read_text(encoding="utf-8"))
    )
    if uploaded is None:
        st.caption(f"Bundled sample: {len(cases)} payment cases with customer narratives.")

    st.dataframe(
        pd.DataFrame(cases)[["case_id", "channel", "amount", "payee_name", "reference"]],
        width="stretch", hide_index=True,
    )

    question_count = len(cases) * (len(FEATURES) + 1)
    if not st.button(f"Extract features from {len(cases)} cases ({question_count} questions)", type="primary"):
        return

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"cases": cases}, build_questions(cases))
    except JevError as exc:
        st.error(str(exc))
        return

    rows = [
        feature_row(
            case["case_id"],
            {feature["id"]: answers.noul(f"{feature['id']}_{index}") for feature in FEATURES},
            answers.score(f"corroboration_{index}"),
            answers.confidence(f"corroboration_{index}"),
        )
        for index, case in enumerate(cases)
    ]
    matrix = feature_frame(rows)

    columns = st.columns(4)
    columns[0].metric("Cases", len(matrix))
    columns[1].metric("Feature columns", len(FEATURE_IDS))
    columns[2].metric("Questions asked", question_count)
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")

    st.subheader("Feature matrix")
    st.caption("Raw probabilities, fixed column order. This is the artefact — join it to your case table on `case_id`.")
    st.dataframe(
        matrix.set_index("case_id")[FEATURE_IDS].style.background_gradient(cmap="Blues", vmin=0, vmax=1),
        width="stretch",
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Prevalence")
        st.caption(
            "A feature present in every case, or in none, tells a downstream model nothing. "
            "This chart is about feature quality, not about fraud."
        )
        st.bar_chart(prevalence(matrix, presence_gate))
    with right:
        st.subheader("Feature correlation")
        st.caption(
            "Two features that move together are one feature with extra steps. "
            "Anything near 1.0 here is a prompt to merge or rewrite them."
        )
        st.dataframe(matrix[FEATURE_IDS].corr().round(2), width="stretch")

    st.subheader("Corroboration score")
    st.dataframe(
        matrix[["case_id", "corroboration", "corroboration_confidence"]].sort_values("corroboration"),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download feature matrix CSV",
        matrix.to_csv(index=False).encode("utf-8"),
        file_name="fraud_features.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {question_count} questions over {len(cases)} cases, "
        f"{answers.input_tokens:,} input tokens, ${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s "
        f"— ${answers.cost_usd / len(cases):.6f} per case."
    )


if __name__ == "__main__":
    main()
