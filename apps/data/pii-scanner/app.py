"""PII Scanner -- which columns in a warehouse carry personal data.

A DSAR lands, or retention policy needs signing off, and someone has to say
which of 900 columns across 60 tables hold personal data. Today that is a
spreadsheet and guesswork.

Two ideas worth stealing:

1. PROFILE, DON'T SHIP THE TABLE. Python reduces each column to a fixed-size
   profile -- name, type, null rate, distinct count, uniqueness ratio, a few
   masked example values, and cheap regex prefilters that say whether anything
   in it has the SHAPE of an email, a phone number, an IBAN or an IP. The model
   sees ~80 tokens per column instead of a billion rows, so the scan costs
   scale with the schema, not the data. Regex does shape; Jev does meaning --
   which is the only way `events.ua_string` and `hr.absence_reason_code` get
   caught, because no regex knows what they mean.

2. THREE NOULS, NOT ONE SCORE. "Singles out a person on its own",
   "singles out a person in combination with other columns", and "special
   category data" are independent properties that are routinely all true at
   once, and each one wants its own threshold. Collapsing them into one
   sensitivity score destroys exactly the thresholding a privacy team needs.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_warehouse_columns.json"
CHARS_PER_TOKEN = 4

# The taxonomy. `not_personal_data` is the no-match option -- without it a
# Choice must file every surrogate key somewhere personal.
CATEGORIES: dict[str, str] = {
    "direct_identifier": (
        "Names a specific individual outright, or is a per-person identifier the "
        "organisation itself can trivially resolve back to one named human."
    ),
    "contact_detail": (
        "A channel for reaching a person: email address, phone number, postal "
        "address, messaging handle. Not the content of the message."
    ),
    "government_id": (
        "An identifier issued by a state or public body: national insurance or "
        "social security number, passport, driving licence, tax reference."
    ),
    "financial_instrument": (
        "Bank account, IBAN, sort code, payment card, or a fragment of one such "
        "as the last four digits. Not an order total or a price."
    ),
    "location": (
        "Where a person is or lives: postcode, city, coordinates, geo-derived "
        "region. Not a warehouse or store location that belongs to the company."
    ),
    "device_or_online_identifier": (
        "Identifies a device, browser or session rather than a person by name: "
        "IP address, cookie or session id, advertising id, user agent string, "
        "device fingerprint, hashed or pseudonymous user key."
    ),
    "demographic_attribute": (
        "A stable personal attribute that describes the individual: date of "
        "birth, age, gender, nationality, marital status, language."
    ),
    "behavioural_record": (
        "A record of something the person did or received: a page view, a "
        "purchase, a support note, an opt-in flag, a rating they gave."
    ),
    "derived_or_inferred": (
        "Not collected from the person but computed about them: a segment, a "
        "propensity score, a risk band, a predicted attribute."
    ),
    "not_personal_data": (
        "Relates to no individual at all -- a surrogate key with no person "
        "behind it, a currency code, a product attribute, a system timestamp, "
        "an internal status. This is the correct answer whenever the column "
        "name merely sounds personal but the profile shows otherwise."
    ),
}

# Cheap shape detectors. Tolerant of masking (*) because a responsible profile
# is built from masked samples. These are HINTS in the state, never a filter
# that drops a column -- ua_string and absence_reason_code match nothing here.
PATTERNS: dict[str, re.Pattern[str]] = {
    "email_shape": re.compile(r"[\w*.+-]+@[\w*-]+\.[\w*.-]+"),
    "phone_shape": re.compile(r"^\+?\d[\d\s()*]{6,}$"),
    "iban_shape": re.compile(r"\b[A-Z]{2}\d{2}[\sA-Z0-9*]{6,}", re.IGNORECASE),
    "ip_shape": re.compile(r"\b[\d*]{1,3}\.[\d*]{1,3}\.[\d*]{1,3}\.[\d*]{1,3}"),
    "long_opaque_token": re.compile(r"^[0-9a-f*\-]{16,}$", re.IGNORECASE),
    "date_shape": re.compile(r"\b[\d*]{4}-[\d*]{2}-[\d*]{2}"),
}

# Categories that policy masks regardless of how the Nouls landed. Policy is
# Python, so changing this re-runs no inference.
ALWAYS_MASK = ("government_id", "financial_instrument")
TIERS = ("mask", "restrict", "free")


def profile_column(raw: dict, *, include_examples: bool) -> dict:
    """Everything the model is told about one column. Python computes all of it.

    `include_examples=False` genuinely omits the sample values from the state --
    the key is not present, so nothing sensitive leaves the machine.
    """
    rows = max(int(raw.get("row_count", 0) or 0), 1)
    distinct = int(raw.get("distinct_count", 0) or 0)
    examples = [str(value) for value in raw.get("examples", [])]
    hits = sorted(name for name, pattern in PATTERNS.items()
                  if any(pattern.search(value) for value in examples))

    profile = {
        "table": raw.get("table", ""),
        "column": raw.get("column", ""),
        "type": raw.get("type", ""),
        "null_rate": round(float(raw.get("null_rate", 0.0) or 0.0), 3),
        "distinct_values": distinct,
        "uniqueness": round(min(distinct / rows, 1.0), 4),
        "regex_shapes_found": hits or ["none"],
    }
    if include_examples:
        profile["examples"] = examples
    return profile


def batch_profiles(profiles: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so one request's state fits the provider's advertised window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for profile in profiles:
        size = max(1, len(json.dumps(profile)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(profile)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(profiles: list[dict], *, include_examples: bool) -> dict:
    """One Choice + three Nouls per column, all in a single request.

    The three Nouls are separate on purpose: `users.date_of_birth` is not a
    direct identifier, is a textbook quasi-identifier, and is not special
    category. `hr.absence_reason_code` is none, weakly yes, and emphatically
    yes. One rubric could not express either row.
    """
    questions: dict[str, dict] = {}
    for index in range(len(profiles)):
        reference = {
            "table": f"`columns[{index}].table`",
            "column_name": f"`columns[{index}].column`",
            "storage_type": f"`columns[{index}].type`",
            "fraction_null": f"`columns[{index}].null_rate`",
            "distinct_values": f"`columns[{index}].distinct_values`",
            "uniqueness_ratio": f"`columns[{index}].uniqueness`",
            "regex_shapes_found": f"`columns[{index}].regex_shapes_found`",
            "note": (
                "uniqueness near 1.0 means nearly every row holds a different value; "
                "near 0 means a handful of values repeat across the table. "
                "regex_shapes_found lists only the SHAPES a pattern matcher spotted in "
                "the samples, and is 'none' for anything a pattern cannot describe."
            ),
        }
        if include_examples:
            reference["masked_examples"] = f"`columns[{index}].examples`"

        questions[f"cat_{index}"] = choice(
            {"task": "What kind of personal data, if any, does this warehouse column hold?",
             "column": reference},
            CATEGORIES,
        )
        questions[f"direct_{index}"] = noul(
            {"task": "On its own, does one value from this column pick out one specific person?",
             "column": reference},
            true="Somebody holding a single value from this column, and nothing else, could tell which individual the row is about",
            false="A single value is shared by many people, refers to no person, or is meaningless without joining to another table",
        )
        questions[f"quasi_{index}"] = noul(
            {"task": "Combined with two or three other columns in the same table, would this column narrow a row down to one person?",
             "column": reference},
            true="It carves the population into groups small enough that a few such columns together leave one individual",
            false="It stays coarse however it is combined, or it already identifies a person on its own without needing help",
        )
        questions[f"special_{index}"] = noul(
            {"task": "Does this column fall into a special category of data that needs extra protection under data-protection law?",
             "column": reference},
            true="It reveals health or sickness, biometrics, genetics, racial or ethnic origin, religion, political opinion, sexuality, or trade-union membership -- including indirectly, through a code or flag whose meaning is one of those",
            false="It reveals none of those, even if it is otherwise sensitive commercially or personally",
        )
    return questions


def decide(category: str, direct: float, quasi: float, special: float,
           *, mask_p: float, restrict_p: float) -> str:
    """Map judgments onto a handling tier. Pure Python: move a slider, re-derive
    every tier on screen, spend nothing."""
    if direct >= mask_p or special >= mask_p or category in ALWAYS_MASK:
        return "mask"
    if category == "not_personal_data" and max(direct, quasi, special) < restrict_p:
        return "free"
    if quasi >= restrict_p or direct >= restrict_p or special >= restrict_p:
        return "restrict"
    if category == "not_personal_data":
        return "free"
    return "restrict"


def load_columns(uploaded) -> list[dict]:
    if uploaded is not None:
        return json.loads(io.BytesIO(uploaded.read()).read().decode("utf-8"))
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def main() -> None:
    st.set_page_config(page_title="PII Scanner", page_icon="🔍", layout="wide")
    st.title("🔍 PII Scanner")
    st.caption("Which columns carry personal data — profiled by Python, judged by Jev.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Privacy")
        include_examples = st.toggle(
            "Send example values", value=False,
            help="Off: only names, types and statistics leave this machine. "
                 "On: the masked sample values go too, which is what catches free-text columns.",
        )
        if include_examples:
            st.warning("Sample values will be sent to the provider. Use masked or synthetic data.")
        else:
            st.caption("Names + profiles only. `support.note_body` gets much harder to judge.")
        st.divider()
        st.subheader("Handling policy")
        mask_p = st.slider("Mask above", 0.0, 1.0, 0.70, 0.05,
                           help="A column this likely to identify someone, or to be special category, must be masked.")
        restrict_p = st.slider("Restrict above", 0.0, 1.0, 0.35, 0.05,
                               help="Below the mask line but above this: keep it, gate access to it.")
        st.caption(f"Always masked by policy: {', '.join(ALWAYS_MASK)}")

    uploaded = st.file_uploader(
        "Column profiles JSON (table, column, type, row_count, null_rate, distinct_count, examples)",
        type=["json"],
    )
    try:
        raw_columns = load_columns(uploaded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        st.error(f"Could not parse that JSON: {exc}")
        return
    if not raw_columns:
        st.warning("No columns in that file.")
        return
    if uploaded is None:
        tables = sorted({row.get("table", "") for row in raw_columns})
        st.caption(f"Using the bundled sample: {len(raw_columns)} columns across {len(tables)} tables ({', '.join(tables)}).")

    profiles = [profile_column(row, include_examples=include_examples) for row in raw_columns]
    st.dataframe(pd.DataFrame(profiles).head(8), width="stretch")

    if not st.button(f"Scan {len(profiles)} columns", type="primary"):
        return

    groups = batch_profiles(profiles, int(provider.context_tokens * 0.5))
    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_seconds = 0.0
    total_questions = 0
    progress = st.progress(0.0, text="Scanning...")

    try:
        with JevClient(provider=provider) as client:
            for group_index, group in enumerate(groups):
                questions = build_questions(group, include_examples=include_examples)
                answers = client.ask({"columns": group}, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_seconds += answers.elapsed_s
                total_questions += len(questions)
                for index, profile in enumerate(group):
                    results.append({
                        "table": profile["table"],
                        "column": profile["column"],
                        "type": profile["type"],
                        "uniqueness": profile["uniqueness"],
                        "regex_shapes": ", ".join(profile["regex_shapes_found"]),
                        "category": answers.choice(f"cat_{index}"),
                        "category_confidence": answers.confidence(f"cat_{index}"),
                        "identifies_alone": answers.noul(f"direct_{index}"),
                        "quasi_identifier": answers.noul(f"quasi_{index}"),
                        "special_category": answers.noul(f"special_{index}"),
                    })
                progress.progress((group_index + 1) / len(groups), text="Scanning...")
    except JevError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)
    out["tier"] = [
        decide(row.category, row.identifies_alone, row.quasi_identifier, row.special_category,
               mask_p=mask_p, restrict_p=restrict_p)
        for row in out.itertuples()
    ]

    # ── Every number below is pandas. The model was asked for none of them. ──
    counts = out["tier"].value_counts()
    columns = st.columns(4)
    columns[0].metric("Columns scanned", len(out))
    columns[1].metric("Must mask", int(counts.get("mask", 0)))
    columns[2].metric("Restrict access", int(counts.get("restrict", 0)))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Handling tier by table")
        crosstab = pd.crosstab(out["table"], out["tier"])
        for tier in TIERS:
            if tier not in crosstab.columns:
                crosstab[tier] = 0
        st.dataframe(crosstab[list(TIERS)], width="stretch")
    with right:
        st.subheader("Category spread")
        st.bar_chart(out["category"].value_counts())

    st.subheader("Special category data")
    st.caption(
        "A separate Noul, because a column can be special category while being "
        "useless as an identifier — a two-letter absence code is the classic case."
    )
    special = out[out["special_category"] >= restrict_p].sort_values("special_category", ascending=False)
    if special.empty:
        st.caption(f"Nothing above P={restrict_p:.2f}.")
    else:
        st.dataframe(
            special[["table", "column", "category", "special_category", "identifies_alone", "tier"]],
            width="stretch", hide_index=True,
        )

    st.subheader("All three true at once")
    st.caption("The rows that justify three Nouls instead of one sensitivity score.")
    triple = out[(out["identifies_alone"] >= restrict_p)
                 & (out["quasi_identifier"] >= restrict_p)
                 & (out["special_category"] >= restrict_p)]
    if triple.empty:
        st.caption("None in this schema at the current thresholds.")
    else:
        st.dataframe(
            triple[["table", "column", "category", "identifies_alone",
                    "quasi_identifier", "special_category", "tier"]],
            width="stretch", hide_index=True,
        )

    st.subheader("Full register")
    st.dataframe(
        out.sort_values(["tier", "identifies_alone"], ascending=[True, False]),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download PII register CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="pii_register.csv",
        mime="text/csv",
    )
    st.caption(
        f"{len(groups)} request(s) · {total_questions} questions · {total_tokens:,} input tokens · "
        f"{total_seconds:.2f}s · ${total_cost:.6f} total, ${total_cost / len(out):.8f} per column. "
        f"Example values were {'included in' if include_examples else 'withheld from'} the state."
    )


if __name__ == "__main__":
    main()
