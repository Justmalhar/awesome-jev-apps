"""Bookmark Organizer -- file years of bookmarks into a taxonomy you describe.

You write the folder tree in a textarea, in the shape you actually think in.
Every bookmark gets a top-level Choice and a sub-folder Choice in the same
request, and Python reconciles the two: when the sub-folder does not belong to
the chosen top-level folder, that disagreement is surfaced rather than hidden.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_bookmarks.json"
CHARS_PER_TOKEN = 4
UNFILED = "unfiled"
NO_SUB = "none"

DEFAULT_TAXONOMY = """Engineering
  Databases and storage
  Distributed systems
  Programming languages
  Security
  Tooling and editors
Craft
  Writing
  Design and typography
  Teaching and explanation
Career
  Interviewing and hiring
  Management
  Compensation and negotiation
Home
  Cooking
  Travel and places
  Things to buy
"""


def parse_taxonomy(text: str) -> dict[str, list[str]]:
    """Indented lines become sub-folders of the last unindented line."""
    tree: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw[:1].isspace():
            if current:
                tree[current].append(raw.strip())
        else:
            current = raw.strip()
            tree.setdefault(current, [])
    return tree


def load_bookmarks(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def split_batches(items: list[dict], context_tokens: int, questions_per_item: int = 4) -> list[list[int]]:
    """Years of bookmarks do not fit one window; the taxonomy rides in every request."""
    budget = max(1000, context_tokens // 2)
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, item in enumerate(items):
        cost = len(json.dumps(item)) // CHARS_PER_TOKEN + questions_per_item * 40
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        batches.append(current)
    return batches


def build_questions(indices: list[int], tree: dict[str, list[str]]) -> dict:
    """Two Choices per bookmark -- one per level -- plus two independent Nouls."""
    tops = {name: f"Bookmarks about: {name}." for name in tree}
    tops[UNFILED] = "None of the listed folders is a reasonable home for this."
    subs = {
        f"{top}/{sub}": f"{sub} (a sub-folder of {top})"
        for top, children in tree.items()
        for sub in children
    }
    subs[NO_SUB] = "No sub-folder in the whole tree fits; it belongs at the top level."

    questions: dict[str, dict] = {}
    for index in indices:
        bookmark = {
            "title": f"`bookmarks[{index}].title`",
            "url": f"`bookmarks[{index}].url`",
            "note": f"`bookmarks[{index}].note`",
        }
        questions[f"top_{index}"] = choice(
            {"task": "Which top-level folder does this bookmark belong in?", "bookmark": bookmark},
            tops,
        )
        questions[f"sub_{index}"] = choice(
            {"task": "Which sub-folder in the tree fits this bookmark best?", "bookmark": bookmark},
            subs,
        )
        questions[f"personal_{index}"] = noul(
            {"task": "Was this saved for personal life rather than for work?", "bookmark": bookmark},
            true="Household, hobby, travel, shopping, or anything unrelated to a job",
            false="Professional: work, craft, tools, or career",
        )
        questions[f"reference_{index}"] = noul(
            {"task": "Is this something to come back to repeatedly rather than read once?",
             "bookmark": bookmark},
            true="Documentation, a cheatsheet, a tool, or a page to look things up in",
            false="An article, story, or post read once and finished with",
        )
    return questions


def file_path(judgment: dict, tree: dict[str, list[str]]) -> tuple[str, bool]:
    """Reconcile the two levels. Returns (path, the_two_levels_disagreed).

    The sub-folder Choice ranges over the WHOLE tree, so it can pick a child of a
    different parent than the top-level Choice did. That disagreement is real
    information -- the bookmark straddles two folders -- so it is surfaced, not
    silently resolved in favour of one level.
    """
    top, sub = judgment["top"], judgment["sub"]
    if top == UNFILED:
        return "Unfiled", False
    if sub == NO_SUB:
        return top, False
    parent, _, child = sub.partition("/")
    if parent == top:
        return f"{top}/{child}", False
    return f"{top}/_needs a look", True


def needs_review(judgment: dict, *, confidence_gate: float) -> bool:
    """Low confidence on either level, or the two levels disagreeing."""
    return (
        judgment["top_confidence"] < confidence_gate
        or judgment["sub_confidence"] < confidence_gate
        or judgment["top"] == UNFILED
    )


def main() -> None:
    st.set_page_config(page_title="Bookmark Organizer", page_icon="🔖", layout="wide")
    st.title("🔖 Bookmark Organizer")
    st.caption("File years of bookmarks into the folder tree you describe, not one somebody else picked.")

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
        st.caption("Re-filing makes **no** API calls.")
        confidence_gate = st.slider("Confidence below which to review by hand", 0.0, 1.0, 0.5, 0.05)
        reference_gate = st.slider("P(reference) to tag as reference", 0.0, 1.0, 0.6, 0.05)
        personal_gate = st.slider("P(personal) to split work from life", 0.0, 1.0, 0.6, 0.05)
        split_personal = st.checkbox("Separate Work/ and Life/ at the root", value=False)
        bookmarks_path = st.text_input("Bookmarks JSON (optional)", value="")

    left, right = st.columns([2, 3])
    with left:
        taxonomy_text = st.text_area("Your folder tree (indent to nest)", value=DEFAULT_TAXONOMY, height=330)
    tree = parse_taxonomy(taxonomy_text)
    bookmarks = load_bookmarks(Path(bookmarks_path).expanduser() if bookmarks_path.strip() else None)
    with right:
        st.caption(
            f"{len(tree)} top-level folders, "
            f"{sum(len(children) for children in tree.values())} sub-folders, "
            f"{len(bookmarks)} bookmarks."
        )
        st.json({top: children for top, children in tree.items()}, expanded=False)

    if not tree or not bookmarks:
        st.warning("Need a folder tree and some bookmarks. The bundled sample is the default.")
        return

    batches = split_batches(bookmarks, provider.context_tokens)
    if st.button(f"File {len(bookmarks)} bookmarks ({len(batches)} request(s))", type="primary"):
        judgments: list[dict | None] = [None] * len(bookmarks)
        totals = {"questions": 0, "tokens": 0, "cost": 0.0, "elapsed": 0.0}
        try:
            with st.spinner("Filing..."):
                with JevClient(provider=provider) as client:
                    for indices in batches:
                        questions = build_questions(indices, tree)
                        answers = client.ask({"bookmarks": bookmarks, "folder_tree": tree}, questions)
                        for index in indices:
                            judgments[index] = {
                                "top": answers.choice(f"top_{index}"),
                                "top_confidence": answers.confidence(f"top_{index}"),
                                "sub": answers.choice(f"sub_{index}"),
                                "sub_confidence": answers.confidence(f"sub_{index}"),
                                "personal": answers.noul(f"personal_{index}"),
                                "reference": answers.noul(f"reference_{index}"),
                            }
                        totals["questions"] += len(questions)
                        totals["tokens"] += answers.input_tokens
                        totals["cost"] += answers.cost_usd
                        totals["elapsed"] += answers.elapsed_s
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["filed"] = {"bookmarks": bookmarks, "judgments": judgments, "tree": tree,
                                     "requests": len(batches), **totals}

    state = st.session_state.get("filed")
    if not state:
        st.info("Hit the button to file the pile.")
        return

    # --- policy only below this line ---
    folders: dict[str, list[tuple[dict, dict, bool]]] = {}
    review = 0
    for bookmark, judgment in zip(state["bookmarks"], state["judgments"]):
        path, conflict = file_path(judgment, state["tree"])
        if split_personal:
            root = "Life" if judgment["personal"] >= personal_gate else "Work"
            path = f"{root}/{path}"
        flagged = needs_review(judgment, confidence_gate=confidence_gate) or conflict
        review += flagged
        folders.setdefault(path, []).append((bookmark, judgment, flagged))

    columns = st.columns(4)
    columns[0].metric("Folders used", len(folders))
    columns[1].metric("Needs a human", review)
    columns[2].metric("Requests", state["requests"])
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    for path in sorted(folders):
        st.subheader(f"📁 {path}  ({len(folders[path])})")
        for bookmark, judgment, flagged in folders[path]:
            marker = "⚠️ " if flagged else ""
            tags = []
            if judgment["reference"] >= reference_gate:
                tags.append("📖 reference")
            if judgment["personal"] >= personal_gate:
                tags.append("🏠 personal")
            st.markdown(f"{marker}[{bookmark['title']}]({bookmark.get('url', '#')})")
            st.caption(
                f"{judgment['top']} (conf {judgment['top_confidence']:.2f}) · "
                f"{judgment['sub']} (conf {judgment['sub_confidence']:.2f})"
                + ("  ·  " + " · ".join(tags) if tags else "")
            )

    st.divider()
    st.caption(
        f"{len(state['bookmarks'])} bookmarks × 4 judgments = {state['questions']} questions in "
        f"{state['requests']} request(s) · {state['elapsed']:.1f}s · "
        f"{state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Every threshold above re-files the pile for $0.00; changing the tree does not."
    )


if __name__ == "__main__":
    main()
