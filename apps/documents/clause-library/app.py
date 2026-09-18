"""Clause Library -- rerank a counterparty's clauses against your approved set.

Semantic Ctrl-F runs one query against many blocks. This runs many clauses
against one known-good library: every incoming clause becomes its own Choice
whose options are the library clause ids, with the approved text as each
option's description.

The `none` option is the product. A Choice must return something, so without it
a never-before-seen clause -- the exact thing you need legal to look at -- gets
quietly filed under whichever approved clause it least resembles.

Match is a rerank, not a generation: the "matched" text is copied out of your
own library, so the app cannot invent an approved position you never approved.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_LIBRARY = HERE / "sample_library.json"
SAMPLE_INCOMING = HERE / "sample_incoming.txt"

# Rough chars-per-token, only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4
# Below this a paragraph is a heading or a preamble, not a contract clause.
MIN_CLAUSE_CHARS = 120

ROUTE_APPROVE = "auto-approve"
ROUTE_REDLINE = "redline queue"
ROUTE_LEGAL = "legal review"


def split_clauses(text: str) -> list[str]:
    """One clause per paragraph, dropping title blocks and stray headings.

    ponytail: a length floor, not a clause-numbering parser. Contract paper is
    not consistently numbered across firms; a parser would be wrong differently
    on every document. Raise MIN_CLAUSE_CHARS if your paper has short clauses.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [p for p in paragraphs if len(p) >= MIN_CLAUSE_CHARS]


def load_library(raw: str) -> list[dict]:
    """Parse and validate the approved clause library."""
    library = json.loads(raw)
    if not isinstance(library, list) or not library:
        raise ValueError("library must be a non-empty JSON list")
    for entry in library:
        missing = [k for k in ("id", "title", "text") if not entry.get(k)]
        if missing:
            raise ValueError(f"library entry missing {missing}: {entry}")
    if "none" in {entry["id"] for entry in library}:
        raise ValueError("'none' is reserved for the no-match option")
    return library


def build_questions(clauses: list[str], library: list[dict]) -> dict:
    """Per incoming clause: one Choice over the library + three independent Nouls.

    The three Nouls are separate because they are separate facts. A clause can
    match the library subject and still be on worse terms; a clause can be novel
    without being adverse. Collapsing them into one rubric would make each of
    them unthresholdable on its own.
    """
    options: dict[str, str] = {
        entry["id"]: f"{entry['title']} -- {entry['text']}" for entry in library
    }
    options["none"] = (
        "No approved clause in the library covers this subject matter. "
        "This is a non-standard clause with no counterpart on our paper."
    )

    questions: dict[str, dict] = {}
    for index in range(len(clauses)):
        clause_ref = f"`incoming_clauses[{index}].text`"
        questions[f"match_{index}"] = choice(
            {
                "task": (
                    "Select the approved library clause that governs the same subject "
                    "matter as the incoming clause below. Match on subject, not on "
                    "whether the terms are favourable."
                ),
                "incoming_clause": clause_ref,
                "note": (
                    "Choose 'none' when no library clause governs this subject, even "
                    "if one is loosely adjacent to it."
                ),
            },
            options,
        )
        questions[f"deviates_{index}"] = noul(
            {
                "task": (
                    "Does this incoming clause set out different substantive terms "
                    "from our approved library position on the same subject?"
                ),
                "incoming_clause": clause_ref,
                "approved_library": "`approved_library`",
            },
            true=(
                "The clause covers a subject in the library but changes an operative "
                "term: a cap, a notice period, a carve-out, a remedy, a forum, or who "
                "bears an obligation"
            ),
            false=(
                "The clause is our approved position, or differs only in wording, "
                "ordering, or formatting without changing an operative term"
            ),
        )
        questions[f"worse_{index}"] = noul(
            {
                "task": (
                    "Does this incoming clause leave Supplier -- our side -- in a worse "
                    "position than a balanced, mutual version of the same clause?"
                ),
                "incoming_clause": clause_ref,
                "our_side": "Supplier",
            },
            true=(
                "The clause imposes an obligation, risk, cap, or restriction on Supplier "
                "that it does not impose reciprocally on Customer, or it strips Supplier "
                "of a protection Customer keeps"
            ),
            false=(
                "The clause is mutual, is neutral between the parties, or is one-sided "
                "in Supplier's favour"
            ),
        )
        questions[f"novel_{index}"] = noul(
            {
                "task": (
                    "Is the subject matter of this incoming clause absent from our "
                    "approved library entirely?"
                ),
                "incoming_clause": clause_ref,
                "approved_library": "`approved_library`",
            },
            true=(
                "No library clause addresses this subject; it is a new obligation type "
                "such as non-solicitation, pricing parity, exclusivity, or audit rights"
            ),
            false=(
                "Some library clause addresses this same subject, even if it does so on "
                "different terms"
            ),
        )
    return questions


def route(
    match_id: str,
    match_probability: float,
    deviates: float,
    worse: float,
    novel: float,
    match_threshold: float,
    deviation_threshold: float,
    novel_threshold: float,
) -> tuple[str, str]:
    """Jev supplied the evidence; this is the policy, and it lives in Python.

    Ordered by escalation: a clause with no approved counterpart goes to a human
    before anything else is considered, because a redline against the wrong
    baseline is worse than no redline.
    """
    if novel >= novel_threshold:
        return ROUTE_LEGAL, f"no library counterpart (novel P={novel:.2f})"
    if match_id == "none":
        return ROUTE_LEGAL, "matched the no-match option"
    if match_probability < match_threshold:
        return ROUTE_LEGAL, f"match not confident (P={match_probability:.2f})"
    if deviates >= deviation_threshold and worse >= deviation_threshold:
        return ROUTE_REDLINE, f"deviates against us (worse P={worse:.2f})"
    if deviates >= deviation_threshold:
        return ROUTE_REDLINE, f"deviates from approved text (P={deviates:.2f})"
    return ROUTE_APPROVE, f"matches approved text (P={match_probability:.2f})"


def main() -> None:
    st.set_page_config(page_title="Clause Library", page_icon="📚", layout="wide")
    st.title("📚 Clause Library")
    st.caption(
        "Rerank a counterparty's clauses against your approved library. "
        "The clauses that match **nothing** are the ones worth your afternoon."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(
            f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok",
            language=None,
        )
        st.caption("Change `provider` in providers.toml, or set `JEV_PROVIDER`.")
        st.divider()
        st.subheader("Routing thresholds")
        st.caption("Tune on your own past redlines. These are not universal.")
        match_threshold = st.slider("Minimum match probability to trust a match", 0.0, 1.0, 0.45, 0.01)
        deviation_threshold = st.slider("P(deviates) at or above which we redline", 0.0, 1.0, 0.60, 0.01)
        novel_threshold = st.slider("P(novel subject) at or above which we escalate", 0.0, 1.0, 0.60, 0.01)

    library_text = st.text_area(
        "Approved clause library (JSON: id, title, text)",
        value=SAMPLE_LIBRARY.read_text(encoding="utf-8") if SAMPLE_LIBRARY.is_file() else "[]",
        height=180,
    )
    incoming_text = st.text_area(
        "Incoming paper (one clause per paragraph)",
        value=SAMPLE_INCOMING.read_text(encoding="utf-8") if SAMPLE_INCOMING.is_file() else "",
        height=260,
    )

    if not st.button("Match against library", type="primary"):
        st.info("The bundled sample is loaded. Hit the button to rerank it.")
        return

    try:
        library = load_library(library_text)
    except (ValueError, json.JSONDecodeError) as exc:
        st.error(f"Library is not usable: {exc}")
        return

    clauses = split_clauses(incoming_text)
    if not clauses:
        st.warning(
            f"No paragraph reached {MIN_CLAUSE_CHARS} characters, so nothing looked "
            "like a clause. Paste clauses separated by blank lines."
        )
        return

    library_chars = sum(len(entry["text"]) for entry in library)
    # Every Choice repeats the whole library in its criteria, so cost grows with
    # clauses x library, not with either alone.
    estimated_tokens = (len(incoming_text) + library_chars * len(clauses)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Send fewer clauses per run, or trim the library."
        )
        return

    questions = build_questions(clauses, library)
    state = {
        "incoming_clauses": [{"text": clause} for clause in clauses],
        "approved_library": library,
    }

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    titles = {entry["id"]: entry["title"] for entry in library}
    rows = []
    for index, clause in enumerate(clauses):
        match_id = answers.choice(f"match_{index}")
        probabilities = answers.probabilities(f"match_{index}")
        match_probability = probabilities.get(match_id, 0.0)
        deviates = answers.noul(f"deviates_{index}")
        worse = answers.noul(f"worse_{index}")
        novel = answers.noul(f"novel_{index}")
        decision, reason = route(
            match_id,
            match_probability,
            deviates,
            worse,
            novel,
            match_threshold,
            deviation_threshold,
            novel_threshold,
        )
        rows.append(
            {
                "Incoming clause": clause[:90] + ("…" if len(clause) > 90 else ""),
                "Matched library entry": titles.get(match_id, "— none —"),
                "Match P": round(match_probability, 3),
                "Deviates P": round(deviates, 3),
                "Worse for us P": round(worse, 3),
                "Novel P": round(novel, 3),
                "Route": decision,
                "Why": reason,
            }
        )

    table = pd.DataFrame(rows)
    for decision in (ROUTE_LEGAL, ROUTE_REDLINE, ROUTE_APPROVE):
        subset = table[table["Route"] == decision]
        if subset.empty:
            continue
        st.subheader(f"{decision} ({len(subset)})")
        st.dataframe(subset.drop(columns=["Route"]), width="stretch", hide_index=True)

    with st.expander("Full clause text, in routed order"):
        order = {ROUTE_LEGAL: 0, ROUTE_REDLINE: 1, ROUTE_APPROVE: 2}
        for index in sorted(range(len(clauses)), key=lambda i: order[rows[i]["Route"]]):
            with st.container(border=True):
                st.markdown(
                    f"**{rows[index]['Route']}** · {rows[index]['Matched library entry']} "
                    f"· {rows[index]['Why']}"
                )
                st.write(clauses[index])

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Questions", len(questions))
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{answers.elapsed_s:.1f} s")
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{len(clauses)} clauses × {len(library)} library entries → "
        f"{len(questions)} judgments in 1 request, "
        f"{answers.input_tokens:,} input tokens."
    )


if __name__ == "__main__":
    main()
