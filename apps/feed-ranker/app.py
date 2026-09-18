"""Feed Ranker -- rank today's Hacker News against your interests, written as a sentence.

Two ideas worth stealing from this app:

1. N stories x M dimensions = N*M questions in ONE request. Questions run in
   parallel against a state ingested once, so batching is dramatically cheaper
   and faster than looping. The app shows the loop's cost next to the real one.

2. Score once, reweight forever. The sliders below re-rank instantly and make
   ZERO API calls, because changing a weight does not change the evidence or
   the meaning of the questions. Raw judgments stay reusable; policy lives in
   code where you can tune it for free.

    uv run streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import httpx
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
HN_FRONT_PAGE = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50"

DEFAULT_INTERESTS = (
    "I build AI developer tooling. I care about inference cost, local models, "
    "compilers, and databases. I do not care about crypto, startup drama, "
    "hiring posts, or US political news."
)

# Each dimension is one reusable judgment. Weights are policy and live in code.
DIMENSIONS = {
    "relevance": {
        "label": "Matches my interests",
        "levels": [
            "Unrelated to the stated interests",
            "Adjacent -- same broad field, not what they asked for",
            "Squarely inside the stated interests",
            "Exactly the kind of thing they said they want, specifically",
        ],
        "default_weight": 1.0,
    },
    "depth": {
        "label": "Substance over noise",
        "levels": [
            "Announcement, press release, or pure opinion",
            "Reports something real but shallow",
            "Substantive technical content with detail",
            "Deep primary-source work: benchmarks, internals, or original research",
        ],
        "default_weight": 0.6,
    },
    "novelty": {
        "label": "Tells me something new",
        "levels": [
            "Rehashes widely known material",
            "Familiar topic with a modest new angle",
            "Genuinely new result, tool, or idea",
        ],
        "default_weight": 0.4,
    },
}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_front_page() -> list[dict]:
    response = httpx.get(HN_FRONT_PAGE, timeout=20)
    response.raise_for_status()
    stories = []
    for hit in response.json().get("hits", []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        stories.append(
            {
                "title": title,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "points": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
            }
        )
    return stories


def build_questions(stories: list[dict], interests: str) -> dict:
    """One Score per story per dimension, plus a junk filter. All in one call."""
    questions: dict[str, dict] = {}
    for index, story in enumerate(stories):
        for key, spec in DIMENSIONS.items():
            questions[f"{key}__{index}"] = score(
                {
                    "task": spec["label"],
                    "rate_this_story": f"`stories[{index}].title`",
                    "reader_interests": interests,
                },
                spec["levels"],
            )
        questions[f"junk__{index}"] = noul(
            {
                "task": "Is this a hiring post, job ad, fundraising announcement, or promotional item?",
                "story": f"`stories[{index}].title`",
            },
            true="Hiring, 'Who is hiring', job ad, funding round, or product promotion",
            false="Ordinary technical or informational submission",
        )
    return questions


def main() -> None:
    st.set_page_config(page_title="Feed Ranker", page_icon="📰", layout="wide")
    st.title("📰 Feed Ranker")
    st.caption("Rank today's Hacker News against your interests, described in one sentence.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        story_limit = st.slider("Stories to score", 10, 50, 30, 5)
        st.divider()
        st.subheader("Weights")
        st.caption("Move these freely — re-ranking makes **no** API calls.")
        weights = {
            key: st.slider(spec["label"], 0.0, 2.0, spec["default_weight"], 0.1)
            for key, spec in DIMENSIONS.items()
        }
        hide_junk = st.checkbox("Hide hiring/promo posts", value=True)
        junk_threshold = st.slider("Junk threshold P(yes)", 0.0, 1.0, 0.6, 0.05)

    interests = st.text_area("What do you care about?", value=DEFAULT_INTERESTS, height=100)

    if st.button("Fetch & score front page", type="primary"):
        try:
            stories = fetch_front_page()[:story_limit]
        except httpx.HTTPError as exc:
            st.error(f"Could not reach Hacker News: {exc}")
            return
        if not stories:
            st.warning("No stories returned.")
            return

        questions = build_questions(stories, interests.strip())
        try:
            with st.spinner(f"Scoring {len(stories)} stories x {len(DIMENSIONS) + 1} judgments..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"stories": stories, "reader_interests": interests}, questions)
        except JevError as exc:
            st.error(str(exc))
            return

        # Cache the RAW judgments. Sliders recombine these for free.
        st.session_state["scored"] = {
            "stories": stories,
            "judgments": [
                {
                    **{key: answers.score(f"{key}__{i}") for key in DIMENSIONS},
                    "junk": answers.noul(f"junk__{i}"),
                }
                for i in range(len(stories))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    scored = st.session_state.get("scored")
    if not scored:
        st.info("Hit the button to pull the front page and score it.")
        return

    # --- pure code from here down: no inference, just policy ---
    rows = []
    for story, judgment in zip(scored["stories"], scored["judgments"]):
        if hide_junk and judgment["junk"] >= junk_threshold:
            continue
        # Normalise each score to 0..1 so weights are comparable across rubrics.
        total = sum(
            weights[key] * (judgment[key] / (len(DIMENSIONS[key]["levels"]) - 1))
            for key in DIMENSIONS
        )
        rows.append((total, story, judgment))
    rows.sort(key=lambda row: -row[0])

    st.subheader(f"{len(rows)} stories, your order")
    for rank, (total, story, judgment) in enumerate(rows, start=1):
        with st.container(border=True):
            left, right = st.columns([5, 2])
            left.markdown(f"**{rank}. [{story['title']}]({story['url']})**")
            left.caption(
                " · ".join(
                    f"{DIMENSIONS[key]['label']}: {judgment[key]:.2f}" for key in DIMENSIONS
                )
                + f" · junk P={judgment['junk']:.2f}"
            )
            right.metric("Score", f"{total:.2f}")
            right.caption(f"{story['points']} pts · {story['comments']} comments")

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Judgments", scored["questions"])
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{scored['elapsed']:.1f} s")
    columns[3].metric("Cost", f"${scored['cost']:.6f}")
    st.caption(
        f"{scored['tokens']:,} input tokens. The same work as {scored['questions']} separate "
        f"calls. Every slider move above re-ranked these for $0.00."
    )


if __name__ == "__main__":
    main()
