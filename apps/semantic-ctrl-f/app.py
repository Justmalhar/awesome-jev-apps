"""Semantic Ctrl-F -- ask a document a plain-English question, get the exact clause.

The whole search is ONE request. Every block in the document becomes an option in
a single Choice question (option key = block id, option description = block text),
so Jev scores all of them against the query in one pass over one shared state.
A companion Noul asks whether the document answers the question at all, which is
what stops the app confidently highlighting the least-wrong paragraph.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
# Rough chars-per-token; only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4
# A Choice needs every option's text in criteria, so block count is bounded by context.
MAX_BLOCKS = 300


def split_blocks(text: str) -> list[str]:
    """Split into numbered blocks: paragraphs, or lines for clause-numbered docs.

    Contracts and ToS are usually one clause per line; prose is paragraph-shaped.
    Pick whichever yields blocks that are not absurdly long.
    """
    paragraphs = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if paragraphs and max(len(p) for p in paragraphs) > 600:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines
    return paragraphs


def build_questions(blocks: list[str], query: str) -> dict:
    """One Choice across every block + one Noul guarding against no-answer."""
    options = {str(i): block for i, block in enumerate(blocks)}
    # Without this, the model must pick a block even when none is relevant.
    options["none"] = "No block in this document addresses the question."

    return {
        "best_block": choice(
            {
                "task": "Select the block id whose text most directly answers the question.",
                "question": query,
                "note": "Choose 'none' if no block addresses it, even partially.",
            },
            options,
        ),
        "has_answer": noul(
            {
                "task": "Does this document contain an answer to the question?",
                "question": query,
            },
            true="Some passage in the document directly addresses the question",
            false="The document is silent on this; answering would require outside knowledge",
        ),
    }


def main() -> None:
    st.set_page_config(page_title="Semantic Ctrl-F", page_icon="🔎", layout="wide")
    st.title("🔎 Semantic Ctrl-F")
    st.caption(
        "Ask a contract, lease, or ToS a plain-English question. "
        "The entire document is scored in **one** request."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.caption("Change `provider` in providers.toml, or set `JEV_PROVIDER`.")
        top_n = st.slider("Show top N blocks", 1, 10, 3)
        answer_threshold = st.slider(
            "P(document answers this) below which we warn", 0.0, 1.0, 0.5, 0.05
        )

    sample = HERE / "sample_lease.txt"
    default_text = sample.read_text(encoding="utf-8") if sample.is_file() else ""

    uploaded = st.file_uploader("Upload a .txt document", type=["txt"])
    text = uploaded.read().decode("utf-8", errors="replace") if uploaded else default_text
    text = st.text_area("Document", value=text, height=240)

    query = st.text_input(
        "Your question",
        placeholder="Can I keep a cat without asking the landlord?",
    )

    if not st.button("Search", type="primary") or not query.strip() or not text.strip():
        st.info("Paste a document, type a question, and hit Search.")
        return

    blocks = split_blocks(text)
    if not blocks:
        st.warning("No content found in that document.")
        return
    if len(blocks) > MAX_BLOCKS:
        st.warning(f"Document has {len(blocks)} blocks; using the first {MAX_BLOCKS}.")
        blocks = blocks[:MAX_BLOCKS]

    estimated_tokens = (len(text) + len(query)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Trim the document or switch provider."
        )
        return

    questions = build_questions(blocks, query.strip())

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"document_blocks": blocks}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    has_answer = answers.noul("has_answer")
    winner = answers.choice("best_block")
    probabilities = answers.probabilities("best_block")

    if has_answer < answer_threshold or winner == "none":
        st.warning(
            f"This document probably does **not** answer that question "
            f"(P = {has_answer:.2f}). Showing closest matches anyway."
        )
    else:
        st.success(f"Document answers this: P = {has_answer:.2f}")

    ranked = sorted(
        ((bid, p) for bid, p in probabilities.items() if bid != "none"),
        key=lambda kv: -kv[1],
    )[:top_n]

    for rank, (block_id, probability) in enumerate(ranked, start=1):
        block_text = blocks[int(block_id)]
        label = "Best match" if rank == 1 else f"#{rank}"
        with st.container(border=True):
            st.markdown(f"**{label}** · block `{block_id}` · P = {probability:.3f}")
            st.progress(min(probability, 1.0))
            st.write(block_text)

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Blocks scored", len(blocks))
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{answers.elapsed_s * 1000:.0f} ms")
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{answers.input_tokens:,} input tokens. Scoring {len(blocks)} blocks one-at-a-time "
        f"would have been {len(blocks)} requests."
    )


if __name__ == "__main__":
    main()
