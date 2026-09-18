"""Age Appropriateness -- rate content against the age bands YOU define.

Age ratings are not a fact about content. They are a fact about a jurisdiction,
a platform, and a promise made to parents. A kids' video service, a games
storefront and a social feed looking at the same clip will legitimately land on
three different bands, and each of them has the band definitions written down
somewhere -- usually in a policy document that no classifier has ever read.

So here the bands ARE the rubric: the operator types them into the sidebar, one
per line, ordered youngest to oldest, and they become the levels of the Score.
Rewriting the rubric rewrites the question. Beside it, the operator's own list
of specific concerns becomes one Noul each, because "could a child copy this at
home" and "does this push purchases" are independent of the band and of each
other.

Ratings that affect what a child can see should be signed off by a person.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_content.json"
SAMPLE_BANDS = HERE / "sample_bands.txt"
SAMPLE_CONCERNS = HERE / "sample_concerns.txt"
SUMMARY_CHARS = 1500
MIN_LEVEL_CHARS = 16  # a level that short is a label, and a label is not a rubric

# What is actually driving the rating. `nothing_notable` is the mandatory
# escape hatch -- most content on a well-run platform is unremarkable.
DRIVER_KINDS = {
    "peril_or_fear": "Frightening, threatening, or distressing moments, including jump scares and menace.",
    "imitable_risk": "A physical act a viewer could copy and be hurt by, presented without discouragement.",
    "language": "Swearing, slurs, or crude humour is the main thing pushing the rating up.",
    "commercial_pressure": "Spending mechanics: purchases, loot boxes, streaks, or pressure to pay to continue.",
    "social_contact": "Open contact with strangers, private messaging, or sharing of personal details.",
    "mature_themes": "Substances, gambling, relationships, or other subject matter aimed past childhood.",
    "nothing_notable": "Nothing in the content pushes the rating above the youngest band.",
}

DEFAULT_GATES = {
    "confidence": 0.55,  # Score confidence below which a rating is not trusted
    "concern": 0.6,      # P(concern present) to flag one of the operator's concerns
    "borderline": 0.45,  # P(two reviewers land in different bands) that forces a human
    "concern_floor": 2,  # a flagged concern below this band index needs a person
    "margin": 0.25,      # how far between two bands before we round to the older one
}


def parse_lines(text: str) -> list[str]:
    """One rubric level per line, `#` comments and blanks dropped."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def parse_concerns(text: str) -> list[dict]:
    """`C2: Frightening imagery` -> {"id": "C2", "text": "Frightening imagery"}."""
    concerns: list[dict] = []
    for line in parse_lines(text):
        head, sep, body = line.partition(":")
        head, body = head.strip(), body.strip()
        if sep and body and " " not in head and len(head) <= 12 and any(c.isdigit() for c in head):
            concerns.append({"id": head, "text": body})
        else:
            concerns.append({"id": f"C{len(concerns) + 1}", "text": line})
    return concerns


def rubric_problems(bands: list[str]) -> list[str]:
    """Reject a rubric the model cannot use. Enforced before any request is sent."""
    problems = []
    if len(bands) < 2:
        problems.append("A rubric needs at least two bands, youngest first.")
    for band in bands:
        if len(band) < MIN_LEVEL_CHARS:
            problems.append(f"Band {band!r} is a label, not a situation — describe what belongs in it.")
    return problems


def build_questions(items: list[dict], bands: list[str], concerns: list[dict]) -> dict:
    """One Score against the operator's own bands, plus one Noul per concern."""
    questions: dict[str, dict] = {}
    for index in range(len(items)):
        reference = {
            "title": f"`content[{index}].title`",
            "kind": f"`content[{index}].kind`",
            "summary": f"`content[{index}].summary`",
            "transcript_excerpt": f"`content[{index}].transcript_excerpt`",
        }
        questions[f"band_{index}"] = score(
            {"task": "Which of these audience bands does this content belong in, using the platform's own definitions?",
             "content": reference},
            bands,
        )
        for position, concern in enumerate(concerns):
            questions[f"concern_{index}_{position}"] = noul(
                {
                    "task": "Is the concern described below present in this content?",
                    "concern": f"`concerns[{position}].text`",
                    "content": reference,
                },
                true="The content plainly contains the thing described",
                false="It does not contain it, or the concern describes something else",
            )
        # Independent of the band: content can be borderline at any level, and a
        # borderline rating is the one a parent complains about.
        questions[f"borderline_{index}"] = noul(
            {"task": "Would two reviewers applying these same band definitions plausibly put this in different bands?",
             "content": reference},
            true="It sits between two of the definitions, or depends on a judgment the definitions do not settle",
            false="One band clearly fits it better than the neighbouring ones",
        )
        questions[f"driver_{index}"] = choice(
            {"task": "What is the single thing most responsible for this content's rating?",
             "content": reference},
            DRIVER_KINDS,
        )
    return questions


def assign_band(value: float, bands: list[str], margin: float) -> int:
    """Turn a between-levels Score into a band index, in Python.

    Rounds UP when the score sits meaningfully between two bands: for age
    ratings the safe direction is the older audience, and that asymmetry is a
    policy choice, so it lives here and not in the instruction.
    """
    if not bands:
        raise ValueError("no bands")
    lower = max(0, min(int(value), len(bands) - 1))
    if lower >= len(bands) - 1:
        return len(bands) - 1
    return lower + 1 if (value - lower) >= margin else lower


def decide(band_index: int, confidence: float, borderline: float, concerns: dict, gates: dict) -> str:
    """Route one item. Pure, so the whole policy is retunable without inference."""
    flagged = [name for name, probability in concerns.items() if probability >= gates["concern"]]
    if confidence < gates["confidence"]:
        return "human_review"
    if borderline >= gates["borderline"]:
        return "human_review"
    if flagged and band_index < gates["concern_floor"]:
        # A named concern inside a band meant for young children is exactly the
        # combination a parent writes in about. It never auto-publishes.
        return "human_review"
    return "publish"


def main() -> None:
    st.set_page_config(page_title="Age Appropriateness", page_icon="🧒", layout="wide")
    st.title("🧒 Age Appropriateness")
    st.caption("Your age bands, your concerns, your thresholds — applied to a content queue.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Thresholds")
        gates = {
            "confidence": st.slider("Confidence to trust a rating", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
            "concern": st.slider("P(concern present) to flag it", 0.0, 1.0, DEFAULT_GATES["concern"], 0.05),
            "borderline": st.slider("P(reviewers disagree) that forces a human", 0.0, 1.0, DEFAULT_GATES["borderline"], 0.05),
            "concern_floor": st.slider("Band index below which any concern needs a person", 0, 5, DEFAULT_GATES["concern_floor"], 1),
            "margin": st.slider("Distance between bands before rounding to the older one", 0.0, 1.0, DEFAULT_GATES["margin"], 0.05),
        }
        batch_size = st.slider("Items per request", 2, 25, 8, 1)

    left, right = st.columns(2)
    with left:
        bands_text = st.text_area(
            "Your age bands — youngest first, one per line. These become the rubric.",
            value=SAMPLE_BANDS.read_text(encoding="utf-8") if SAMPLE_BANDS.is_file() else "",
            height=220,
        )
    with right:
        concerns_text = st.text_area(
            "Your specific concerns — one per line, each becomes its own flag.",
            value=SAMPLE_CONCERNS.read_text(encoding="utf-8") if SAMPLE_CONCERNS.is_file() else "",
            height=220,
        )

    bands = parse_lines(bands_text)
    problems = rubric_problems(bands)
    if problems:
        for problem in problems:
            st.error(problem)
        return
    concerns = parse_concerns(concerns_text)
    if not concerns:
        st.warning("Write at least one concern.")
        return

    uploaded = st.file_uploader("Content JSON: [{title, kind, summary, transcript_excerpt}]", type=["json"])
    if uploaded:
        items = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        items = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(items)} items.")
    else:
        st.warning("Upload a content JSON.")
        return

    for item in items:
        item["summary"] = str(item.get("summary", ""))[:SUMMARY_CHARS]
        item["transcript_excerpt"] = str(item.get("transcript_excerpt", ""))[:SUMMARY_CHARS]

    st.caption(f"{len(bands)} bands · {len(concerns)} concerns · {len(items)} items.")
    if not st.button(f"Rate {len(items)} items", type="primary"):
        return

    groups = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
    rated: list[dict] = []
    cost = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Rating against {len(bands)} bands in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, bands, concerns)
                    answers = client.ask(
                        {"bands": bands, "concerns": concerns, "content": group}, questions
                    )
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, item in enumerate(group):
                        raw = answers.score(f"band_{index}")
                        found = {
                            concern["id"]: answers.noul(f"concern_{index}_{position}")
                            for position, concern in enumerate(concerns)
                        }
                        rated.append(
                            {
                                **item,
                                "raw_band": raw,
                                "band_index": assign_band(raw, bands, gates["margin"]),
                                "confidence": answers.confidence(f"band_{index}"),
                                "concerns": found,
                                "borderline": answers.noul(f"borderline_{index}"),
                                "driver": answers.choice(f"driver_{index}"),
                                "driver_confidence": answers.confidence(f"driver_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in rated:
        item["band"] = bands[item["band_index"]]
        item["flagged"] = [k for k, v in item["concerns"].items() if v >= gates["concern"]]
        item["action"] = decide(
            item["band_index"], item["confidence"], item["borderline"], item["concerns"], gates
        )

    counts = Counter(item["action"] for item in rated)
    columns = st.columns(3)
    columns[0].metric("Items", len(rated))
    columns[1].metric("Rated automatically", counts["publish"])
    columns[2].metric("To a human", counts["human_review"])

    st.subheader("🧑 Human review queue")
    st.caption("Low confidence, borderline between bands, or a concern inside a young band.")
    queue = sorted([i for i in rated if i["action"] == "human_review"], key=lambda i: i["confidence"])
    if not queue:
        st.info("Empty this run. An always-empty queue means the gates are too tight to be useful.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['title']}** · {item['kind']}")
            st.write(item["summary"])
            st.caption(
                f"proposed band {item['band_index']} — {item['band'][:70]} · "
                f"raw {item['raw_band']:.2f} · confidence {item['confidence']:.2f} · "
                f"borderline P={item['borderline']:.2f} · concerns: {', '.join(item['flagged']) or 'none'}"
            )

    st.subheader("Rated")
    for item in [i for i in rated if i["action"] == "publish"]:
        with st.container(border=True):
            st.markdown(f"**{item['title']}** — band {item['band_index']}")
            st.caption(
                f"{item['band'][:90]} · raw {item['raw_band']:.2f} · "
                f"driver {item['driver']} ({item['driver_confidence']:.2f}) · "
                f"concerns: {', '.join(item['flagged']) or 'none'}"
            )

    st.subheader("Band distribution")
    st.caption("Counted in Python from the Scores, never asked of the model.")
    spread = Counter(item["band_index"] for item in rated)
    st.bar_chart({f"band {n}": spread.get(n, 0) for n in range(len(bands))})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(rated)} items in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(rated), 1):.8f} per item). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
