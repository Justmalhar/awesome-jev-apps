"""Note Linker -- given a new note, which existing notes should it link to?

Pairwise relevance: the new note is judged against every note already in the
vault, in one request. The link block it produces is assembled by Python from
your own note titles -- Jev selects, it never writes.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE_VAULT = HERE / "sample_vault.json"
SAMPLE_NOTE = HERE / "sample_note.md"
CHARS_PER_TOKEN = 4
EXCERPT_CHARS = 900

RELATION_LEVELS = [
    "The two notes are about unrelated subjects and share no useful thread",
    "Loosely connected: same field or vocabulary, but no shared idea",
    "Genuinely related: they discuss the same system, problem, or argument",
    "Tightly coupled: one is about the specific thing the other is about",
]

# Link kinds, in the order Python applies them. Policy, not prompt.
LINK_LABELS = {
    "merge": "🔀 Probably the same note",
    "contradicts": "⚡ Contradicts this",
    "extends": "➕ Extends this",
    "background": "📖 Assumes this",
    "see_also": "🔗 See also",
}


def load_vault(path: Path | None) -> list[dict]:
    """A vault index (title/path/tags/excerpt per note), or the bundled sample."""
    source = path if path and path.is_file() else SAMPLE_VAULT
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def load_new_note(path: Path | None) -> str:
    source = path if path and path.is_file() else SAMPLE_NOTE
    return source.read_text(encoding="utf-8") if source.is_file() else ""


def split_batches(notes: list[dict], context_tokens: int, questions_per_note: int = 5) -> list[list[int]]:
    """A vault outgrows any context window; the new note rides along in each request."""
    budget = max(1000, context_tokens // 2)
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, note in enumerate(notes):
        cost = len(json.dumps(note)) // CHARS_PER_TOKEN + questions_per_note * 40
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        batches.append(current)
    return batches


def build_questions(indices: list[int]) -> dict:
    """Five judgments per candidate pair. The new note is in state, not repeated."""
    questions: dict[str, dict] = {}
    for index in indices:
        pair = {
            "new_note": "`new_note`",
            "existing_note_title": f"`vault[{index}].title`",
            "existing_note": f"`vault[{index}].excerpt`",
        }
        questions[f"relation_{index}"] = score(
            {"task": "How closely related are these two notes?", "pair": pair}, RELATION_LEVELS
        )
        questions[f"extends_{index}"] = noul(
            {"task": "Does the new note add detail, evidence, or a development to the existing note's subject?",
             "pair": pair},
            true="The new note carries the existing note's idea further or supplies its missing detail",
            false="It stands on its own, or merely mentions the same words",
        )
        questions[f"background_{index}"] = noul(
            {"task": "Does the existing note explain background that the new note takes for granted?",
             "pair": pair},
            true="The new note assumes a concept or definition the existing note sets out",
            false="The new note is self-contained with respect to this note",
        )
        questions[f"contradicts_{index}"] = noul(
            {"task": "Do these two notes make claims that cannot both be right?",
             "pair": pair},
            true="They state conflicting facts, measurements, or conclusions about the same thing",
            false="They agree, or they simply discuss different things",
        )
        questions[f"duplicate_{index}"] = noul(
            {"task": "Do these two notes cover the same material closely enough that one should be merged into the other?",
             "pair": pair},
            true="Substantially the same content, written twice",
            false="Related but each holds something the other does not",
        )
    return questions


def link_type(judgment: dict, *, relate_gate: float, noul_gate: float) -> str | None:
    """Pure policy. First match wins; the order encodes what a reader needs to see."""
    if judgment["duplicate"] >= noul_gate:
        return "merge"
    if judgment["contradicts"] >= noul_gate:
        return "contradicts"
    if judgment["relation"] < relate_gate:
        return None
    if judgment["extends"] >= noul_gate:
        return "extends"
    if judgment["background"] >= noul_gate:
        return "background"
    return "see_also"


def render_links(links: list[tuple[str, dict, dict]]) -> str:
    """Assemble the markdown block in Python from the vault's own titles."""
    if not links:
        return "## Links\n\n_No note in this vault is close enough to link._\n"
    lines = ["## Links", ""]
    for kind in LINK_LABELS:
        group = [(note, judgment) for k, note, judgment in links if k == kind]
        if not group:
            continue
        lines.append(f"**{LINK_LABELS[kind]}**")
        for note, judgment in sorted(group, key=lambda pair: -pair[1]["relation"]):
            lines.append(f"- [[{note['title']}]] — `{note.get('path', '')}`")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    st.set_page_config(page_title="Note Linker", page_icon="🔗", layout="wide")
    st.title("🔗 Note Linker")
    st.caption("Where does this new note belong in your vault? Pairwise, in one request.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\ncontext {provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Thresholds")
        st.caption("Re-linking makes **no** API calls.")
        relate_gate = st.slider("Relation needed to link at all", 0.0, 3.0, 1.8, 0.1)
        noul_gate = st.slider("P(yes) for a typed link", 0.0, 1.0, 0.6, 0.05)
        vault_path = st.text_input("Vault index JSON (optional)", value="")
        note_path = st.text_input("New note file (optional)", value="")

    vault = load_vault(Path(vault_path).expanduser() if vault_path.strip() else None)
    new_note = st.text_area(
        "The new note",
        value=load_new_note(Path(note_path).expanduser() if note_path.strip() else None),
        height=220,
    )
    if not vault or not new_note.strip():
        st.warning("Need a vault index and a new note. The bundled samples are the defaults.")
        return
    st.caption(f"Vault: {len(vault)} notes.")

    batches = split_batches(vault, provider.context_tokens)
    if st.button(f"Find links across {len(vault)} notes", type="primary"):
        judgments: list[dict | None] = [None] * len(vault)
        totals = {"questions": 0, "tokens": 0, "cost": 0.0, "elapsed": 0.0}
        try:
            with st.spinner("Comparing against the vault..."):
                with JevClient(provider=provider) as client:
                    for indices in batches:
                        questions = build_questions(indices)
                        answers = client.ask(
                            {"new_note": new_note[:4000], "vault": vault}, questions
                        )
                        for index in indices:
                            judgments[index] = {
                                "relation": answers.score(f"relation_{index}"),
                                "extends": answers.noul(f"extends_{index}"),
                                "background": answers.noul(f"background_{index}"),
                                "contradicts": answers.noul(f"contradicts_{index}"),
                                "duplicate": answers.noul(f"duplicate_{index}"),
                            }
                        totals["questions"] += len(questions)
                        totals["tokens"] += answers.input_tokens
                        totals["cost"] += answers.cost_usd
                        totals["elapsed"] += answers.elapsed_s
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["links"] = {"vault": vault, "judgments": judgments,
                                     "requests": len(batches), **totals}

    state = st.session_state.get("links")
    if not state:
        st.info("Hit the button to compare this note against the vault.")
        return

    # --- policy only below this line ---
    links = []
    for note, judgment in zip(state["vault"], state["judgments"]):
        kind = link_type(judgment, relate_gate=relate_gate, noul_gate=noul_gate)
        if kind:
            links.append((kind, note, judgment))

    columns = st.columns(4)
    columns[0].metric("Links proposed", len(links))
    columns[1].metric("Merge candidates", sum(1 for k, _, _ in links if k == "merge"))
    columns[2].metric("Requests", state["requests"])
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Proposed links")
        for kind, note, judgment in sorted(links, key=lambda row: -row[2]["relation"]):
            with st.container(border=True):
                st.markdown(f"**{LINK_LABELS[kind]}** · [[{note['title']}]]")
                st.caption(
                    f"`{note.get('path', '')}` · {' '.join(note.get('tags', []))} · "
                    f"relation {judgment['relation']:.2f}/3 · extends P={judgment['extends']:.2f} · "
                    f"background P={judgment['background']:.2f} · "
                    f"conflict P={judgment['contradicts']:.2f} · dup P={judgment['duplicate']:.2f}"
                )
                with st.expander("Existing note"):
                    st.text(note.get("excerpt", "")[:EXCERPT_CHARS])
    with right:
        st.subheader("Paste into the note")
        st.code(render_links(links), language="markdown")

    st.divider()
    st.caption(
        f"{len(state['vault'])} notes × 5 judgments = {state['questions']} questions in "
        f"{state['requests']} request(s) · {state['elapsed']:.1f}s · "
        f"{state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Both thresholds re-type every link for $0.00."
    )


if __name__ == "__main__":
    main()
