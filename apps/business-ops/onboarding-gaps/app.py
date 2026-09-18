"""Onboarding Gaps -- which steps a new hire's docs never actually explain.

Every team believes its onboarding docs are fine, because the people who wrote
them already know the answers. The gaps are invisible from the inside: the step
that is documented as "request access in the usual way", the runbook that
assumes you already have the VPN profile, the deploy guide that starts at step
three.

This is a cross product: every onboarding step against every document. One Noul
per pair asking whether THAT document explains THAT step well enough to act on,
and a second asking whether it merely mentions it. Coverage per step is then a
max over documents -- computed in Python, because a max over N numbers is
arithmetic and the model is never asked to do arithmetic.

That shape matters: asking "is this step documented anywhere?" over the whole
corpus is indirection over a large state, which is a documented jev-1.13 failure
mode. Asking one document at a time is not.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_STEPS = HERE / "sample_steps.json"
SAMPLE_DOCS = HERE / "sample_docs.json"

CHARS_PER_TOKEN = 4
OVERHEAD_TOKENS = 2000


def build_questions(steps: list[dict], docs: list[dict], pairs: list[tuple[int, int]]) -> dict:
    """Two Nouls per (step, document) pair: actionable, and merely mentioned.

    They are separate because they are separate properties. A document that
    mentions a step without explaining it is the single most common kind of gap,
    and collapsing the two loses exactly that case.
    """
    questions: dict[str, dict] = {}
    for step_index, doc_index in pairs:
        reference = {
            "onboarding_step": f"`steps[{step_index}].task`",
            "document_title": f"`docs[{doc_index}].title`",
            "document_body": f"`docs[{doc_index}].body`",
        }
        questions[f"act_{step_index}_{doc_index}"] = noul(
            {
                "task": "Does this document explain the onboarding step well enough that a new joiner could complete it without asking anyone?",
                **reference,
            },
            true="The document gives the concrete commands, links, names, or steps needed to do it",
            false="The document leaves out something the new joiner would have to ask a colleague for",
        )
        questions[f"men_{step_index}_{doc_index}"] = noul(
            {"task": "Does this document refer to the onboarding step at all?", **reference},
            true="The step is named or clearly referred to somewhere in the document",
            false="The document does not refer to this step",
        )
    return questions


def all_pairs(steps: list[dict], docs: list[dict]) -> list[tuple[int, int]]:
    return [(s, d) for s in range(len(steps)) for d in range(len(docs))]


def batch_pairs(
    pairs: list[tuple[int, int]], docs: list[dict], context_tokens: int
) -> list[list[tuple[int, int]]]:
    """Chunk the cross product to fit the provider's context window.

    Never hardcode a window -- `provider.context_tokens` differs by provider.
    """
    budget = max(context_tokens - OVERHEAD_TOKENS, 2000)
    chunks: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    used = 0
    for pair in pairs:
        cost = len(docs[pair[1]].get("body", "")) // CHARS_PER_TOKEN + 120
        if current and used + cost > budget:
            chunks.append(current)
            current, used = [], 0
        current.append(pair)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def coverage(
    steps: list[dict], docs: list[dict], judgments: dict[tuple[int, int], dict]
) -> pd.DataFrame:
    """Best document per step. The max is arithmetic, so it happens here."""
    rows = []
    for step_index, step in enumerate(steps):
        scores = [
            (judgments[(step_index, doc_index)]["actionable"], doc_index)
            for doc_index in range(len(docs))
            if (step_index, doc_index) in judgments
        ]
        mentions = [
            judgments[(step_index, doc_index)]["mentioned"]
            for doc_index in range(len(docs))
            if (step_index, doc_index) in judgments
        ]
        best, best_doc = max(scores) if scores else (0.0, None)
        rows.append(
            {
                "step": step["task"],
                "owner": step.get("owner", ""),
                "day": step.get("day", ""),
                "best explanation": round(best, 3),
                "best mention": round(max(mentions), 3) if mentions else 0.0,
                "best doc": docs[best_doc]["title"] if best_doc is not None else "—",
            }
        )
    return pd.DataFrame(rows)


def classify(explained: float, mentioned: float, covered_at: float, absent_below: float) -> str:
    """Three states, and the middle one is the interesting one."""
    if explained >= covered_at:
        return "covered"
    if mentioned >= covered_at:
        return "mentioned but not explained"
    if mentioned <= absent_below:
        return "missing entirely"
    return "thin"


def main() -> None:
    st.set_page_config(page_title="Onboarding Gaps", page_icon="🧭", layout="wide")
    st.title("🧭 Onboarding Gaps")
    st.caption("Which onboarding steps your documentation never actually explains.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.caption(f"context window: {provider.context_tokens:,} tokens")
        st.divider()
        st.subheader("Coverage thresholds")
        covered_at = st.slider("Covered at P(yes) ≥", 0.5, 0.99, 0.70, 0.01)
        absent_below = st.slider("Absent at P(mentioned) ≤", 0.01, 0.5, 0.20, 0.01)

    left, right = st.columns(2)
    steps_upload = left.file_uploader("Onboarding steps (JSON)", type=["json"])
    docs_upload = right.file_uploader("Documents (JSON)", type=["json"])
    steps = json.loads(steps_upload.read().decode("utf-8")) if steps_upload else json.loads(
        SAMPLE_STEPS.read_text(encoding="utf-8")
    )
    docs = json.loads(docs_upload.read().decode("utf-8")) if docs_upload else json.loads(
        SAMPLE_DOCS.read_text(encoding="utf-8")
    )

    pairs = all_pairs(steps, docs)
    chunks = batch_pairs(pairs, docs, provider.context_tokens)
    st.caption(
        f"{len(steps)} steps × {len(docs)} documents × 2 judgments = "
        f"{len(pairs) * 2} questions in {len(chunks)} request(s)."
    )

    if st.button("Find the gaps", type="primary"):
        judgments: dict[tuple[int, int], dict] = {}
        tokens = cost = elapsed = 0.0
        asked = 0
        try:
            with JevClient(provider=provider) as client:
                progress = st.progress(0.0)
                for position, chunk in enumerate(chunks, start=1):
                    questions = build_questions(steps, docs, chunk)
                    answers = client.ask({"steps": steps, "docs": docs}, questions)
                    asked += len(questions)
                    tokens += answers.input_tokens
                    cost += answers.cost_usd
                    elapsed += answers.elapsed_s
                    for step_index, doc_index in chunk:
                        judgments[(step_index, doc_index)] = {
                            "actionable": answers.noul(f"act_{step_index}_{doc_index}"),
                            "mentioned": answers.noul(f"men_{step_index}_{doc_index}"),
                        }
                    progress.progress(position / len(chunks))
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["gaps"] = {
            "steps": steps,
            "docs": docs,
            "judgments": judgments,
            "questions": asked,
            "requests": len(chunks),
            "tokens": int(tokens),
            "cost": cost,
            "elapsed": elapsed,
        }

    found = st.session_state.get("gaps")
    if not found:
        st.info(
            f"Bundled sample: {len(steps)} onboarding steps against {len(docs)} internal docs."
        )
        return

    frame = coverage(found["steps"], found["docs"], found["judgments"])
    frame["state"] = [
        classify(row["best explanation"], row["best mention"], covered_at, absent_below)
        for _, row in frame.iterrows()
    ]

    order = ["missing entirely", "mentioned but not explained", "thin", "covered"]
    columns = st.columns(4)
    for column, state in zip(columns, order):
        column.metric(state.title(), int((frame["state"] == state).sum()))

    for state in order:
        subset = frame[frame["state"] == state]
        if subset.empty:
            continue
        st.subheader(f"{state.title()} ({len(subset)})")
        if state == "mentioned but not explained":
            st.caption(
                "The worst kind of gap: the doc looks complete because the step appears in it."
            )
        st.dataframe(
            subset.drop(columns=["state"]).sort_values("best explanation"),
            width="stretch",
            hide_index=True,
        )

    st.subheader("Which documents are pulling their weight")
    carried = frame.groupby("best doc").size().rename("steps best covered")
    st.dataframe(carried.sort_values(ascending=False), width="stretch")

    st.download_button(
        "Download onboarding_gaps.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="onboarding_gaps.csv",
        mime="text/csv",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", found["questions"])
    metrics[1].metric("Requests", found["requests"])
    metrics[2].metric("Latency", f"{found['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${found['cost']:.6f}")
    st.caption(
        f"{found['tokens']:,} input tokens across {len(found['steps'])} × {len(found['docs'])} "
        "step/document pairs. Re-classifying at a different threshold costs $0.00."
    )


if __name__ == "__main__":
    main()
