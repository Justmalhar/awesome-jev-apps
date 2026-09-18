"""Deep Classifier -- file items into a deep taxonomy with BEAM SEARCH.

The problem with deep taxonomies is not the leaves, it is the root. Ask a
classifier to pick one of 36 leaves directly and it drowns. Walk the tree
greedily instead -- pick the best branch at each level -- and you get a new
problem: a wrong turn at level 1 is UNRECOVERABLE. "Cycling jersey" goes to
`apparel`, and every leaf under `sports_and_outdoors > cycling > cycle_clothing`
becomes unreachable no matter how obviously right it was.

This app keeps k branches alive instead of one.

    level 1:  one Choice per item          -> keep the top k branches by P
    level 2:  one Choice per SURVIVING beam -> keep the top k paths by cumulative P
    level 3:  same again                    -> the winner is the best full path

Cumulative probability is a PRODUCT over levels, computed in Python from the
distributions Jev returns. A path that was second at level 1 with P=0.35 and
then overwhelming at level 2 with P=0.95 beats a path that led at level 1 with
P=0.55 and then fell apart at 0.30. Greedy cannot express that. Beam search is
just the arithmetic that lets the second level correct the first.

Two things make it affordable:

1. EVERY BEAM AT A LEVEL GOES IN ONE REQUEST. Not one request per item, and not
   one per beam -- all of them, against a state ingested once. A 3-level
   taxonomy over any number of items is THREE round trips.

2. A CHOICE RETURNS THE WHOLE DISTRIBUTION, not just the winner. Beam search
   needs P(branch) for every branch, which is the primitive's actual output.
   Getting the same thing out of a text model means asking for logprobs and
   hoping the tokenizer cooperates.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice

HERE = Path(__file__).resolve().parent
SAMPLE_TAXONOMY = HERE / "sample_taxonomy.json"
SAMPLE_ITEMS = HERE / "sample_products.json"

# The escape hatch. Not decoration: it is how a beam TERMINATES EARLY, which is
# what stops a genuinely uncategorisable item being shoved down to a leaf.
NO_MATCH = "none_of_these"
NO_MATCH_TEXT = (
    "None of the branches above is where this item belongs. Choose this when the item "
    "is real but nothing listed covers it, or when the item does not fit the parent "
    "category at all."
)

QUESTION_OVERHEAD_TOKENS = 60


@dataclass(frozen=True)
class Beam:
    """One surviving path through the taxonomy for one item."""

    item: int
    path: tuple[str, ...]
    probability: float  # cumulative: the product of each level's P(branch)
    open: bool = True   # False once the path terminated on NO_MATCH


def node_at(taxonomy: dict, path: tuple[str, ...]) -> dict | None:
    """Walk a path of child names from the root. None if the path is not in the tree."""
    node = taxonomy
    for name in path:
        children = {child["name"]: child for child in node.get("children", [])}
        if name not in children:
            return None
        node = children[name]
    return node


def branch_options(node: dict) -> dict[str, str]:
    """This node's children as Choice options, always with an escape hatch."""
    options = {
        child["name"]: child.get("description", child["name"])
        for child in node.get("children", [])
    }
    options[NO_MATCH] = NO_MATCH_TEXT
    return options


def taxonomy_depth(node: dict) -> int:
    children = node.get("children", [])
    return 0 if not children else 1 + max(taxonomy_depth(child) for child in children)


def leaf_paths(node: dict, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    children = node.get("children", [])
    if not children:
        return [prefix] if prefix else []
    return [p for child in children for p in leaf_paths(child, prefix + (child["name"],))]


def build_level_questions(beams: list[Beam], taxonomy: dict) -> dict:
    """One Choice per OPEN beam. Every question at this level is independent of
    every other, so they all belong in a single ask()."""
    questions: dict[str, dict] = {}
    for index, beam in enumerate(beams):
        if not beam.open:
            continue
        node = node_at(taxonomy, beam.path)
        if node is None or not node.get("children"):
            continue
        parent = " > ".join(beam.path) if beam.path else "the top of the taxonomy"
        questions[f"beam_{index}"] = choice(
            {
                "task": "Which of these branches does this item belong under?",
                "item": f"`items[{beam.item}]`",
                "parent_category": parent,
                "note": "Judge only against the branches listed. The item has already been placed under the parent category named above.",
            },
            branch_options(node),
        )
    return questions


def expand(beams: list[Beam], distributions: dict[str, dict[str, float]]) -> list[Beam]:
    """Turn each open beam into one child beam per branch, carrying the product
    of probabilities. Pure: hand it a dict and it needs no network."""
    expanded: list[Beam] = []
    for index, beam in enumerate(beams):
        distribution = distributions.get(f"beam_{index}")
        if not beam.open or not distribution:
            expanded.append(beam)  # already terminated, or a leaf with nowhere to go
            continue
        for name, probability in distribution.items():
            if name == NO_MATCH:
                # The path stops here. It keeps its accumulated probability so it
                # can still win against three weak levels of forced descent.
                expanded.append(Beam(beam.item, beam.path, beam.probability * probability, open=False))
            else:
                expanded.append(
                    Beam(beam.item, beam.path + (name,), beam.probability * probability, open=True)
                )
    return expanded


def prune(beams: list[Beam], width: int, floor: float) -> list[Beam]:
    """Keep the top `width` beams PER ITEM, and drop anything below `floor`.

    The floor is the cost control: a branch at cumulative P=0.001 will not win,
    and every beam kept is a question paid for at the next level.
    """
    by_item: dict[int, list[Beam]] = {}
    for beam in beams:
        by_item.setdefault(beam.item, []).append(beam)

    kept: list[Beam] = []
    for item in sorted(by_item):
        ranked = sorted(by_item[item], key=lambda b: -b.probability)
        survivors = [beam for beam in ranked[:width] if beam.probability >= floor]
        # Never let an item fall out of the search entirely.
        kept.extend(survivors or ranked[:1])
    return kept


def greedy_roots(beams: list[Beam], distributions: dict[str, dict[str, float]]) -> dict[int, str]:
    """What a k=1 greedy descent would have committed to at this level.

    Kept only so the app can report how often beam search DISAGREED -- which is
    the entire value proposition, and is otherwise invisible.
    """
    roots: dict[int, str] = {}
    for index, beam in enumerate(beams):
        distribution = distributions.get(f"beam_{index}")
        if not distribution:
            continue
        branches = {name: p for name, p in distribution.items() if name != NO_MATCH}
        if branches:
            roots[beam.item] = max(branches, key=branches.get)
    return roots


def best_per_item(beams: list[Beam]) -> dict[int, Beam]:
    best: dict[int, Beam] = {}
    for beam in beams:
        if beam.item not in best or beam.probability > best[beam.item].probability:
            best[beam.item] = beam
    return best


def decide(beam: Beam, required_depth: int, min_probability: float) -> str:
    """Pure policy. Depth and cumulative probability are both in Python, so both
    sliders re-derive the whole result with no new inference."""
    if len(beam.path) < required_depth:
        return "partial"
    if beam.probability >= min_probability:
        return "filed"
    return "review"


def split_questions(questions: dict, items: list[dict], context_tokens: int,
                    headroom: float = 0.5) -> list[list[str]]:
    """Group question ids into requests that fit the PROVIDER's context.

    The state (every item) is sent with each request, so it is charged once per
    request -- which is the real argument for keeping requests few and fat.
    """
    state_tokens = len(str(items)) // 4
    budget = max(int(context_tokens * headroom) - state_tokens, QUESTION_OVERHEAD_TOKENS)
    per_request = max(budget // QUESTION_OVERHEAD_TOKENS, 1)
    ids = list(questions)
    return [ids[i : i + per_request] for i in range(0, len(ids), per_request)] or []


def main() -> None:
    st.set_page_config(page_title="Deep Classifier", page_icon="🌳", layout="wide")
    st.title("🌳 Deep Classifier")
    st.caption("Beam search down a 3-level taxonomy. One request per level, however many items.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\ncontext {provider.context_tokens:,} tokens", language=None)
        st.divider()
        st.subheader("Beam search")
        beam_width = st.slider("Beam width k", 1, 6, 3, 1,
                               help="k=1 is greedy descent, and cannot recover from a wrong turn at level 1.")
        floor = st.select_slider("Prune beams below cumulative P", [0.0, 0.001, 0.01, 0.03, 0.05, 0.1], value=0.01)
        st.caption("Width costs questions at the NEXT level. The floor is what stops that growing.")
        st.divider()
        st.subheader("Policy")
        min_probability = st.slider("File automatically above cumulative P", 0.0, 1.0, 0.35, 0.05)
        st.caption("Cumulative P is a product over 3 levels, so it is small by construction. 0.35 is already confident.")

    taxonomy = json.loads(SAMPLE_TAXONOMY.read_text(encoding="utf-8"))
    uploaded = st.file_uploader("Items JSON (a list of objects)", type=["json"])
    if uploaded is not None:
        items = json.loads(uploaded.read().decode("utf-8"))
    else:
        items = json.loads(SAMPLE_ITEMS.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(items)} products.")

    depth = taxonomy_depth(taxonomy)
    leaves = leaf_paths(taxonomy)
    info = st.columns(4)
    info[0].metric("Items", len(items))
    info[1].metric("Taxonomy depth", depth)
    info[2].metric("Leaf categories", len(leaves))
    info[3].metric("Flat-choice options", f"{len(leaves) + 1}",
                   help="What a single flat Choice would have to discriminate between.")

    st.dataframe(pd.DataFrame(items).head(8), width="stretch")

    if not st.button(f"Classify {len(items)} items", type="primary"):
        return

    beams = [Beam(item=index, path=(), probability=1.0) for index in range(len(items))]
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0
    requests = 0
    questions_asked = 0
    trace: list[dict] = []
    greedy_first_branch: dict[int, str] = {}

    try:
        with JevClient(provider=provider) as client:
            for level in range(1, depth + 1):
                questions = build_level_questions(beams, taxonomy)
                if not questions:
                    break

                distributions: dict[str, dict[str, float]] = {}
                for group in split_questions(questions, items, provider.context_tokens):
                    answers = client.ask(
                        {"items": items}, {qid: questions[qid] for qid in group}
                    )
                    requests += 1
                    questions_asked += len(group)
                    total_cost += answers.cost_usd
                    total_tokens += answers.input_tokens
                    total_elapsed += answers.elapsed_s
                    for qid in group:
                        distributions[qid] = answers.probabilities(qid)

                before = len(beams)
                if level == 1:
                    greedy_first_branch = greedy_roots(beams, distributions)
                beams = prune(expand(beams, distributions), beam_width, floor)
                trace.append(
                    {
                        "level": level,
                        "beams_in": before,
                        "questions": len(questions),
                        "beams_out": len(beams),
                    }
                )
                st.caption(
                    f"level {level}: {len(questions)} question(s) over {before} beam(s) "
                    f"-> {len(beams)} kept"
                )
    except JevError as exc:
        st.error(str(exc))
        return

    # ── Everything below is Python. The model produced distributions only. ──
    best = best_per_item(beams)
    alternatives: dict[int, list[Beam]] = {}
    for beam in sorted(beams, key=lambda b: -b.probability):
        alternatives.setdefault(beam.item, []).append(beam)

    rows = []
    for index, item in enumerate(items):
        beam = best.get(index)
        if beam is None:
            continue
        runner_up = next((b for b in alternatives[index] if b.path != beam.path), None)
        rows.append(
            {
                "item": item.get("title", str(item)),
                "path": " > ".join(beam.path) or "(unplaced)",
                "cumulative_p": round(beam.probability, 4),
                "depth": len(beam.path),
                "state": decide(beam, depth, min_probability),
                "runner_up": " > ".join(runner_up.path) if runner_up else "",
                "runner_up_p": round(runner_up.probability, 4) if runner_up else 0.0,
            }
        )
    out = pd.DataFrame(rows)

    # How often did the winner differ from what pure greedy descent would pick?
    # Greedy would have committed to the level-1 argmax and never looked back.
    recovered = sum(
        1 for index, beam in best.items()
        if beam.path and beam.path[0] != greedy_first_branch.get(index, beam.path[0])
    )

    summary = st.columns(4)
    summary[0].metric("Auto-filed", int((out["state"] == "filed").sum()))
    summary[1].metric("Need review", int((out["state"] == "review").sum()))
    summary[2].metric("Terminated early", int((out["state"] == "partial").sum()),
                      help="A beam that hit the no-match option before reaching a leaf.")
    summary[3].metric("Cost", f"${total_cost:.6f}")

    st.subheader("Search trace")
    st.caption("Beams in, questions asked, beams kept — per level. This is the whole algorithm.")
    st.dataframe(pd.DataFrame(trace), width="stretch", hide_index=True)

    st.subheader("Results")
    st.dataframe(out.sort_values("cumulative_p", ascending=False), width="stretch", hide_index=True)

    st.subheader("Where beam width earned its keep")
    st.caption(
        "Rows whose winning path is NOT the one greedy descent would have taken at "
        "level 1. With k=1 these are misfiled and unrecoverable."
    )
    contested = out[(out["runner_up"] != "") & (out["runner_up_p"] > 0)].copy()
    contested["margin"] = (contested["cumulative_p"] - contested["runner_up_p"]).round(4)
    st.dataframe(
        contested.sort_values("margin")[["item", "path", "cumulative_p", "runner_up", "runner_up_p", "margin"]],
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download classifications CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="classified_items.csv",
        mime="text/csv",
    )
    st.divider()
    st.caption(
        f"{questions_asked} question(s) across {depth} level(s) in {requests} request(s) · "
        f"{total_tokens:,} input tokens · {total_elapsed:.2f}s · ${total_cost:.6f} "
        f"(${total_cost / max(len(items), 1):.8f} per item). "
        f"Beam search changed the level-1 branch for {recovered} item(s)."
    )


if __name__ == "__main__":
    main()
