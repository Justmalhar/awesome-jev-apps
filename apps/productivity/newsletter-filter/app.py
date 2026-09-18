"""Newsletter Filter -- keep only the items in a digest you would genuinely have read.

Five newsletters arrive with 25 items between them and you read two. This app
decides which two, and shows you the other 23 collapsed so you can check it was
right. Nothing is rewritten: Jev cannot generate, so every blurb is the one the
newsletter sent.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_digest.json"

DEFAULT_INTERESTS = (
    "I run a small data platform team. I want database internals, storage engines, "
    "query planners, and honest postmortems with numbers in them. I will read anything "
    "with a benchmark I can reproduce. I do not want funding rounds, AI hype, "
    "hiring posts, conference announcements, or opinion pieces about remote work."
)

MATCH_LEVELS = [
    "The subject is outside everything the reader listed as interesting",
    "Nearby subject matter, but not the specific thing the reader asked for",
    "Clearly one of the subjects the reader named",
    "Precisely the kind of item the reader said they will always read",
]

SUBSTANCE_LEVELS = [
    "A headline and a link with nothing else: no claim, no detail, no result",
    "Describes what something is, without evidence or working detail",
    "Contains a specific claim backed by detail: numbers, code, or a mechanism",
    "Reports original measurement or primary work the reader could check themselves",
]


def load_digest(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def build_questions(items: list[dict], interests: str) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(items)):
        entry = {
            "newsletter": f"`items[{index}].newsletter`",
            "title": f"`items[{index}].title`",
            "blurb": f"`items[{index}].blurb`",
        }
        questions[f"match_{index}"] = score(
            {"task": "How well does this item match what the reader said they want?",
             "item": entry, "reader_interests": interests},
            MATCH_LEVELS,
        )
        questions[f"substance_{index}"] = score(
            {"task": "How much substance does this item carry?", "item": entry}, SUBSTANCE_LEVELS
        )
        questions[f"sponsored_{index}"] = noul(
            {"task": "Is this a paid placement, advertisement, or promotion of a product?",
             "item": entry},
            true="Marked as sponsored, or written as promotion for a company or product",
            false="Editorial content chosen on merit",
        )
        questions[f"withholds_{index}"] = noul(
            {"task": "Does the headline withhold the fact it promises so the reader has to click?",
             "item": entry},
            true="Teases a result, reason, or number without stating it",
            false="The headline or blurb states what the item is actually about",
        )
        questions[f"everywhere_{index}"] = noul(
            {"task": "Is this item about news the reader has very likely already seen elsewhere?",
             "item": entry},
            true="Major release, acquisition, or story that every outlet in the field carried",
            false="Niche, original, or from a corner the reader would not stumble on",
        )
    return questions


def keeps(judgment: dict, *, match_gate: float, substance_gate: float, noul_gate: float,
          drop_duplicates: bool) -> bool:
    """Pure policy: what survives the filter."""
    if judgment["sponsored"] >= noul_gate:
        return False
    if drop_duplicates and judgment["everywhere"] >= noul_gate:
        return False
    if judgment["match"] < match_gate:
        return False
    return judgment["substance"] >= substance_gate


def dropped_because(judgment: dict, *, match_gate: float, substance_gate: float,
                    noul_gate: float, drop_duplicates: bool) -> str:
    """Why it was cut -- so a wrong cut is visible rather than silent."""
    if judgment["sponsored"] >= noul_gate:
        return "sponsored"
    if drop_duplicates and judgment["everywhere"] >= noul_gate:
        return "you have seen this everywhere"
    if judgment["match"] < match_gate:
        return "not your subject"
    if judgment["substance"] < substance_gate:
        return "nothing in it"
    return "kept"


def main() -> None:
    st.set_page_config(page_title="Newsletter Filter", page_icon="📨", layout="wide")
    st.title("📨 Newsletter Filter")
    st.caption("Only the items you would genuinely have read. Everything else is one click away.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Filter")
        st.caption("Re-filtering makes **no** API calls.")
        match_gate = st.slider("Match needed to keep", 0.0, 3.0, 2.0, 0.1)
        substance_gate = st.slider("Substance needed to keep", 0.0, 3.0, 1.5, 0.1)
        noul_gate = st.slider("P(yes) for a veto", 0.0, 1.0, 0.6, 0.05)
        drop_duplicates = st.checkbox("Drop what everyone else already covered", value=True)
        digest_path = st.text_input("Digest JSON (optional)", value="")

    interests = st.text_area("What do you actually read?", value=DEFAULT_INTERESTS, height=120)
    items = load_digest(Path(digest_path).expanduser() if digest_path.strip() else None)
    if not items:
        st.warning("No digest items. Provide a digest JSON, or restore sample_digest.json.")
        return
    sources = sorted({item.get("newsletter", "?") for item in items})
    if not digest_path.strip():
        st.caption(f"Using the bundled sample: {len(items)} items from {len(sources)} newsletters.")

    if st.button(f"Filter {len(items)} items", type="primary"):
        questions = build_questions(items, interests.strip())
        try:
            with st.spinner("Reading this week's digests..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"items": items, "reader_interests": interests}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["digest"] = {
            "items": items,
            "judgments": [
                {
                    "match": answers.score(f"match_{i}"),
                    "substance": answers.score(f"substance_{i}"),
                    "sponsored": answers.noul(f"sponsored_{i}"),
                    "withholds": answers.noul(f"withholds_{i}"),
                    "everywhere": answers.noul(f"everywhere_{i}"),
                }
                for i in range(len(items))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    state = st.session_state.get("digest")
    if not state:
        st.info("Hit the button to filter this week's digests.")
        return

    # --- policy only below this line ---
    gates = {"match_gate": match_gate, "substance_gate": substance_gate,
             "noul_gate": noul_gate, "drop_duplicates": drop_duplicates}
    kept, cut = [], []
    for item, judgment in zip(state["items"], state["judgments"]):
        (kept if keeps(judgment, **gates) else cut).append((item, judgment))
    kept.sort(key=lambda pair: -(pair[1]["match"] + pair[1]["substance"]))

    columns = st.columns(4)
    columns[0].metric("Worth reading", len(kept))
    columns[1].metric("Cut", len(cut))
    columns[2].metric("Newsletters", len(sources))
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    st.subheader("Read these")
    if not kept:
        st.caption("Nothing survived. Loosen the gates, or unsubscribe from something.")
    for item, judgment in kept:
        with st.container(border=True):
            st.markdown(f"**[{item['title']}]({item.get('url', '#')})**")
            st.write(item.get("blurb", ""))
            flags = []
            if judgment["withholds"] > noul_gate:
                flags.append("🎣 headline withholds the point")
            if judgment["everywhere"] > noul_gate:
                flags.append("📰 widely covered")
            st.caption(
                f"{item.get('newsletter', '?')} · match {judgment['match']:.2f}/3 · "
                f"substance {judgment['substance']:.2f}/3"
                + ("  ·  " + " · ".join(flags) if flags else "")
            )

    with st.expander(f"What was cut ({len(cut)}) — check the filter here"):
        for item, judgment in sorted(cut, key=lambda pair: -pair[1]["match"]):
            st.markdown(
                f"~~{item['title']}~~ · _{dropped_because(judgment, **gates)}_ · "
                f"{item.get('newsletter', '?')} · match {judgment['match']:.2f}/3"
            )

    st.divider()
    st.caption(
        f"{len(state['items'])} items × 5 judgments = {state['questions']} questions in 1 request · "
        f"{state['elapsed']:.1f}s · {state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Every gate above re-filters the week for $0.00."
    )


if __name__ == "__main__":
    main()
