"""Schema Mapper -- decide which source column feeds which target field.

A CRM export has 40 columns. The warehouse schema it has to land in has 35
fields. Somebody has to say, for every one of those 1,400 combinations, whether
`cust_nm` is `customer_full_name` or `contact_first_name`, and whether
`billing_email` is `invoice_email` or `primary_contact_email`.

Three ideas worth stealing:

1. PREFILTER IN PYTHON. difflib over abbreviation-expanded names, plus a coarse
   type-compatibility test, throws away the pairs that are obviously unrelated
   before a single token is spent. The app prints the reduction.

2. THE SCORE LEVELS *ARE* THE ACTIONS. Not "how similar are these fields?" but
   "do not map these" / "a data engineer should confirm" / "safe to map
   automatically". There is no similarity cutoff being fitted, because the
   rubric already encodes what you do with the answer.

3. SAMPLE VALUES IN THE STATE. Names lie -- `phone` and `mobile_phone` look
   nearly identical and hold different things; `dt_created` and `created_at`
   look different and hold the same thing. Three real values per field settle
   both arguments, which is why the state carries name, type AND samples.

The one-to-one assignment (a target field can receive exactly one source
column) is greedy Python over the scores. The model never sees the other pairs'
answers, never ranks anything, and never counts.

    uv run streamlit run app.py
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, score

HERE = Path(__file__).resolve().parent
SAMPLE_SOURCE = HERE / "sample_source_schema.json"
SAMPLE_TARGET = HERE / "sample_target_schema.json"

# The rubric IS the decision. Read each level as an instruction to a migration engineer.
LEVELS = [
    "These two fields hold unrelated data. Copying one into the other would put wrong values in the warehouse column.",
    "Plausibly the same field, but the names, the declared types or the sample values disagree. A data engineer should confirm before this mapping ships.",
    "The same field under a different name. The sample values are visibly the same kind of thing, so this mapping is safe to apply without review.",
]

# Legacy column names are abbreviations. Expanding them before the lexical
# compare is what lets `cust_nm` reach `customer_full_name` at all.
ABBREV = {
    "acct": "account", "addr": "address", "amt": "amount", "ann": "annual",
    "cd": "code", "cnt": "count", "cntry": "country", "cust": "customer",
    "desc": "description", "dt": "date", "emp": "employee", "fname": "first name",
    "lang": "language", "ln": "line", "lname": "last name", "mgr": "manager",
    "mob": "mobile", "nm": "name", "no": "number", "num": "number",
    "opp": "opportunity", "pct": "percent", "pref": "preferred",
    "prob": "probability", "prov": "province", "qty": "quantity",
    "rev": "revenue", "src": "source", "st": "state", "tel": "telephone",
    "ts": "timestamp", "txt": "text",
}

# Coarse buckets. "text" is compatible with everything on purpose: legacy CRMs
# store dates, flags and money in VARCHAR columns, and this filter must not be
# the thing that loses a real mapping.
TYPE_GROUP = {
    "string": "text", "text": "text", "varchar": "text", "char": "text",
    "integer": "number", "int": "number", "bigint": "number", "float": "number",
    "double": "number", "decimal": "number", "numeric": "number",
    "date": "time", "datetime": "time", "timestamp": "time",
    "boolean": "bool", "bool": "bool",
}

# Rough packing constants. Only used to size batches against the provider's real
# context window -- never to report anything to the user.
CHARS_PER_TOKEN = 4
QUESTION_TOKENS = 120  # the score() instruction plus three levels, per pair


def normalize(name: str) -> str:
    """`dt_created` -> `created date`. Tokens sorted so word order stops mattering."""
    tokens: list[str] = []
    for part in re.findall(r"[a-z]+|\d+", name.lower()):
        tokens.extend(ABBREV.get(part, part).split())
    return " ".join(sorted(tokens))


def type_compatible(source_type: str, target_type: str) -> bool:
    """Same bucket, or either side is free-text and could hold anything."""
    a = TYPE_GROUP.get(str(source_type).lower(), "text")
    b = TYPE_GROUP.get(str(target_type).lower(), "text")
    return a == b or "text" in (a, b)


def candidate_pairs(
    source: list[dict], target: list[dict], cutoff: float
) -> list[tuple[int, int, float]]:
    """Cheap prefilter. Recall-oriented: a junk pair costs tokens, a miss costs a column."""
    source_keys = [normalize(field["name"]) for field in source]
    target_keys = [normalize(field["name"]) for field in target]
    pairs: list[tuple[int, int, float]] = []
    for i, source_field in enumerate(source):
        for j, target_field in enumerate(target):
            if not type_compatible(source_field.get("type", ""), target_field.get("type", "")):
                continue
            ratio = difflib.SequenceMatcher(None, source_keys[i], target_keys[j]).ratio()
            if ratio >= cutoff:
                pairs.append((i, j, ratio))
    return sorted(pairs, key=lambda triple: -triple[2])


def pair_state(source: list[dict], target: list[dict], pairs: list[tuple[int, int, float]]) -> dict:
    """Name, type and sample values for both sides. The samples do the work."""
    return {
        "pairs": [
            {"source": source[i], "target": target[j]} for i, j, _ in pairs
        ]
    }


def build_questions(pairs: list[tuple[int, int, float]]) -> dict:
    return {
        f"pair_{index}": score(
            {
                "task": "Should the source column be loaded into the target field during a migration?",
                "source_column": f"`pairs[{index}].source`",
                "target_field": f"`pairs[{index}].target`",
                "note": (
                    "Judge the data, not the spelling. Two fields with similar names that hold "
                    "different things (an accounts-payable address versus a named person's address) "
                    "are not the same field."
                ),
            },
            LEVELS,
        )
        for index in range(len(pairs))
    }


def batch_pairs(
    source: list[dict],
    target: list[dict],
    pairs: list[tuple[int, int, float]],
    context_tokens: int,
    headroom: float = 0.4,
) -> list[list[tuple[int, int, float]]]:
    """Pack pairs into requests sized against the provider's real context window.

    TypeSafe serves 64k and OpenRouter 32k, so this reads provider.context_tokens
    rather than assuming either. Headroom is deliberately conservative: the
    provider splits the window between state and questions.
    """
    budget = max(int(context_tokens * headroom), QUESTION_TOKENS * 2)
    batches: list[list[tuple[int, int, float]]] = []
    current: list[tuple[int, int, float]] = []
    used = 0
    for i, j, ratio in pairs:
        cost = QUESTION_TOKENS + len(
            json.dumps({"source": source[i], "target": target[j]}, default=str)
        ) // CHARS_PER_TOKEN
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append((i, j, ratio))
        used += cost
    if current:
        batches.append(current)
    return batches


def assign(scored: list[tuple[int, int, float]], floor: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one: best score first, each source and each target used once.

    Greedy rather than Hungarian on purpose -- the scores are ordinal levels, not
    costs, and a globally optimal assignment over a rubric is false precision.
    """
    used_source: set[int] = set()
    used_target: set[int] = set()
    chosen: list[tuple[int, int, float]] = []
    for i, j, value in sorted(scored, key=lambda triple: -triple[2]):
        if value < floor or i in used_source or j in used_target:
            continue
        used_source.add(i)
        used_target.add(j)
        chosen.append((i, j, value))
    return chosen


def schema_frame(fields: list[dict]) -> pd.DataFrame:
    """Sample values are a mixed-type list; flatten them so Arrow stays quiet."""
    return pd.DataFrame(
        [
            {
                "name": field.get("name", ""),
                "type": field.get("type", ""),
                "samples": ", ".join(str(value) for value in field.get("samples", [])),
            }
            for field in fields
        ]
    )


def load_schema(uploaded, fallback: Path) -> list[dict] | None:
    payload = json.loads(uploaded.getvalue().decode("utf-8")) if uploaded else (
        json.loads(fallback.read_text(encoding="utf-8")) if fallback.is_file() else None
    )
    if payload is None:
        return None
    fields = payload["fields"] if isinstance(payload, dict) else payload
    return [dict(field) for field in fields]


def main() -> None:
    st.set_page_config(page_title="Schema Mapper", page_icon="🗺️", layout="wide")
    st.title("🗺️ Schema Mapper")
    st.caption("Map one system's columns onto another's, with the sample values in the room.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token context", language=None)
        st.divider()
        st.subheader("Prefilter")
        cutoff = st.slider(
            "Lexical similarity to even consider a pair", 0.0, 0.9, 0.35, 0.05,
            help="Runs on abbreviation-expanded names. Lower = more pairs judged = higher recall and higher cost.",
        )
        st.caption("Pure Python, costs nothing. Keep it generous.")
        st.divider()
        st.subheader("Mapping policy")
        auto_at = st.slider("Auto-map at score above", 1.0, 2.0, 1.5, 0.05)
        review_at = st.slider("Send to a curator at score above", 0.0, 1.5, 0.5, 0.05)
        st.caption("Thresholds are applied in Python. Moving them re-derives every table below without a new request.")

    left, right = st.columns(2)
    source_upload = left.file_uploader("Source schema JSON", type=["json"], key="src")
    target_upload = right.file_uploader("Target schema JSON", type=["json"], key="tgt")

    source = load_schema(source_upload, SAMPLE_SOURCE)
    target = load_schema(target_upload, SAMPLE_TARGET)
    if not source or not target:
        st.warning("Upload two schema JSON files: a list of {name, type, samples} objects.")
        return
    if source_upload is None and target_upload is None:
        st.caption("Using the bundled sample: a LegacyCRM v4 export against a new warehouse dimension.")

    left.dataframe(schema_frame(source), width="stretch", height=240, hide_index=True)
    right.dataframe(schema_frame(target), width="stretch", height=240, hide_index=True)

    total_possible = len(source) * len(target)
    pairs = candidate_pairs(source, target, cutoff)

    columns = st.columns(4)
    columns[0].metric("Source columns", len(source))
    columns[1].metric("Target fields", len(target))
    columns[2].metric("All possible pairs", f"{total_possible:,}")
    columns[3].metric(
        "After prefilter", f"{len(pairs):,}",
        delta=f"-{(1 - len(pairs) / total_possible) * 100:.1f}%" if total_possible else None,
    )

    if not pairs:
        st.info("The prefilter removed every pair. Lower the cutoff.")
        return

    batches = batch_pairs(source, target, pairs, provider.context_tokens)
    if not st.button(f"Judge {len(pairs)} candidate pairs in {len(batches)} request(s)", type="primary"):
        return

    scored: list[tuple[int, int, float]] = []
    lexical: dict[tuple[int, int], float] = {}
    total_cost = 0.0
    total_tokens = 0
    total_seconds = 0.0
    question_count = 0
    progress = st.progress(0.0, text="Judging candidate pairs...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, group in enumerate(batches):
                questions = build_questions(group)
                answers = client.ask(pair_state(source, target, group), questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_seconds += answers.elapsed_s
                question_count += len(questions)
                for index, (i, j, ratio) in enumerate(group):
                    scored.append((i, j, answers.score(f"pair_{index}")))
                    lexical[(i, j)] = ratio
                progress.progress((batch_index + 1) / len(batches), text="Judging candidate pairs...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    # ── Everything below is Python. The model saw no assignment, no totals. ──
    chosen = assign(scored, review_at)
    auto = [triple for triple in chosen if triple[2] >= auto_at]
    curate = [triple for triple in chosen if triple[2] < auto_at]
    mapped_targets = {j for _, j, _ in chosen}
    unmapped_targets = [j for j in range(len(target)) if j not in mapped_targets]
    mapped_sources = {i for i, _, _ in chosen}
    unmapped_sources = [i for i in range(len(source)) if i not in mapped_sources]

    columns = st.columns(5)
    columns[0].metric("Auto-mapped", len(auto))
    columns[1].metric("Needs a curator", len(curate))
    columns[2].metric("Target fields left empty", len(unmapped_targets))
    columns[3].metric("Source columns dropped", len(unmapped_sources))
    columns[4].metric("Cost", f"${total_cost:.6f}")

    def frame(rows: list[tuple[int, int, float]]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "source": source[i]["name"],
                    "source_type": source[i].get("type", ""),
                    "target": target[j]["name"],
                    "target_type": target[j].get("type", ""),
                    "score": round(value, 3),
                    "lexical": round(lexical.get((i, j), 0.0), 3),
                    "source_samples": ", ".join(str(s) for s in source[i].get("samples", [])[:3]),
                    "target_samples": ", ".join(str(s) for s in target[j].get("samples", [])[:3]),
                }
                for i, j, value in rows
            ]
        )

    st.subheader(f"✅ Safe to map automatically ({len(auto)})")
    st.caption("Level 2 of the rubric. One source column per target field, decided greedily in Python.")
    st.dataframe(frame(sorted(auto, key=lambda t: -t[2])), width="stretch", hide_index=True)

    st.subheader(f"⚠️ Curator queue ({len(curate)})")
    st.caption("Level 1. Not a weak match on a similarity scale — a different instruction: someone confirms this one.")
    st.dataframe(frame(sorted(curate, key=lambda t: -t[2])), width="stretch", hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader(f"Target fields nothing filled ({len(unmapped_targets)})")
        st.caption("Either the source genuinely lacks the data, or the prefilter never offered the pair.")
        st.dataframe(schema_frame([target[j] for j in unmapped_targets]), width="stretch", hide_index=True)
    with right:
        st.subheader(f"Source columns with nowhere to go ({len(unmapped_sources)})")
        st.caption("Decide explicitly: drop, or add a column to the target schema.")
        st.dataframe(schema_frame([source[i] for i in unmapped_sources]), width="stretch", hide_index=True)

    everything = pd.DataFrame(
        [
            {
                "source": source[i]["name"],
                "target": target[j]["name"],
                "score": value,
                "lexical": lexical.get((i, j), 0.0),
                "assigned": (i, j) in {(a, b) for a, b, _ in chosen},
            }
            for i, j, value in sorted(scored, key=lambda t: -t[2])
        ]
    )
    st.download_button(
        "Download every judged pair (raw scores)",
        everything.to_csv(index=False).encode("utf-8"),
        file_name="schema_mapping_scores.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{question_count} questions over {len(batches)} request(s) · {total_tokens:,} input tokens · "
        f"{total_seconds:.1f}s · ${total_cost:.6f}. Judging all {total_possible:,} pairs would have been "
        f"~{total_possible / max(len(pairs), 1):.1f}x the tokens."
    )


if __name__ == "__main__":
    main()
