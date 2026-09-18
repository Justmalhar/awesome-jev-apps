"""Reading Queue -- rank a 200-item read-later pile by what you need NOW.

The whole queue is judged in one request (split only when it genuinely exceeds
the provider's context window), and the ranking is then recomputed for free
every time you move a weight or change how long you have to read.

Reading time is arithmetic, so Python does it from the stored word count. The
model is never asked how long anything takes.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_queue.json"
WORDS_PER_MINUTE = 220
CHARS_PER_TOKEN = 4  # crude but conservative; only used to decide when to split

DEFAULT_NEED = (
    "I am rewriting our Postgres ingest pipeline this month and I keep getting "
    "paged for lock contention. I need things that help me decide on partitioning, "
    "connection pooling and batch sizing. I am not looking for career advice, "
    "company news, or anything about frontend frameworks right now."
)

RELEVANCE_LEVELS = [
    "Nothing in this item touches what the reader said they are working on",
    "Same broad field as the reader's work but not the problem they described",
    "Directly about the problem the reader described, among other things",
    "Squarely and specifically the thing the reader said they need right now",
]

DEPTH_LEVELS = [
    "Headline, announcement, or opinion with no working detail",
    "Explains the idea but stays at the level of a blog introduction",
    "Concrete working detail: code, configuration, numbers, or failure modes",
    "Primary source depth: benchmarks, internals, or original measurement",
]


def load_queue(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def minutes_to_read(item: dict) -> int:
    """Python's job, not the model's."""
    return max(1, round(int(item.get("words", 0)) / WORDS_PER_MINUTE))


def split_batches(items: list[dict], context_tokens: int, questions_per_item: int = 5) -> list[list[int]]:
    """Split the queue only if it will not fit in one request.

    Budget is read from `provider.context_tokens` -- never hardcoded -- and
    halved, because the questions themselves occupy the same window as the state.
    """
    budget = max(1000, context_tokens // 2)
    per_question_overhead = 40
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, item in enumerate(items):
        cost = len(json.dumps(item)) // CHARS_PER_TOKEN + questions_per_item * per_question_overhead
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        batches.append(current)
    return batches


def build_questions(items: list[dict], indices: list[int], need: str) -> dict:
    questions: dict[str, dict] = {}
    for index in indices:
        article = {
            "title": f"`queue[{index}].title`",
            "source": f"`queue[{index}].source`",
            "excerpt": f"`queue[{index}].excerpt`",
        }
        questions[f"relevance_{index}"] = score(
            {"task": "How closely does this match what the reader needs right now?",
             "article": article, "what_they_need_now": need},
            RELEVANCE_LEVELS,
        )
        questions[f"depth_{index}"] = score(
            {"task": "How much working detail does this contain?", "article": article},
            DEPTH_LEVELS,
        )
        questions[f"decides_{index}"] = noul(
            {"task": "Would reading this change a decision the reader is about to make?",
             "article": article, "what_they_need_now": need},
            true="It bears directly on a choice they said they are making now",
            false="Interesting or educational, but no pending decision turns on it",
        )
        questions[f"evergreen_{index}"] = noul(
            {"task": "Will this still be worth reading long after the news cycle it came from?",
             "article": article},
            true="The substance is durable: technique, principle, or a lasting reference",
            false="Tied to a release, an event, or a moment that dates it quickly",
        )
        questions[f"reference_{index}"] = noul(
            {"task": "Is this reference material to consult when needed rather than read end to end?",
             "article": article},
            true="Documentation, API reference, cheatsheet, or a long catalogue to look things up in",
            false="Written to be read straight through as an argument or narrative",
        )
    return questions


def rank(items: list[dict], judgments: list[dict], weights: dict[str, float]) -> list[tuple[float, int]]:
    """Pure policy. Scores are normalised to 0..1 so weights are comparable."""
    ranked = []
    for index, judgment in enumerate(judgments):
        total = (
            weights["relevance"] * judgment["relevance"] / (len(RELEVANCE_LEVELS) - 1)
            + weights["depth"] * judgment["depth"] / (len(DEPTH_LEVELS) - 1)
            + weights["decides"] * judgment["decides"]
            + weights["evergreen"] * judgment["evergreen"]
        )
        ranked.append((total, index))
    ranked.sort(key=lambda pair: (-pair[0], items[pair[1]]["title"]))
    return ranked


def fill_session(ranked: list[tuple[float, int]], items: list[dict], budget_minutes: int) -> list[int]:
    """Greedy: take the best thing that still fits, in rank order."""
    chosen, remaining = [], budget_minutes
    for _, index in ranked:
        cost = minutes_to_read(items[index])
        if cost <= remaining:
            chosen.append(index)
            remaining -= cost
    return chosen


def main() -> None:
    st.set_page_config(page_title="Reading Queue", page_icon="📚", layout="wide")
    st.title("📚 Reading Queue")
    st.caption("Your read-later pile, ranked by what you need this week. One request for the whole queue.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\ncontext {provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Weights")
        st.caption("Re-ranking makes **no** API calls.")
        weights = {
            "relevance": st.slider("Matches what I need now", 0.0, 2.0, 1.0, 0.1),
            "depth": st.slider("Working detail", 0.0, 2.0, 0.6, 0.1),
            "decides": st.slider("Changes a decision", 0.0, 2.0, 0.8, 0.1),
            "evergreen": st.slider("Still good later", 0.0, 2.0, 0.2, 0.1),
        }
        st.divider()
        budget_minutes = st.slider("Minutes I have to read", 10, 240, 45, 5)
        hide_reference = st.checkbox("Park reference material", value=True)
        reference_gate = st.slider("P(reference) to park it", 0.0, 1.0, 0.6, 0.05)
        queue_path = st.text_input("Queue JSON (optional)", value="")

    need = st.text_area("What do you need right now?", value=DEFAULT_NEED, height=120)
    items = load_queue(Path(queue_path).expanduser() if queue_path.strip() else None)
    if not items:
        st.warning("Empty queue. Provide a queue JSON, or restore sample_queue.json.")
        return
    if not queue_path.strip():
        st.caption(f"Using the bundled sample pile: {len(items)} saved items.")

    batches = split_batches(items, provider.context_tokens)
    if st.button(f"Judge {len(items)} items ({len(batches)} request{'s' if len(batches) > 1 else ''})",
                 type="primary"):
        judgments: list[dict | None] = [None] * len(items)
        totals = {"questions": 0, "tokens": 0, "cost": 0.0, "elapsed": 0.0}
        try:
            with st.spinner("Reading the pile..."):
                with JevClient(provider=provider) as client:
                    for indices in batches:
                        questions = build_questions(items, indices, need.strip())
                        answers = client.ask({"queue": items, "reader_need": need}, questions)
                        for index in indices:
                            judgments[index] = {
                                "relevance": answers.score(f"relevance_{index}"),
                                "depth": answers.score(f"depth_{index}"),
                                "decides": answers.noul(f"decides_{index}"),
                                "evergreen": answers.noul(f"evergreen_{index}"),
                                "reference": answers.noul(f"reference_{index}"),
                            }
                        totals["questions"] += len(questions)
                        totals["tokens"] += answers.input_tokens
                        totals["cost"] += answers.cost_usd
                        totals["elapsed"] += answers.elapsed_s
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["queue"] = {"items": items, "judgments": judgments,
                                     "requests": len(batches), **totals}

    state = st.session_state.get("queue")
    if not state:
        st.info("Hit the button to judge the pile.")
        return

    # --- policy only below this line ---
    kept = [(t, i) for t, i in rank(state["items"], state["judgments"], weights)
            if not (hide_reference and state["judgments"][i]["reference"] >= reference_gate)]
    session = set(fill_session(kept, state["items"], budget_minutes))
    parked = len(state["items"]) - len(kept)

    columns = st.columns(4)
    columns[0].metric("In your session", len(session))
    columns[1].metric("Parked as reference", parked)
    columns[2].metric("Requests", state["requests"])
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    st.subheader(f"Read these next ({budget_minutes} min)")
    for total, index in kept:
        item = state["items"][index]
        judgment = state["judgments"][index]
        in_session = index in session
        with st.container(border=True):
            left, right = st.columns([5, 2])
            left.markdown(
                ("✅ " if in_session else "· ") + f"**[{item['title']}]({item.get('url', '#')})**"
            )
            flags = []
            if judgment["decides"] > 0.6:
                flags.append("🎯 bears on a live decision")
            if judgment["evergreen"] < 0.35:
                flags.append("⏳ dates quickly -- read it or drop it")
            if judgment["reference"] > reference_gate:
                flags.append("📖 reference, not a read")
            left.caption(
                f"{item.get('source', '?')} · saved {item.get('saved', '?')} · "
                f"{minutes_to_read(item)} min · relevance {judgment['relevance']:.2f}/3 · "
                f"depth {judgment['depth']:.2f}/3"
                + ("  ·  " + " · ".join(flags) if flags else "")
            )
            right.metric("Score", f"{total:.2f}")
            with st.expander("Excerpt"):
                st.text(item.get("excerpt", ""))

    st.divider()
    st.caption(
        f"{len(state['items'])} items × 5 judgments = {state['questions']} questions in "
        f"{state['requests']} request(s) · {state['elapsed']:.1f}s · "
        f"{state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Weights and the time budget re-rank everything for $0.00."
    )


if __name__ == "__main__":
    main()
