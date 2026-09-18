"""Column Inferrer -- name what an undocumented warehouse column semantically IS.

You inherit a 200-column table whose columns are called `f_12`, `val`, `dt2` and
`cd`, and the data dictionary was a Confluence page that nobody migrated.

THE ONE IDEA WORTH STEALING: the model never sees the data.

A 10,000-row column is a wall of tokens that says almost nothing a hundred rows
would not. So pandas PROFILES each column first -- distinct count, null rate,
length range, dtype, whether the values parse as numbers or dates, the values
that repeat most, and a handful of examples -- and only that profile is sent.
Every statistic in the profile is computed by pandas. The model is never asked
how many of anything there are; it is asked what kind of thing these look like.
That filtering is the difference between an affordable pass over a warehouse and
an unaffordable one, and it is also what makes the answer better: a profile is
signal, ten thousand rows are mostly repetition.

The second idea: one Choice for the semantic type, then separate Nouls for the
properties that are independent of it. A column can be an opaque machine
identifier AND carry a misleading name AND be full of test data, all at once, and
each of those wants its own threshold. Collapsing them into one rubric would
destroy that.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_table.csv"

# The vocabulary. `unknown` is the mandatory escape hatch: a Choice always
# returns something, so without it every junk column gets a confident label.
SEMANTIC_TYPES = {
    "person_name": "The name of a human being: given name, surname, or a full name.",
    "email_address": "An email address. Not a username, not a domain on its own.",
    "phone_number": "A telephone number, in any national or international format.",
    "postal_code": "A postal or ZIP code. Not a full street address, not a city.",
    "country_code": "A short country or region code such as US, GB, DE, or a full country name.",
    "currency_amount": "A quantity of money: prices, invoice totals, balances, refunds.",
    "timestamp": "A point in time carrying a clock time, or an epoch second/millisecond value.",
    "calendar_date": "A calendar day with no clock time attached.",
    "primary_identifier": "The identifier of the row's own subject, one distinct value per row.",
    "foreign_key_reference": "An identifier pointing at a row in some other table; values repeat.",
    "boolean_flag": "A two-state yes/no, true/false, Y/N or 0/1 indicator.",
    "enum_category": "A label drawn from a small fixed vocabulary, such as a plan tier or status.",
    "free_text_note": "Prose written by a person: comments, notes, descriptions, support replies.",
    "url": "A web address or link, with or without its scheme. Not a bare domain used as a name.",
    "geo_coordinate": "A latitude or longitude, as a signed decimal degree.",
    "unknown": "The values do not determine a semantic type: too uniform, too sparse, or too ambiguous to call.",
}

DATE_LIKE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}([T ]\d{1,2}:\d{2}|\s*$)")
NUMERIC_LIKE = re.compile(r"^\s*[-+]?\d{1,3}(,\d{3})*(\.\d+)?\s*$|^\s*[-+]?\d*\.?\d+([eE][-+]?\d+)?\s*$")


# ---------------------------------------------------------------------------
# Profiling -- pandas computes every number here. The model computes none.
# ---------------------------------------------------------------------------


def profile_column(frame: pd.DataFrame, name: str, examples: int = 8) -> dict:
    """Reduce a whole column to a small, token-cheap description of its shape."""
    column = frame[name]
    present = column.dropna().astype(str).str.strip()
    present = present[present != ""]
    rows = len(column)
    filled = len(present)

    lengths = present.str.len()
    repeated = present.value_counts()
    # Only surface values that actually repeat -- a list of 30 unique strings is
    # just the column again, and says nothing about its shape.
    top = [
        {"value": value[:80], "share_of_rows": f"{hits / filled:.0%}"}
        for value, hits in repeated.head(5).items()
        if hits > 1
    ] if filled else []

    return {
        "column_name": name,
        "pandas_dtype": str(column.dtype),
        "rows_profiled": f"{rows} rows",
        "empty_or_null": f"{(rows - filled) / rows:.0%} of rows" if rows else "no rows",
        "distinct_values": f"{present.nunique()} distinct out of {filled} filled",
        "uniqueness": "every filled value is distinct"
        if filled and present.nunique() == filled
        else ("values repeat heavily" if filled and present.nunique() <= max(2, filled // 5) else "values repeat some"),
        "value_length": f"{int(lengths.min())}-{int(lengths.max())} characters" if filled else "n/a",
        "parses_as_number": f"{present.str.match(NUMERIC_LIKE).mean():.0%} of filled values" if filled else "n/a",
        "parses_as_date": f"{present.str.match(DATE_LIKE).mean():.0%} of filled values" if filled else "n/a",
        "most_repeated_values": top or "no value occurs more than once",
        "example_values": present.head(examples).tolist(),
    }


def profile_table(frame: pd.DataFrame, examples: int = 8) -> list[dict]:
    return [profile_column(frame, name, examples) for name in frame.columns]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def build_questions(profiles: list[dict]) -> dict:
    """One Choice for the type; three independent Nouls that may all be true."""
    questions: dict[str, dict] = {}
    for index in range(len(profiles)):
        reference = {
            "column_name": f"`columns[{index}].column_name`",
            "profile": f"`columns[{index}]`",
        }
        questions[f"type_{index}"] = choice(
            {
                "task": "What kind of real-world thing do the values in this column hold?",
                "column": reference,
                "note": "Decide from the observed values and their shape. The column name is a "
                        "hint and is often wrong or abbreviated past the point of meaning.",
            },
            SEMANTIC_TYPES,
        )
        questions[f"opaque_{index}"] = noul(
            {
                "task": "Are these values an opaque machine-generated identifier rather than "
                        "something a human would read and recognise?",
                "column": reference,
            },
            true="Hashes, UUIDs, surrogate keys, random tokens -- meaningless to a person on sight",
            false="Values a human reads directly: names, notes, prices, dates, codes with meaning",
        )
        questions[f"misnamed_{index}"] = noul(
            {
                "task": "Does the column name describe something different from what the values "
                        "actually hold?",
                "column": reference,
            },
            true="The name promises one kind of thing and the values are plainly another kind",
            false="The name fits the values, or is so abbreviated that it promises nothing at all",
        )
        questions[f"placeholder_{index}"] = noul(
            {
                "task": "Do these values look like placeholder, filler or test data rather than "
                        "real production records?",
                "column": reference,
            },
            true="Fixtures and stubs: 'test', 'asdf', 'foo', 'TODO', or one filler value everywhere",
            false="Plausible real-world values with the variety real data has",
        )
    return questions


def batch_profiles(profiles: list[dict], context_tokens: int, reserve: float = 0.55) -> list[list[dict]]:
    """Split columns so one request's state fits the provider's context window.

    Sized against `provider.context_tokens`, never a hardcoded window -- TypeSafe
    serves 64k and OpenRouter 32k for the same model.
    """
    budget = max(1, int(context_tokens * reserve))
    batches: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for profile in profiles:
        # ~4 characters per token, plus the four questions this column adds.
        cost = len(json.dumps(profile, default=str)) // 4 + 220
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(profile)
        used += cost
    if current:
        batches.append(current)
    return batches


def decide(confidence: float, gate: float) -> str:
    """Whether an inferred type can go straight into the dictionary."""
    return "accepted" if confidence >= gate else "review"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Column Inferrer", page_icon="🔍", layout="wide")
    st.title("🔍 Column Inferrer")
    st.caption("Rebuild the data dictionary for a table nobody documented. Profiles in, semantics out.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token context", language=None)
        st.divider()
        st.subheader("Acceptance")
        gate = st.slider(
            "Accept an inferred type at confidence above", 0.0, 1.0, 0.7, 0.05,
            help="Columns below this land in the review queue instead of the dictionary.",
        )
        flag_gate = st.slider(
            "Raise a property flag at probability above", 0.0, 1.0, 0.6, 0.05,
            help="Applies to the opaque / misnamed / placeholder Nouls.",
        )
        st.divider()
        st.subheader("Profiling")
        examples = st.slider("Example values per column", 3, 20, 8, 1,
                             help="Sent to the model. Everything else in the profile is a pandas statistic.")

    uploaded = st.file_uploader("Table CSV", type=["csv"])
    if uploaded:
        frame = pd.read_csv(io.BytesIO(uploaded.read()), dtype=str, keep_default_na=False, na_values=[""])
    elif SAMPLE.is_file():
        frame = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False, na_values=[""])
        st.caption(
            f"Using the bundled sample: {frame.shape[1]} columns x {frame.shape[0]} rows of "
            "deliberately undocumented warehouse data."
        )
    else:
        st.warning("Upload a CSV, or add sample_table.csv next to this app.")
        return

    if frame.empty:
        st.error("That CSV has no rows to profile.")
        return

    profiles = profile_table(frame, examples)
    batches = batch_profiles(profiles, provider.context_tokens)

    columns = st.columns(4)
    columns[0].metric("Columns", frame.shape[1])
    columns[1].metric("Rows in file", f"{frame.shape[0]:,}")
    columns[2].metric("Rows sent to the model", 0, help="Zero. Only the pandas profiles are sent.")
    columns[3].metric("Requests", len(batches))

    with st.expander("What actually gets sent (the profile, not the rows)"):
        st.json(profiles[0])

    st.dataframe(frame.head(6), width="stretch")

    if not st.button(f"Infer {frame.shape[1]} column types", type="primary"):
        return

    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_questions = 0
    total_elapsed = 0.0
    progress = st.progress(0.0, text="Inferring...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, group in enumerate(batches):
                questions = build_questions(group)
                answers = client.ask({"columns": group}, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_elapsed += answers.elapsed_s
                total_questions += len(questions)
                for index, profile in enumerate(group):
                    results.append(
                        {
                            "column": profile["column_name"],
                            "inferred_type": answers.choice(f"type_{index}"),
                            "confidence": answers.confidence(f"type_{index}"),
                            "opaque_identifier": answers.noul(f"opaque_{index}"),
                            "name_is_misleading": answers.noul(f"misnamed_{index}"),
                            "placeholder_data": answers.noul(f"placeholder_{index}"),
                            "distinct_values": profile["distinct_values"],
                            "empty_or_null": profile["empty_or_null"],
                            "example_values": " | ".join(str(v) for v in profile["example_values"][:3]),
                        }
                    )
                progress.progress((batch_index + 1) / len(batches), text="Inferring...")
    except JevError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    out["decision"] = [decide(c, gate) for c in out["confidence"]]

    accepted = out[out["decision"] == "accepted"]
    review = out[out["decision"] == "review"]
    misnamed = out[out["name_is_misleading"] > flag_gate]

    columns = st.columns(4)
    columns[0].metric("Into the dictionary", len(accepted))
    columns[1].metric("Need a human", len(review))
    columns[2].metric("Misleading names", len(misnamed))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    st.subheader(f"Inferred data dictionary ({len(accepted)})")
    st.dataframe(
        accepted[["column", "inferred_type", "confidence", "distinct_values", "example_values"]]
        .sort_values("confidence", ascending=False),
        width="stretch", hide_index=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader(f"⚠️ Review queue ({len(review)})")
        st.caption(
            "A Choice always returns a type. What separates these is a flat distribution — "
            "the signal that it was picking between plausible options rather than recognising one."
        )
        st.dataframe(
            review[["column", "inferred_type", "confidence", "example_values"]].sort_values("confidence"),
            width="stretch", hide_index=True,
        )

    with right:
        st.subheader(f"🚩 The name lies ({len(misnamed)})")
        st.caption(
            "The single most useful flag here: a column called `email` full of phone numbers "
            "breaks every downstream consumer that trusted the name."
        )
        st.dataframe(
            misnamed[["column", "inferred_type", "name_is_misleading", "example_values"]]
            .sort_values("name_is_misleading", ascending=False),
            width="stretch", hide_index=True,
        )

    st.subheader("Independent properties")
    st.caption(
        "Three separate Nouls, not three levels on one rubric — a column can be opaque AND "
        "misnamed AND full of test data at the same time, and each wants its own threshold."
    )
    st.dataframe(
        out[["column", "inferred_type", "opaque_identifier", "name_is_misleading", "placeholder_data"]],
        width="stretch", hide_index=True,
        column_config={
            key: st.column_config.ProgressColumn(label, min_value=0.0, max_value=1.0, format="%.2f")
            for key, label in (
                ("opaque_identifier", "Opaque machine id"),
                ("name_is_misleading", "Name is misleading"),
                ("placeholder_data", "Placeholder / test data"),
            )
        },
    )

    st.download_button(
        "Download inferred data dictionary (CSV)",
        out.to_csv(index=False).encode("utf-8"),
        file_name="data_dictionary.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{total_questions} questions over {frame.shape[1]} columns in {len(batches)} request(s) · "
        f"{total_tokens:,} input tokens · {total_elapsed:.2f}s · ${total_cost:.6f} total — "
        f"${total_cost / frame.shape[1]:.8f} per column. "
        f"None of the {frame.shape[0]:,} data rows were sent."
    )


if __name__ == "__main__":
    main()
