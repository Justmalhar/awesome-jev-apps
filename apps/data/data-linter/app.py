"""Data Linter -- find rows whose fields contradict EACH OTHER.

Not a classifier. Every value in these rows is individually plausible: `Lisbon`
is a real city, `Spain` is a real country, `Junior` is a real seniority and
`Chief Technology Officer` is a real title. A column-at-a-time validator --
regex, enum check, dbt test, Great Expectations -- passes all of them, because
each value is valid *in isolation*. The error only exists in the relationship
between two columns, and that is precisely the class of bug that survives every
constraint you can write down.

Two ideas worth stealing:

1. ONE NOUL PER CONSISTENCY RULE. The rules are independent: a row can have a
   wrong city AND a wrong currency AND a title that does not match its seniority.
   Those are three separate probabilities you want to threshold separately, so
   they are three Nouls -- not one Choice ("what is wrong with this row?") and
   not one Score ("how bad is this row?"). Collapsing them would throw away the
   only thing you actually act on: which rule fired.

2. THE RULES ARE DATA. Adding a rule is adding a dict, not writing a validator.
   That is the point: `city is not in country` is a rule nobody can express as
   code without shipping a gazetteer, and here it is nine lines of prose.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_records.csv"

# Rough per-question cost of the instruction text itself, in tokens. Used only
# to size batches against provider.context_tokens -- never shown to the model.
QUESTION_OVERHEAD_TOKENS = 45

# Each rule is ONE Noul. They are independent properties of the same row, so
# they get separate probabilities and separate thresholds. `fields` is what the
# rule reads -- rows missing any of them skip the rule entirely, in Python.
RULES: list[dict] = [
    {
        "id": "geo",
        "label": "City does not belong to the stated country",
        "fields": ("city", "country"),
        "task": "Is the city in this record located somewhere other than the country in this record?",
        "true": "The named city is not in the named country. Example: city Toronto with country Czechia.",
        "false": "The named city is in the named country, or the pairing is a genuine same-name city that really exists there.",
    },
    {
        "id": "seniority",
        "label": "Job title does not match the seniority column",
        "fields": ("job_title", "seniority"),
        "task": "Does the job title in this record sit at a different level from the seniority value in this record?",
        "true": "Title and seniority describe different levels. Example: title Intern with seniority C-level, or title Chief Technology Officer with seniority Junior.",
        "false": "Title and seniority describe the same level, or the title is level-neutral and could reasonably sit at the stated seniority.",
    },
    {
        "id": "department",
        "label": "Job title does not belong to the stated department",
        "fields": ("job_title", "department"),
        "task": "Would the job title in this record normally sit in a different department from the one in this record?",
        "true": "The role clearly belongs elsewhere. Example: a Sous Chef filed under Engineering, or a VP Sales filed under Engineering.",
        "false": "The role plausibly sits in the stated department, including cross-functional roles that legitimately report into it.",
    },
    {
        "id": "currency",
        "label": "Currency is not the one used in the stated country",
        "fields": ("currency", "country"),
        "task": "Is the currency code in this record other than the currency normally used in the country in this record?",
        "true": "The currency is not the country's own. Example: currency USD with country Italy, or currency GBP with country Spain.",
        "false": "The currency is the country's own, or is a currency that country genuinely transacts in as standard.",
    },
    {
        "id": "status",
        "label": "Free-text description contradicts the status column",
        "fields": ("account_status", "description"),
        "task": "Does the description in this record describe a situation that the status value in this record rules out?",
        "true": "The prose and the status cannot both be true. Example: status churned alongside a description of renewing early and expanding, or status suspended alongside a description of full access and no outstanding balance.",
        "false": "The prose is consistent with the status, or says nothing about the account's standing either way.",
    },
    {
        "id": "description",
        "label": "Free-text description contradicts the department",
        "fields": ("description", "department"),
        "task": "Does the work described in this record belong to a different department from the one in this record?",
        "true": "The described duties belong to another function. Example: a description of picking and packing outbound orders under a Legal department.",
        "false": "The described duties fit the stated department, or are too generic to place anywhere in particular.",
    },
]

RULES_BY_ID = {rule["id"]: rule for rule in RULES}


def culprit_options(columns: list[str]) -> dict[str, str]:
    """Which single column holds the bad value. Two escape hatches, both real:
    the row may be fine, or it may conflict without showing which side is wrong."""
    options = {
        column: f"The value in the `{column}` field is the incorrect one, and the fields it conflicts with are correct."
        for column in columns
    }
    options["no_contradiction"] = (
        "Nothing in this record conflicts with anything else in it; every field is consistent with the rest."
    )
    options["unclear"] = (
        "Fields in this record do conflict, but the record does not show which of them holds the incorrect value."
    )
    return options


def applicable_rules(columns: list[str], enabled: list[str]) -> list[dict]:
    """A rule needs its columns present. Filtering here means we never send a
    question whose answer is structurally meaningless."""
    present = set(columns)
    return [
        rule for rule in RULES
        if rule["id"] in enabled and set(rule["fields"]) <= present
    ]


def estimate_row_tokens(row: dict, rules: list[dict]) -> int:
    """Crude but honest: state size plus one instruction per question."""
    return len(str(row)) // 4 + (len(rules) + 1) * QUESTION_OVERHEAD_TOKENS


def batch_rows(rows: list[dict], rules: list[dict], context_tokens: int,
               headroom: float = 0.5) -> list[list[dict]]:
    """Pack rows into requests that fit the PROVIDER's context, whatever it is.
    TypeSafe serves 64k and OpenRouter 32k; neither number appears here."""
    budget = max(int(context_tokens * headroom), 1)
    batches: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for row in rows:
        cost = estimate_row_tokens(row, rules)
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(row)
        used += cost
    if current:
        batches.append(current)
    return batches


def build_questions(rows: list[dict], rules: list[dict], columns: list[str]) -> dict:
    """One Noul per (row, rule) plus one Choice per row. All independent, so all
    of them go in a single ask() against a state ingested once."""
    options = culprit_options(columns)
    questions: dict[str, dict] = {}
    for index in range(len(rows)):
        for rule in rules:
            questions[f"{rule['id']}_{index}"] = noul(
                {
                    "task": rule["task"],
                    **{field: f"`rows[{index}].{field}`" for field in rule["fields"]},
                    "record": f"`rows[{index}]`",
                },
                true=rule["true"],
                false=rule["false"],
            )
        questions[f"culprit_{index}"] = choice(
            {
                "task": "If one field in this record holds an incorrect value, which field is it?",
                "record": f"`rows[{index}]`",
                "note": "Judge only against the other fields in the same record. Do not use outside knowledge of the person or account.",
            },
            options,
        )
    return questions


def triage(probability: float, flag_at: float, band: float) -> str:
    """Pure policy, in Python. Re-tuning the sliders re-derives every row on
    screen without a single new API call."""
    if probability >= flag_at:
        return "contradiction"
    if probability >= flag_at - band:
        return "review"
    return "clean"


def main() -> None:
    st.set_page_config(page_title="Data Linter", page_icon="🧹", layout="wide")
    st.title("🧹 Data Linter")
    st.caption("Rows where the fields contradict each other. Every value is valid; the row is not.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\ncontext {provider.context_tokens:,} tokens", language=None)
        st.divider()
        st.subheader("Rules")
        enabled = st.multiselect(
            "Consistency rules to run",
            [rule["id"] for rule in RULES],
            default=[rule["id"] for rule in RULES],
            format_func=lambda rule_id: RULES_BY_ID[rule_id]["label"],
        )
        st.caption("Each rule is one Noul. Dropping a rule drops its questions, and its cost.")
        st.divider()
        st.subheader("Policy")
        flag_at = st.slider("Flag a contradiction at P >=", 0.3, 0.99, 0.65, 0.05)
        band = st.slider("Width of the review band below that", 0.0, 0.4, 0.2, 0.05)
        culprit_gate = st.slider("Trust the suggested culprit above confidence", 0.0, 1.0, 0.6, 0.05)
        st.caption("All three are Python. Changing them re-runs no inference.")

    uploaded = st.file_uploader("Records CSV", type=["csv"])
    if uploaded is not None:
        frame = pd.read_csv(io.BytesIO(uploaded.read()))
    elif SAMPLE.is_file():
        frame = pd.read_csv(SAMPLE)
        st.caption(f"Using the bundled sample: {len(frame)} records with planted contradictions.")
    else:
        st.warning("Upload a CSV of records.")
        return

    columns = [str(column) for column in frame.columns]
    rules = applicable_rules(columns, enabled)
    if not rules:
        st.warning(
            "No rule matches this CSV's columns. Rules need their fields present — "
            f"available here: {', '.join(columns)}"
        )
        return

    st.dataframe(frame.head(8), width="stretch")
    skipped = [rule["label"] for rule in RULES if rule["id"] in enabled and rule not in rules]
    if skipped:
        st.info("Skipped for missing columns: " + "; ".join(skipped))

    rows = frame.to_dict("records")
    batches = batch_rows(rows, rules, provider.context_tokens)
    question_count = len(rows) * (len(rules) + 1)

    metrics = st.columns(4)
    metrics[0].metric("Records", len(rows))
    metrics[1].metric("Rules active", len(rules))
    metrics[2].metric("Questions", f"{question_count:,}")
    metrics[3].metric("Requests", len(batches))

    if not st.button(f"Lint {len(rows)} records", type="primary"):
        return

    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0
    progress = st.progress(0.0, text="Linting...")

    try:
        with JevClient(provider=provider) as client:
            offset = 0
            for batch_index, batch in enumerate(batches):
                answers = client.ask(
                    {"rows": batch}, build_questions(batch, rules, columns)
                )
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_elapsed += answers.elapsed_s
                for index, row in enumerate(batch):
                    record = {"_row": offset + index, **row}
                    for rule in rules:
                        record[rule["id"]] = answers.noul(f"{rule['id']}_{index}")
                    record["culprit"] = answers.choice(f"culprit_{index}")
                    record["culprit_confidence"] = answers.confidence(f"culprit_{index}")
                    results.append(record)
                offset += len(batch)
                progress.progress((batch_index + 1) / len(batches), text="Linting...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    rule_ids = [rule["id"] for rule in rules]

    # ── Every number below is pandas. The model produced probabilities only. ──
    for rule_id in rule_ids:
        out[f"{rule_id}_state"] = out[rule_id].map(lambda p: triage(p, flag_at, band))

    state_columns = [f"{rule_id}_state" for rule_id in rule_ids]
    out["n_contradictions"] = (out[state_columns] == "contradiction").sum(axis=1)
    out["n_review"] = (out[state_columns] == "review").sum(axis=1)

    dirty = out[out["n_contradictions"] > 0]
    review = out[(out["n_contradictions"] == 0) & (out["n_review"] > 0)]

    summary = st.columns(4)
    summary[0].metric("Rows with a contradiction", len(dirty),
                      delta=f"{len(dirty) / len(out) * 100:.0f}% of the table")
    summary[1].metric("Rows in the review band", len(review))
    summary[2].metric("Clean rows", len(out) - len(dirty) - len(review))
    summary[3].metric("Cost", f"${total_cost:.6f}")

    st.subheader("Which rule fired, and how often")
    st.caption("Separate Nouls means this breakdown exists at all. One Score per row would not give it to you.")
    per_rule = pd.DataFrame(
        {
            "rule": [RULES_BY_ID[rule_id]["label"] for rule_id in rule_ids],
            "flagged": [(out[f"{rule_id}_state"] == "contradiction").sum() for rule_id in rule_ids],
            "in_review_band": [(out[f"{rule_id}_state"] == "review").sum() for rule_id in rule_ids],
            "mean_probability": [out[rule_id].mean().round(3) for rule_id in rule_ids],
        }
    ).set_index("rule")
    st.bar_chart(per_rule["flagged"])
    st.dataframe(per_rule, width="stretch")

    st.subheader("Which column to fix")
    st.caption(
        "The Choice always returns a field — that is how a Choice works. Only the "
        "ones above the confidence gate are worth acting on."
    )
    trusted = dirty[dirty["culprit_confidence"] >= culprit_gate]
    if trusted.empty:
        st.caption("No culprit suggestion cleared the gate.")
    else:
        st.dataframe(
            trusted["culprit"].value_counts().rename_axis("suspect field").to_frame("rows"),
            width="stretch",
        )

    st.subheader(f"🚨 Contradictions ({len(dirty)})")
    for _, record in dirty.sort_values("n_contradictions", ascending=False).iterrows():
        fired = [rule_id for rule_id in rule_ids if record[f"{rule_id}_state"] == "contradiction"]
        with st.container(border=True):
            st.markdown("**" + " · ".join(RULES_BY_ID[rule_id]["label"] for rule_id in fired) + "**")
            st.caption(
                " · ".join(f"{rule_id} P={record[rule_id]:.2f}" for rule_id in fired)
                + f" · suspect field `{record['culprit']}` (confidence {record['culprit_confidence']:.2f})"
            )
            evidence = sorted({field for rule_id in fired for field in RULES_BY_ID[rule_id]["fields"]})
            st.dataframe(
                frame.iloc[[int(record["_row"])]][evidence], width="stretch", hide_index=True
            )

    if not review.empty:
        st.subheader(f"⚠️ Review band ({len(review)})")
        st.caption("Below the flag threshold but above the band floor. Widen or narrow the band in the sidebar — it costs nothing.")
        st.dataframe(review[["_row", *rule_ids]], width="stretch", hide_index=True)

    st.download_button(
        "Download lint results CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="lint_results.csv",
        mime="text/csv",
    )
    st.divider()
    st.caption(
        f"{question_count:,} questions over {len(rows)} rows in {len(batches)} request(s) · "
        f"{total_tokens:,} input tokens · {total_elapsed:.2f}s · ${total_cost:.6f} "
        f"(${total_cost / max(len(rows), 1):.8f} per row). Raw probabilities are in the CSV, "
        f"so re-thresholding is free."
    )


if __name__ == "__main__":
    main()
