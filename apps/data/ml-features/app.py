"""ML Features -- turn free text into calibrated features, then prove they helped.

Every churn model in the world is trained on the structured columns, because
those are the ones that fit in a matrix. The support ticket sitting next to them
-- the one where the customer writes "at this point I'm not sure we'll stay past
the contract" -- is dropped, because "text" is not a number.

The usual fixes are all bad in the same way. TF-IDF gives you five thousand
sparse columns that encode vocabulary, not meaning. Sentence embeddings give you
768 dense columns that encode meaning you cannot name, and need far more labelled
rows than you have to fit through. An LLM gives you a *sentence*, which you then
have to parse back into a number, having lost the calibration on the way.

A Noul returns a float in [0, 1]. That is already a feature.

    ticket_text -> {"considering_leaving": 0.91, "competitor_named": 0.12, ...}

Eight dense, calibrated, HUMAN-NAMED columns you can read the logistic regression
coefficients of and hand to a stakeholder. And because they are named, the model
that uses them stays auditable -- which is usually the reason the text got
dropped in the first place.

This app does the whole loop and reports the only number that settles it:

    AUC(structured)  vs  AUC(structured + text features)

If the second is not meaningfully larger, the features did not earn their cost,
and the app says so rather than showing you a chart.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import streamlit as st
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_tickets.csv"

TEXT_COLUMN = "ticket_text"
TARGET_COLUMN = "churned_within_90d"
NUMERIC_COLUMNS = ["tenure_months", "seats", "monthly_spend", "prior_tickets_90d", "first_response_hours"]
CATEGORICAL_COLUMNS = ["plan_tier"]

QUESTION_OVERHEAD_TOKENS = 55

# Every one of these can be true at the same time as every other, so every one
# is its own Noul. One Choice over them would force them to compete; one Score
# would rank things that have no order. Independent properties, separate columns.
NOUL_FEATURES: list[dict] = [
    {
        "id": "considering_leaving",
        "task": "Does the writer say or imply they are thinking about ending the relationship with this vendor?",
        "true": "The message raises leaving, not renewing, reconsidering the contract, or having to justify keeping it.",
        "false": "The message stays about the problem itself and never raises the future of the relationship.",
    },
    {
        "id": "competitor_named",
        "task": "Does the writer refer to another vendor or product that does this better, or that they are also using?",
        "true": "Another product or supplier is mentioned favourably, as a trial, or as a comparison.",
        "false": "No other vendor or product is referred to at all.",
    },
    {
        "id": "repeat_problem",
        "task": "Does the writer say this same problem has happened to them before?",
        "true": "The message refers back to a previous report, a recurrence, or a problem that came back.",
        "false": "The message reads as the first time this has been raised.",
    },
    {
        "id": "work_blocked",
        "task": "Does the writer say that work is stopped right now because of this?",
        "true": "People, customers, or a deadline are described as blocked, stopped, or failing because of the issue.",
        "false": "The issue is described as an annoyance, a question, or something that can wait.",
    },
    {
        "id": "billing_dispute",
        "task": "Does the writer dispute something about what they were charged?",
        "true": "An invoice, a charge, a duplicate payment, or a seat count on the bill is described as wrong.",
        "false": "Money is not disputed, even if the product or the plan is discussed.",
    },
    {
        "id": "exit_logistics",
        "task": "Does the writer ask about the practical steps of leaving, such as notice, termination, or taking their data out?",
        "true": "The message asks about cancelling, notice periods, closing the account, or exporting everything out.",
        "false": "No question is asked about how to leave or how to retrieve data before leaving.",
    },
    {
        "id": "relationship_warmth",
        "task": "Does the writer treat the support team as people they are on good terms with?",
        "true": "The tone is patient or friendly: no rush, happy to jump on a call, thanks in advance.",
        "false": "The tone is neutral, transactional, or hostile.",
    },
]

# A Score, not a Noul, because this one is genuinely a degree -- and the levels
# are situations, so the number means something without a legend.
TEMPERATURE_LEVELS = [
    "Matter-of-fact. The writer reports the problem and expects it to be handled in the normal course of things.",
    "Visibly impatient. The writer signals that their patience with this is finite, without threatening anything.",
    "Escalating. The writer invokes consequences: management, the contract, a deadline they will miss, or the renewal.",
]

ISSUE_TYPES = {
    "authentication_access": "Signing in, single sign-on, permissions, or being locked out.",
    "data_export_import": "Getting data into or out of the product, including exports and file formats.",
    "integration_sync": "A connection to another system: webhooks, third-party apps, or synchronisation.",
    "billing_account": "Invoices, seats, plan changes, payment methods, or the account itself.",
    "performance_reliability": "Slowness, timeouts, errors, or something that works intermittently.",
    "feature_request": "Asking for behaviour the product does not currently have.",
    "other": "A real issue that none of the types above describes.",
}


def build_questions(rows: list[dict]) -> dict:
    """Every question for every row in the batch, in ONE ask(). They are all
    independent judgements about one ingested state -- exactly the shape ask()
    exists for. Looping one request per ticket would cost ~8x as much."""
    questions: dict[str, dict] = {}
    for index in range(len(rows)):
        reference = f"`tickets[{index}].{TEXT_COLUMN}`"
        for feature in NOUL_FEATURES:
            questions[f"{feature['id']}_{index}"] = noul(
                {"task": feature["task"], "ticket_text": reference},
                true=feature["true"],
                false=feature["false"],
            )
        questions[f"temperature_{index}"] = score(
            {"task": "How hard is the writer pushing in this message?", "ticket_text": reference},
            TEMPERATURE_LEVELS,
        )
        questions[f"issue_{index}"] = choice(
            {"task": "What is this ticket mainly about?", "ticket_text": reference},
            ISSUE_TYPES,
        )
    return questions


def questions_per_row() -> int:
    return len(NOUL_FEATURES) + 2


def batch_rows(rows: list[dict], context_tokens: int, headroom: float = 0.5) -> list[list[dict]]:
    """Pack rows into requests that fit whatever context the provider serves."""
    budget = max(int(context_tokens * headroom), 1)
    batches: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for row in rows:
        cost = len(str(row)) // 4 + questions_per_row() * QUESTION_OVERHEAD_TOKENS
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(row)
        used += cost
    if current:
        batches.append(current)
    return batches


def jev_frame(records: list[dict]) -> pd.DataFrame:
    """Answers -> a numeric design block. The Choice becomes one-hot columns IN
    PANDAS; the model is never asked to produce a number it cannot produce."""
    frame = pd.DataFrame(records)
    issues = pd.get_dummies(
        pd.Categorical(frame["issue"], categories=list(ISSUE_TYPES)), prefix="issue"
    ).astype(float)
    numeric = frame[[feature["id"] for feature in NOUL_FEATURES] + ["temperature", "issue_confidence"]]
    return pd.concat([numeric.astype(float).reset_index(drop=True), issues.reset_index(drop=True)], axis=1)


def design_matrix(frame: pd.DataFrame, features: pd.DataFrame | None = None) -> pd.DataFrame:
    """The baseline block, optionally with the text block bolted on. Same rows,
    same order, same model -- so the only difference between the two runs is the
    text features. That is what makes the lift a fair comparison."""
    numeric = frame[[column for column in NUMERIC_COLUMNS if column in frame.columns]].astype(float)
    categorical = pd.get_dummies(
        frame[[column for column in CATEGORICAL_COLUMNS if column in frame.columns]],
        drop_first=True,
    ).astype(float)
    base = pd.concat([numeric.reset_index(drop=True), categorical.reset_index(drop=True)], axis=1)
    if features is None:
        return base
    return pd.concat([base, features.reset_index(drop=True)], axis=1)


def make_model(regularization: float) -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(C=regularization, max_iter=2000, class_weight="balanced")),
        ]
    )


def evaluate(X: pd.DataFrame, y: np.ndarray, seed: int, folds: int, regularization: float) -> dict:
    """Out-of-fold metrics. Cross-validated, because the honest question is
    whether the features generalise, not whether they fit."""
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    predicted = cross_val_predict(
        make_model(regularization), X, y, cv=splitter, method="predict_proba"
    )[:, 1]
    return {
        "auc": float(roc_auc_score(y, predicted)),
        "average_precision": float(average_precision_score(y, predicted)),
        "accuracy": float(accuracy_score(y, (predicted >= 0.5).astype(int))),
        "brier": float(brier_score_loss(y, predicted)),
        "n_features": int(X.shape[1]),
        "predicted": predicted,
    }


def lift(baseline: dict, enriched: dict) -> dict:
    """Pure arithmetic, in Python, where all arithmetic belongs."""
    return {
        "auc": enriched["auc"] - baseline["auc"],
        "average_precision": enriched["average_precision"] - baseline["average_precision"],
        "accuracy": enriched["accuracy"] - baseline["accuracy"],
        "brier": baseline["brier"] - enriched["brier"],  # lower is better, so flip it
        "features_added": enriched["n_features"] - baseline["n_features"],
    }


def verdict(delta_auc: float, worth_it_at: float) -> str:
    """The whole point of the app is being willing to return 'no'."""
    if delta_auc >= worth_it_at:
        return "worth it"
    if delta_auc > 0:
        return "marginal"
    return "not worth it"


def main() -> None:
    st.set_page_config(page_title="ML Features", page_icon="📈", layout="wide")
    st.title("📈 ML Features")
    st.caption("Free text into calibrated features, into a trained model, into a lift number.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\ncontext {provider.context_tokens:,} tokens", language=None)
        st.divider()
        st.subheader("Model")
        folds = st.slider("Cross-validation folds", 3, 10, 5, 1)
        seed = st.number_input("Random seed", value=20260919, step=1)
        regularization = st.select_slider("Inverse regularization C", [0.03, 0.1, 0.3, 1.0, 3.0, 10.0], value=1.0)
        st.caption("Same model, same folds, same seed for both runs. Only the features differ.")
        st.divider()
        worth_it_at = st.slider("Call it worth it above ΔAUC", 0.0, 0.2, 0.03, 0.005)

    uploaded = st.file_uploader(f"Labelled CSV (needs `{TEXT_COLUMN}` and `{TARGET_COLUMN}`)", type=["csv"])
    if uploaded is not None:
        frame = pd.read_csv(io.BytesIO(uploaded.read()))
    elif SAMPLE.is_file():
        frame = pd.read_csv(SAMPLE)
        st.caption(f"Using the bundled sample: {len(frame)} support tickets with a churn outcome.")
    else:
        st.warning("Upload a labelled CSV.")
        return

    missing = {TEXT_COLUMN, TARGET_COLUMN} - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    y = frame[TARGET_COLUMN].astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        st.error("The outcome column has only one class; there is nothing to learn.")
        return

    st.dataframe(frame.head(6), width="stretch")

    # ── The baseline needs no API key at all. It runs the moment you open this. ──
    baseline_X = design_matrix(frame)
    baseline = evaluate(baseline_X, y, int(seed), folds, regularization)

    head = st.columns(4)
    head[0].metric("Rows", len(frame))
    head[1].metric("Outcome rate", f"{y.mean() * 100:.1f}%")
    head[2].metric("Baseline AUC", f"{baseline['auc']:.3f}",
                   help="Structured columns only. No text, no API call, no cost.")
    head[3].metric("Baseline features", baseline["n_features"])

    rows = frame.to_dict("records")
    batches = batch_rows(rows, provider.context_tokens)
    st.caption(
        f"Extracting text features will ask {len(rows) * questions_per_row():,} questions "
        f"in {len(batches)} request(s)."
    )
    if not st.button("Extract text features and retrain", type="primary"):
        return

    records: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0
    progress = st.progress(0.0, text="Extracting features...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, batch in enumerate(batches):
                answers = client.ask({"tickets": batch}, build_questions(batch))
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_elapsed += answers.elapsed_s
                for index in range(len(batch)):
                    records.append(
                        {
                            **{f["id"]: answers.noul(f"{f['id']}_{index}") for f in NOUL_FEATURES},
                            "temperature": answers.score(f"temperature_{index}"),
                            "issue": answers.choice(f"issue_{index}"),
                            "issue_confidence": answers.confidence(f"issue_{index}"),
                        }
                    )
                progress.progress((batch_index + 1) / len(batches), text="Extracting features...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    features = jev_frame(records)
    enriched_X = design_matrix(frame, features)
    enriched = evaluate(enriched_X, y, int(seed), folds, regularization)
    delta = lift(baseline, enriched)
    call = verdict(delta["auc"], worth_it_at)

    st.subheader("Lift")
    banner = {"worth it": st.success, "marginal": st.warning, "not worth it": st.error}[call]
    banner(
        f"**{call.upper()}** — AUC {baseline['auc']:.3f} → {enriched['auc']:.3f} "
        f"({delta['auc']:+.3f}) from {delta['features_added']} text features, "
        f"at ${total_cost:.6f} for {len(frame)} rows."
    )

    scoreboard = pd.DataFrame(
        {
            "structured only": [baseline["auc"], baseline["average_precision"],
                                baseline["accuracy"], baseline["brier"], baseline["n_features"]],
            "structured + text": [enriched["auc"], enriched["average_precision"],
                                  enriched["accuracy"], enriched["brier"], enriched["n_features"]],
            "delta": [delta["auc"], delta["average_precision"], delta["accuracy"],
                      delta["brier"], delta["features_added"]],
        },
        index=["ROC AUC", "average precision", "accuracy @0.5", "Brier (lower better)", "features"],
    ).round(4)
    st.dataframe(scoreboard, width="stretch")
    st.caption(
        f"Out-of-fold over {folds} stratified folds, seed {seed}. Both rows use the same "
        "model, the same folds and the same seed — the only difference is the text block."
    )

    st.subheader("What the text features are worth, individually")
    st.caption(
        "Standardised logistic-regression coefficients on the full fit. These are readable, "
        "which is the argument for named Nouls over 768 anonymous embedding dimensions."
    )
    model = make_model(regularization).fit(enriched_X, y)
    coefficients = pd.DataFrame(
        {
            "feature": enriched_X.columns,
            "coefficient": model.named_steps["clf"].coef_[0],
            "block": ["text" if column in features.columns else "structured" for column in enriched_X.columns],
        }
    ).sort_values("coefficient", key=abs, ascending=False)
    left, right = st.columns([3, 2])
    left.dataframe(coefficients, width="stretch", hide_index=True)
    right.bar_chart(
        coefficients[coefficients["block"] == "text"].set_index("feature")["coefficient"]
    )

    st.subheader("Feature distributions")
    st.caption("A Noul's output is a probability, so these are already model-ready. No binning, no scaling story.")
    st.dataframe(features.describe().T.round(3), width="stretch")

    buffer = io.BytesIO()
    joblib.dump({"model": model, "columns": list(enriched_X.columns)}, buffer)
    download_left, download_right = st.columns(2)
    download_left.download_button(
        "Download trained model (joblib)", buffer.getvalue(),
        file_name="churn_model.joblib", mime="application/octet-stream",
    )
    download_right.download_button(
        "Download features CSV",
        pd.concat([frame.reset_index(drop=True), features], axis=1).to_csv(index=False).encode("utf-8"),
        file_name="tickets_with_features.csv", mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{len(frame) * questions_per_row():,} questions over {len(frame)} rows in "
        f"{len(batches)} request(s) · {total_tokens:,} input tokens · {total_elapsed:.2f}s · "
        f"${total_cost:.6f} (${total_cost / len(frame):.8f} per row). "
        f"The features are in the CSV, so every retrain after this one is free."
    )


if __name__ == "__main__":
    main()
