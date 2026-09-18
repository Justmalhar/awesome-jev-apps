"""Label Auditor -- find the mislabelled rows in an existing training set.

Your classifier plateaued at 0.86 and you have been tuning the model. The
reason is the training set: somebody labelled 40,000 tickets in a week, and a
few percent of them disagree with their own content.

Two ideas worth stealing:

1. THE SCORE LEVELS *ARE* THE ACTIONS. Not "how wrong is this label, 0 to 10",
   which leaves you fitting a cutoff to a scale nobody defined. The three levels
   are the three things you can do with a row: leave it, send it to a second
   annotator, or correct it. The rubric already encodes the decision.

2. TWO QUESTIONS, ONE REQUEST. "Is this label wrong?" and "which label does the
   content actually support?" are independent questions about the same row, so
   they are batched into the same ask(). The second one is only interesting for
   rows the first one flags -- but finding that out with a second pass would
   cost a second ingest of the same text for nothing.

Every rate, count and confusion cell on screen is pandas. The model is never
asked how many of anything.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_labelled.csv"
CHARS_PER_TOKEN = 4

NO_FIT = "no_label_fits"
NO_FIT_DESCRIPTION = (
    "The content supports none of the labels above. The row is not necessarily "
    "wrong — the taxonomy has a gap where this ticket should go."
)

# The rubric IS the decision. Read these as instructions to whoever owns the set.
LEVELS = [
    "The assigned label is clearly right for this content. Leave the row alone.",
    "The content genuinely supports more than one label. This row is ambiguous and belongs with a second annotator, or should be dropped.",
    "The assigned label contradicts the content. This row is mislabelled and should be corrected.",
]

# Below this the row is clean; between this and the sidebar's correct-at line it
# is a second-annotator job. Python policy, no inference behind it.
REVIEW_AT = 0.5
ACTIONS = ("keep", "review", "correct")

# Descriptions for the labels this repo's sample uses. Unknown labels from an
# uploaded file get a generated description -- serviceable, but write your own
# for a set you care about, because the boundary cases live here.
LABEL_HINTS: dict[str, str] = {
    "billing": "Invoices, charges, refunds, prices, payment methods, tax documents.",
    "technical": "Something in the product is broken, erroring, slow, or not rendering.",
    "account_access": "Getting into the account: passwords, 2FA, SSO, lockouts, seats, permissions.",
    "feature_request": "Asks for something the product does not do yet.",
    "cancellation": "Ending, not renewing, or winding down the subscription.",
}


def label_options(labels: list[str]) -> dict[str, str]:
    """The dataset's own label set, plus the no-match option. Never hardcoded:
    an uploaded CSV brings its own taxonomy."""
    options = {
        label: LABEL_HINTS.get(label, f"The content is about {label.replace('_', ' ')}.")
        for label in labels
    }
    options[NO_FIT] = NO_FIT_DESCRIPTION
    return options


def batch_rows(rows: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so one request's state fits the provider's advertised window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for row in rows:
        size = max(1, len(json.dumps(row, default=str)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(row)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(rows: list[dict], labels: list[str]) -> dict:
    """Two questions per row, one request. The verdict and the suggestion are
    independent: a row can be flagged with no better label available, and a row
    the model would label differently can still be defensibly labelled."""
    options = label_options(labels)
    questions: dict[str, dict] = {}
    for index in range(len(rows)):
        reference = {
            "text": f"`rows[{index}].text`",
            "assigned_label": f"`rows[{index}].label`",
        }
        questions[f"verdict_{index}"] = score(
            {"task": "Judge the label this row was given against what the row actually says.",
             "row": reference},
            LEVELS,
        )
        questions[f"suggest_{index}"] = choice(
            {"task": "Ignoring the label already assigned, which label does this content support?",
             "row": reference,
             "note": "Judge the content on its own terms. If the content pulls towards two "
                     "labels, pick the one the writer is actually asking you to act on."},
            options,
        )
    return questions


def decide(verdict: float, assigned: str, suggested: str, confidence: float,
           *, correct_at: float, conf_gate: float) -> str:
    """Rubric position + suggestion quality -> an action. All policy, no inference.

    A flagged row with a low-confidence suggestion, or a suggestion of
    `no_label_fits`, is a human's problem rather than an automatic correction:
    there is nothing safe to rewrite it to.
    """
    if verdict < REVIEW_AT:
        return "keep"
    if verdict < correct_at:
        return "review"
    if suggested == NO_FIT or suggested == assigned or confidence < conf_gate:
        return "review"
    return "correct"


def load_frame(uploaded) -> pd.DataFrame:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()))
    return pd.read_csv(SAMPLE)


def main() -> None:
    st.set_page_config(page_title="Label Auditor", page_icon="🏷️", layout="wide")
    st.title("🏷️ Label Auditor")
    st.caption("Your classifier is stuck at 0.86 because of the training set, not the model.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Policy")
        correct_at = st.slider(
            "Correct the label above", REVIEW_AT, 2.0, 1.5, 0.05,
            help=f"Position on the rubric (0..{len(LEVELS) - 1}). Level 2 is "
                 f"'mislabelled, correct it'; below this line a flagged row goes to review instead.",
        )
        conf_gate = st.slider(
            "Only apply a suggestion at confidence above", 0.0, 1.0, 0.60, 0.05,
            help="The suggested label is a Choice — it always returns something. "
                 "A flat distribution means it was guessing between labels.",
        )
        st.caption(f"Rows scoring under {REVIEW_AT} are left alone. Both sliders "
                   f"re-derive from stored scores — no re-run, no cost.")

    uploaded = st.file_uploader("Labelled CSV (needs `text` and `label`)", type=["csv"])
    frame = load_frame(uploaded)

    missing = {"text", "label"} - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    labels = sorted(frame["label"].dropna().astype(str).unique())
    if len(labels) < 2:
        st.error("Need at least two distinct labels to audit.")
        return
    if uploaded is None:
        st.caption(f"Using the bundled sample: {len(frame)} support tickets, {len(labels)} labels.")

    columns = st.columns(3)
    columns[0].metric("Rows", len(frame))
    columns[1].metric("Labels", len(labels))
    columns[2].metric("Questions to ask", len(frame) * 2)
    st.dataframe(frame["label"].value_counts().rename("rows"), width="stretch")

    if not st.button(f"Audit {len(frame)} rows", type="primary"):
        return

    records = frame.to_dict("records")
    groups = batch_rows(records, int(provider.context_tokens * 0.5))
    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_seconds = 0.0
    total_questions = 0
    progress = st.progress(0.0, text="Auditing...")

    try:
        with JevClient(provider=provider) as client:
            for group_index, group in enumerate(groups):
                questions = build_questions(group, labels)
                answers = client.ask({"rows": group}, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_seconds += answers.elapsed_s
                total_questions += len(questions)
                for index, row in enumerate(group):
                    results.append({
                        **row,
                        "verdict": answers.score(f"verdict_{index}"),
                        "verdict_confidence": answers.confidence(f"verdict_{index}"),
                        "suggested": answers.choice(f"suggest_{index}"),
                        "suggested_confidence": answers.confidence(f"suggest_{index}"),
                    })
                progress.progress((group_index + 1) / len(groups), text="Auditing...")
    except JevError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    out["action"] = [
        decide(row.verdict, str(row.label), str(row.suggested), row.suggested_confidence,
               correct_at=correct_at, conf_gate=conf_gate)
        for row in out.itertuples()
    ]

    # ── pandas from here down. The model produced none of these numbers. ──
    counts = out["action"].value_counts()
    corrections = out[out["action"] == "correct"]
    flagged = out[out["action"] != "keep"]

    columns = st.columns(4)
    columns[0].metric("Clean", int(counts.get("keep", 0)))
    columns[1].metric("Correct the label", int(counts.get("correct", 0)))
    columns[2].metric("Second annotator", int(counts.get("review", 0)))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    per_class = (
        out.assign(flagged=out["action"] != "keep", corrected=out["action"] == "correct")
        .groupby("label")
        .agg(rows=("label", "size"), flagged=("flagged", "sum"), corrected=("corrected", "sum"))
    )
    per_class["mislabel_rate"] = (per_class["corrected"] / per_class["rows"]).round(3)
    per_class["flagged_rate"] = (per_class["flagged"] / per_class["rows"]).round(3)

    left, right = st.columns([2, 3])
    with left:
        st.subheader("Per-class mislabel rate")
        st.dataframe(per_class.sort_values("mislabel_rate", ascending=False), width="stretch")
        if len(flagged):
            top_class = flagged["label"].value_counts()
            share = top_class.iloc[0] / len(flagged)
            st.metric(f"Flagged mass in `{top_class.index[0]}`", f"{share * 100:.0f}%")
            st.caption(
                "If one class holds most of the damage, that class's annotation "
                "guideline is the bug — not the annotators, and not the model."
            )
    with right:
        st.subheader("Confusion pattern (assigned → suggested)")
        st.caption("Only flagged rows. The recurring cells are your guideline's ambiguity.")
        if flagged.empty:
            st.caption("Nothing flagged at these thresholds.")
        else:
            st.dataframe(pd.crosstab(flagged["label"], flagged["suggested"]), width="stretch")

    st.subheader(f"Corrections ({len(corrections)})")
    st.caption("Assigned label contradicts the content, and the replacement label is confident.")
    show = [c for c in ("id", "text", "label", "suggested", "verdict", "suggested_confidence")
            if c in out.columns]
    st.dataframe(corrections[show].sort_values("verdict", ascending=False),
                 width="stretch", hide_index=True)

    st.subheader(f"Second annotator ({int(counts.get('review', 0))})")
    st.caption(
        "Level 1 of the rubric, plus flagged rows whose replacement label was a guess. "
        f"Rows suggesting `{NO_FIT}` land here too — the content fits nothing in the "
        "taxonomy, which is a gap in the label set rather than a bad row."
    )
    st.dataframe(out[out["action"] == "review"][show].sort_values("verdict", ascending=False),
                 width="stretch", hide_index=True)

    no_fit = out[out["suggested"] == NO_FIT]
    if not no_fit.empty:
        st.info(
            f"{len(no_fit)} row(s) fit no label in the set. Adding a label is usually "
            f"cheaper than re-labelling them into the nearest wrong one."
        )

    st.download_button(
        "Download corrections CSV (raw scores kept)",
        out.to_csv(index=False).encode("utf-8"),
        file_name="label_audit.csv",
        mime="text/csv",
    )
    st.caption(
        f"{len(groups)} request(s) · {total_questions} questions · {total_tokens:,} input tokens · "
        f"{total_seconds:.2f}s · ${total_cost:.6f} total, ${total_cost / len(out):.8f} per row. "
        "Re-thresholding the downloaded file costs nothing — the scores are in it."
    )


if __name__ == "__main__":
    main()
