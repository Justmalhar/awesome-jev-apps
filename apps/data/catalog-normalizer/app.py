"""Catalog Normalizer -- unify product attributes across inconsistent supplier feeds.

Three suppliers send the same products and write every attribute their own way:
"Blk" / "black" / "Jet Black"; "M" / "Medium" / "Med"; "500ml" / "0.5 L" / "50cl".
A merchandiser reconciles it by hand, forever.

THE POINT OF THIS APP is the no-match option. Every Choice carries
`not_in_vocabulary`, which means *the value is real, and the canonical
vocabulary has no term for it*. That is not a failure -- it is the signal to
extend the vocabulary. A Choice without that option has to nominate the
least-wrong term, so "Merino Wool" gets filed as Cotton at unremarkable
confidence and nobody ever finds out.

THE CRITICAL DIVISION OF LABOUR: units are arithmetic, and `jev-1.13` cannot do
arithmetic. The model is never asked to turn "0.5 L" into millilitres. A regex in
Python parses the magnitude and the unit and multiplies; Jev decides only
*which attribute* a raw value belongs to, and *which canonical term* a
non-numeric value maps to. That attribute decision is not busywork -- "16 oz" is
473 ml of shampoo or 454 g of coffee beans, and only the product context tells
you which, so Jev picks the attribute and Python applies the factor.

THE BIG SAVING is deduplication. The same raw value appears across thousands of
rows, so only DISTINCT (attribute, value) pairs are resolved and the mapping is
joined back with pandas. The footer prints the reduction.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice

HERE = Path(__file__).resolve().parent
SAMPLE_FEED = HERE / "sample_supplier_feed.csv"
SAMPLE_VOCAB = HERE / "sample_vocabulary.json"

FEED_COLUMNS = ("sku", "supplier_id", "raw_attribute", "raw_value", "product_title")

# A magnitude and a unit. Everything numeric stops here and never reaches the model.
MEASURE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Za-z][A-Za-z. ]*?)\s*$")

NO_ATTRIBUTE = "not_an_attribute"
NO_TERM = "not_in_vocabulary"


def load_vocabulary(uploaded=None) -> dict:
    if uploaded is not None:
        return json.loads(uploaded.read().decode("utf-8"))
    return json.loads(SAMPLE_VOCAB.read_text(encoding="utf-8"))


def parse_measure(raw_value: str) -> tuple[float, str] | None:
    """Split "0.5 L" into (0.5, "l"). Python's job, start to finish."""
    match = MEASURE_RE.match(str(raw_value))
    if not match:
        return None
    magnitude = float(match.group(1).replace(",", "."))
    unit = re.sub(r"\s+", " ", match.group(2).replace(".", "").strip()).lower()
    return (magnitude, unit) if unit else None


def distinct_items(frame: pd.DataFrame) -> list[dict]:
    """One work item per DISTINCT (attribute, value). This is the whole saving."""
    items: list[dict] = []
    grouped = frame.groupby(["raw_attribute", "raw_value"], sort=False)
    for (attribute, value), group in grouped:
        items.append(
            {
                "labelled_attribute": str(attribute),
                "raw_value": str(value),
                "seen_in_products": sorted(group["product_title"].astype(str).unique())[:3],
                "row_count": int(len(group)),
                "measure": parse_measure(value),
            }
        )
    return items


def attribute_options(vocabulary: dict) -> dict[str, str]:
    options = {name: spec["description"] for name, spec in vocabulary.items()}
    options[NO_ATTRIBUTE] = (
        "This value does not describe any attribute above: it is a product name, a "
        "SKU, a marketing phrase, an empty placeholder, or otherwise not an attribute value."
    )
    return options


def term_options(vocabulary: dict) -> dict[str, str]:
    """Every term from every non-numeric attribute, namespaced, in one Choice.

    Offering the union rather than one attribute's terms is deliberate: it is how
    a value the supplier filed under the wrong attribute gets caught, and it keeps
    everything in a single request.
    """
    options: dict[str, str] = {}
    for attribute, spec in vocabulary.items():
        if spec.get("kind") != "term":
            continue
        for term, description in spec["terms"].items():
            options[f"{attribute}:{term}"] = description
    options[NO_TERM] = (
        "The value is a real attribute value, but no canonical term above means the "
        "same thing. Choose this rather than the closest term -- it is the signal to "
        "extend the vocabulary, not to force a value into it."
    )
    return options


def build_questions(items: list[dict], vocabulary: dict) -> dict:
    """One Choice for the attribute always; a term Choice only for non-numeric values."""
    attributes = attribute_options(vocabulary)
    terms = term_options(vocabulary)
    questions: dict[str, dict] = {}
    for index, item in enumerate(items):
        reference = {
            "supplier_called_it": f"`values[{index}].labelled_attribute`",
            "raw_value": f"`values[{index}].raw_value`",
            "products_it_appears_on": f"`values[{index}].seen_in_products`",
        }
        questions[f"attr_{index}"] = choice(
            {
                "task": "Which canonical product attribute does this raw supplier value actually describe?",
                "value": reference,
                "note": (
                    "The supplier's own label is often wrong -- judge the value itself, "
                    "using the products it appears on as context."
                ),
                "units": (
                    "The numeric magnitude and its unit have already been separated in code. "
                    "Decide only which attribute the measurement belongs to: the same unit "
                    "abbreviation can mean a capacity on a drink and a mass on dry goods."
                ),
            },
            attributes,
        )
        if item["measure"] is None:
            questions[f"term_{index}"] = choice(
                {
                    "task": "Which term in the canonical vocabulary means the same thing as this raw value?",
                    "value": reference,
                    "note": (
                        "Options are written attribute:Term. Pick the term that means the same "
                        "thing, from whichever attribute it belongs to."
                    ),
                },
                terms,
            )
    return questions


def resolve_value(item: dict, attribute: str, term: str | None, vocabulary: dict) -> str | None:
    """Turn the model's pick into a canonical value. Every number here is Python's."""
    spec = vocabulary.get(attribute)
    if spec is None:
        return None
    if spec.get("kind") == "measure":
        if item.get("measure") is None:
            return None
        magnitude, unit = item["measure"]
        factor = spec.get("units", {}).get(unit)
        if factor is None:
            return None
        return f"{round(magnitude * factor, 4):g} {spec['base_unit']}"
    if term and term.startswith(f"{attribute}:"):
        return term.split(":", 1)[1]
    return None


def decide(attribute: str, term: str | None, canonical: str | None,
           confidence: float, labelled_attribute: str, gate: float) -> str:
    """Policy, entirely in Python, so re-tuning the gate costs no inference.

    Confidence gates only the automatic path. The two interesting outcomes --
    the supplier filed it under the wrong attribute, and the vocabulary has no
    term for it -- are routed to a human whatever the confidence, because both
    are decisions about the vocabulary rather than about this row.
    """
    if attribute == NO_ATTRIBUTE:
        return "reject"
    if attribute != labelled_attribute:
        return "relabel"
    if term == NO_TERM:
        return "extend_vocabulary"
    if canonical is None:
        return "curator"
    return "auto_accept" if confidence >= gate else "curator"


def batch_items(items: list[dict], context_tokens: int, fill: float = 0.35) -> list[list[dict]]:
    """Size batches against the provider's real window, never a hardcoded number.

    ~4 chars per token. The fill is conservative because the vocabulary is
    repeated in the option descriptions of every question in the batch.
    """
    budget = max(1, int(context_tokens * fill * 4))
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for item in items:
        cost = len(json.dumps(item, ensure_ascii=False, default=str))
        if current and size + cost > budget:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += cost
    if current:
        batches.append(current)
    return batches


def load_feed(uploaded) -> pd.DataFrame | None:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()), keep_default_na=False, dtype=str)
    if SAMPLE_FEED.is_file():
        return pd.read_csv(SAMPLE_FEED, keep_default_na=False, dtype=str)
    return None


def main() -> None:
    st.set_page_config(page_title="Catalog Normalizer", page_icon="🏷️", layout="wide")
    st.title("🏷️ Catalog Normalizer")
    st.caption("Three suppliers, three spellings, one vocabulary. Jev maps the words, Python does the units.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token window", language=None)
        st.divider()
        st.subheader("Policy")
        gate = st.slider(
            "Auto-accept a mapping above this confidence", 0.0, 1.0, 0.70, 0.05,
            help="Below it, the value goes to the curator queue instead of the mapping table.",
        )
        fill = st.slider(
            "Share of the context window to fill", 0.10, 0.60, 0.35, 0.05,
            help=f"Batches are sized against {provider.context_tokens:,} tokens, not a constant.",
        )
        st.divider()
        st.caption(
            "Changing the gate re-derives every table below without a single new "
            "request — the probabilities are already in the result frame."
        )

    left, right = st.columns(2)
    feed_file = left.file_uploader(
        f"Supplier feed CSV ({', '.join(FEED_COLUMNS)})", type=["csv"]
    )
    vocab_file = right.file_uploader("Canonical vocabulary JSON", type=["json"])

    frame = load_feed(feed_file)
    if frame is None:
        st.warning("Upload a feed CSV, or put sample_supplier_feed.csv next to this app.")
        return
    missing = set(FEED_COLUMNS) - set(frame.columns)
    if missing:
        st.error(f"Feed is missing required column(s): {', '.join(sorted(missing))}")
        return

    try:
        vocabulary = load_vocabulary(vocab_file)
    except (OSError, ValueError) as exc:
        st.error(f"Could not read the vocabulary: {exc}")
        return

    if feed_file is None:
        st.caption(
            f"Using the bundled sample: {len(frame)} rows from "
            f"{frame['supplier_id'].nunique()} suppliers."
        )
    st.dataframe(frame.head(8), width="stretch")

    items = distinct_items(frame)
    batches = batch_items(items, provider.context_tokens, fill)
    measures = sum(1 for item in items if item["measure"] is not None)
    question_count = 2 * len(items) - measures  # measures skip the term question

    columns = st.columns(4)
    columns[0].metric("Feed rows", len(frame))
    columns[1].metric("Distinct values to resolve", len(items),
                      delta=f"-{(1 - len(items) / len(frame)) * 100:.0f}%")
    columns[2].metric("Parsed as a measurement in Python", measures)
    columns[3].metric("Questions / requests", f"{question_count} / {len(batches)}")

    with st.expander("What Python already resolved without asking anything"):
        st.caption(
            "Magnitude and unit, split by one regex. The model is never shown a "
            "conversion and never asked for one."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"raw_value": item["raw_value"], "magnitude": item["measure"][0],
                     "unit": item["measure"][1], "rows": item["row_count"]}
                    for item in items if item["measure"] is not None
                ]
            ),
            width="stretch", hide_index=True,
        )

    if not st.button(f"Resolve {len(items)} distinct values", type="primary"):
        return

    resolved: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0
    total_questions = 0
    progress = st.progress(0.0, text="Resolving...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, group in enumerate(batches):
                questions = build_questions(group, vocabulary)
                answers = client.ask({"values": group}, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_elapsed += answers.elapsed_s
                total_questions += len(questions)
                for index, item in enumerate(group):
                    attribute = answers.choice(f"attr_{index}")
                    confidence = answers.confidence(f"attr_{index}")
                    term = None
                    if f"term_{index}" in questions:
                        term = answers.choice(f"term_{index}")
                        confidence = min(confidence, answers.confidence(f"term_{index}"))
                    canonical = resolve_value(item, attribute, term, vocabulary)
                    resolved.append(
                        {
                            "raw_attribute": item["labelled_attribute"],
                            "raw_value": item["raw_value"],
                            "resolved_attribute": attribute,
                            "canonical_value": canonical,
                            "term_pick": term,
                            "confidence": confidence,
                            "rows_covered": item["row_count"],
                            "status": decide(attribute, term, canonical, confidence,
                                             item["labelled_attribute"], gate),
                        }
                    )
                progress.progress((batch_index + 1) / len(batches), text="Resolving...")
    except JevError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    mapping = pd.DataFrame(resolved)
    merged = frame.merge(mapping, on=["raw_attribute", "raw_value"], how="left")

    # ── Every number from here down is pandas over the returned labels. ──
    counts = mapping["status"].value_counts()
    accepted = int(counts.get("auto_accept", 0))
    row_coverage = int(mapping.loc[mapping["status"] == "auto_accept", "rows_covered"].sum())

    columns = st.columns(5)
    columns[0].metric("Auto-accepted values", f"{accepted}/{len(mapping)}")
    columns[1].metric("Feed rows they cover", f"{row_coverage} ({row_coverage / len(frame) * 100:.0f}%)")
    columns[2].metric("Vocabulary gaps", int(counts.get("extend_vocabulary", 0)))
    columns[3].metric("Wrong attribute", int(counts.get("relabel", 0)))
    columns[4].metric("Cost", f"${total_cost:.6f}")

    st.subheader(f"🧩 Vocabulary gaps ({int(counts.get('extend_vocabulary', 0))})")
    st.caption(
        "`not_in_vocabulary` won. These values are real and nothing canonical covers "
        "them — the output of this app is a proposal to extend the vocabulary, not a "
        "forced mapping to the nearest term. Without this option every one of these "
        "would have been filed under a plausible-looking wrong term."
    )
    st.dataframe(
        mapping[mapping["status"] == "extend_vocabulary"][
            ["raw_attribute", "raw_value", "resolved_attribute", "confidence", "rows_covered"]
        ],
        width="stretch", hide_index=True,
    )

    st.subheader(f"🔀 Filed under the wrong attribute ({int(counts.get('relabel', 0))})")
    st.caption("The supplier's column header disagrees with the value in it.")
    st.dataframe(
        mapping[mapping["status"] == "relabel"][
            ["raw_attribute", "raw_value", "resolved_attribute", "canonical_value", "confidence"]
        ],
        width="stretch", hide_index=True,
    )

    st.subheader(f"✅ Accepted mapping ({accepted})")
    st.caption("Join this back onto any feed. It is a lookup table, not a per-row inference.")
    st.dataframe(
        mapping[mapping["status"] == "auto_accept"][
            ["raw_attribute", "raw_value", "canonical_value", "confidence", "rows_covered"]
        ].sort_values(["raw_attribute", "canonical_value"]),
        width="stretch", hide_index=True,
    )

    queue = int(counts.get("curator", 0)) + int(counts.get("reject", 0))
    st.subheader(f"⚠️ Curator queue ({queue})")
    st.caption(
        "A Choice always returns a term. A flat distribution is the signal it was "
        "guessing, and an unconvertible unit is the signal Python could not finish "
        "the job. Both land here rather than in the mapping."
    )
    st.dataframe(
        mapping[mapping["status"].isin(["curator", "reject"])][
            ["raw_attribute", "raw_value", "resolved_attribute", "term_pick", "confidence", "status"]
        ].sort_values("confidence"),
        width="stretch", hide_index=True,
    )

    st.subheader("Normalized feed")
    st.dataframe(
        merged[["sku", "supplier_id", "product_title", "raw_attribute", "raw_value",
                "resolved_attribute", "canonical_value", "status"]],
        width="stretch", hide_index=True,
    )

    st.subheader("One row per SKU, agreed across suppliers")
    st.caption("A cell with two values separated by | is a genuine supplier disagreement, not a normalization failure.")
    unified = (
        merged[merged["status"] == "auto_accept"]
        .groupby(["sku", "resolved_attribute"])["canonical_value"]
        .agg(lambda values: " | ".join(sorted(set(values))))
        .unstack(fill_value="")
        if accepted
        else pd.DataFrame()
    )
    if unified.empty:
        st.caption("Nothing was auto-accepted at this gate — lower it in the sidebar.")
    else:
        st.dataframe(unified, width="stretch")

    st.download_button(
        "Download value mapping CSV",
        mapping.to_csv(index=False).encode("utf-8"),
        file_name="attribute_mapping.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{len(frame)} feed rows collapsed to {len(mapping)} distinct values "
        f"({len(frame) / len(mapping):.1f}x fewer) · {total_questions} questions in "
        f"{len(batches)} request(s) · {total_tokens:,} input tokens · {total_elapsed:.2f}s · "
        f"${total_cost:.6f} total, ${total_cost / len(frame):.8f} per feed row. "
        f"Batches were sized against this provider's {provider.context_tokens:,}-token window."
    )


if __name__ == "__main__":
    main()
