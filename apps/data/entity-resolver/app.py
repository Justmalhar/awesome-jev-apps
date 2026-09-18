"""Entity Resolver -- decide which records in a messy list are the same thing.

Two systems got merged, or a catalog came from three suppliers, and now you have
"Acme Corp", "ACME Corporation" and "Acme Corp." as three customers.

Two ideas worth stealing:

1. BLOCKING IN CODE. Comparing every pair of n records is n(n-1)/2 -- 500 records
   is 124,750 pairs. A cheap lexical filter (difflib) throws away the 99% that
   are obviously unrelated, and Jev judges only what survives. The app prints the
   reduction. This is the "code finds candidates, model judges" pattern again,
   and here it is the difference between affordable and absurd.

2. THE SCORE LEVELS *ARE* THE ACTIONS. Rather than asking "how similar are
   these?" and then hunting for a cutoff, the three levels are the three things
   you can do with a pair: merge it, leave it, or hand it to a person. There is
   no similarity threshold to fit, because the rubric already encodes the
   decision.

    uv run streamlit run app.py
"""

from __future__ import annotations

import difflib
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_records.csv"

# The rubric IS the decision. Read these as instructions to an operator.
LEVELS = [
    "Clearly different real-world entities. Keeping them separate is correct.",
    "Possibly the same entity, but the records conflict or are too thin to be sure. A person should look.",
    "Confidently the same real-world entity, written differently. Safe to merge automatically.",
]


def blocking_pairs(records: list[dict], cutoff: float) -> list[tuple[int, int, float]]:
    """Cheap lexical prefilter. Recall-oriented: better a junk pair than a miss."""
    keys = [
        " ".join(str(record.get(field, "")) for field in record).lower()
        for record in records
    ]
    pairs = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            ratio = difflib.SequenceMatcher(None, keys[i], keys[j]).ratio()
            if ratio >= cutoff:
                pairs.append((i, j, ratio))
    return sorted(pairs, key=lambda triple: -triple[2])


class Union:
    """Union-find, so merge decisions compose into clusters transitively."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a

    def clusters(self) -> dict[int, list[int]]:
        groups: dict[int, list[int]] = {}
        for node in range(len(self.parent)):
            groups.setdefault(self.find(node), []).append(node)
        return groups


def build_questions(pairs: list[tuple[int, int, float]]) -> dict:
    return {
        f"pair_{index}": score(
            {
                "task": "Do these two records describe the same real-world entity?",
                "record_a": f"`pairs[{index}].a`",
                "record_b": f"`pairs[{index}].b`",
                "note": "Different branches, subsidiaries, or product variants are DIFFERENT entities.",
            },
            LEVELS,
        )
        for index in range(len(pairs))
    }


def main() -> None:
    st.set_page_config(page_title="Entity Resolver", page_icon="🔗", layout="wide")
    st.title("🔗 Entity Resolver")
    st.caption("Find the duplicates two merged systems left behind.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Blocking")
        cutoff = st.slider(
            "Lexical similarity to even consider a pair", 0.1, 0.95, 0.45, 0.05,
            help="Lower = more pairs sent to Jev = higher recall and higher cost.",
        )
        st.caption("This filter is pure Python and costs nothing. Keep it generous.")
        st.divider()
        batch_size = st.slider("Pairs per request", 10, 150, 60, 10)

    uploaded = st.file_uploader("Records CSV", type=["csv"])
    if uploaded:
        frame = pd.read_csv(io.BytesIO(uploaded.read()))
    elif SAMPLE.is_file():
        frame = pd.read_csv(SAMPLE)
        st.caption(f"Using the bundled sample: {len(frame)} records from two merged systems.")
    else:
        st.warning("Upload a CSV of records.")
        return

    st.dataframe(frame.head(10), width="stretch")
    records = frame.to_dict("records")

    total_possible = len(records) * (len(records) - 1) // 2
    pairs = blocking_pairs(records, cutoff)

    columns = st.columns(3)
    columns[0].metric("Records", len(records))
    columns[1].metric("All possible pairs", f"{total_possible:,}")
    columns[2].metric(
        "After blocking", f"{len(pairs):,}",
        delta=f"-{(1 - len(pairs) / total_possible) * 100:.1f}%" if total_possible else None,
    )

    if not pairs:
        st.info("Blocking removed every pair. Lower the cutoff.")
        return
    if not st.button(f"Judge {len(pairs)} candidate pairs", type="primary"):
        return

    batches = [pairs[i : i + batch_size] for i in range(0, len(pairs), batch_size)]
    judged: list[tuple[int, int, float, float]] = []
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0

    try:
        with st.spinner(f"Judging in {len(batches)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in batches:
                    state = {
                        "pairs": [
                            {"a": records[i], "b": records[j]} for i, j, _ in group
                        ]
                    }
                    answers = client.ask(state, build_questions(group))
                    total_cost += answers.cost_usd
                    total_tokens += answers.input_tokens
                    total_elapsed += answers.elapsed_s
                    for index, (i, j, ratio) in enumerate(group):
                        judged.append(
                            (i, j, answers.score(f"pair_{index}"), ratio)
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    # Level 2 is "safe to merge automatically" -- so >= 1.5 leans to merge,
    # <= 0.5 leans to separate, and the middle is the curator queue. These come
    # straight from the rubric; there is no similarity cutoff being tuned.
    merges = [(i, j, s, r) for i, j, s, r in judged if s >= 1.5]
    curate = [(i, j, s, r) for i, j, s, r in judged if 0.5 < s < 1.5]

    union = Union(len(records))
    for i, j, _, _ in merges:
        union.union(i, j)
    clusters = {root: members for root, members in union.clusters().items() if len(members) > 1}

    columns = st.columns(4)
    columns[0].metric("Auto-merge pairs", len(merges))
    columns[1].metric("Clusters formed", len(clusters))
    columns[2].metric("For a curator", len(curate))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    st.subheader(f"Duplicate clusters ({len(clusters)})")
    st.caption("Built by union-find in Python, so A=B and B=C makes one cluster of three.")
    for members in sorted(clusters.values(), key=len, reverse=True):
        with st.container(border=True):
            st.dataframe(frame.iloc[members], width="stretch")

    st.subheader(f"⚠️ Curator queue ({len(curate)})")
    st.caption("The middle level. Not a low score on a similarity scale — a different instruction.")
    for i, j, level, ratio in sorted(curate, key=lambda t: -t[2]):
        with st.container(border=True):
            st.caption(f"score {level:.2f}/2 · lexical {ratio:.2f}")
            st.dataframe(frame.iloc[[i, j]], width="stretch")

    st.divider()
    st.caption(
        f"{len(pairs)} pairs judged in {len(batches)} request(s) · {len(pairs)} question(s) · "
        f"{total_tokens:,} tokens · {total_elapsed:.2f}s · "
        f"${total_cost:.6f}. Comparing all {total_possible:,} pairs would have cost "
        f"~{total_possible / max(len(pairs), 1):.0f}x more."
    )


if __name__ == "__main__":
    main()
