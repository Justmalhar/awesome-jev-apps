"""Data Extractor -- verbatim PICO fields out of a methods section.

Systematic reviews and meta-analyses need the same four fields off every
included paper: population, intervention, comparator, outcomes. Extracting them
by hand is slow, and extracting them with a generative model is worse -- it
paraphrases, and a paraphrased population ("older adults") silently becomes
different data from what the paper said ("adults aged 65 and over").

So this app never asks for text. Python splits the methods section into
candidate sentences, and Jev SELECTS which candidate states each field. The
value that lands in the extraction table is a span that exists in the paper,
byte for byte, with an index you can click back to.

Each field carries two independent judgments:

    reported?  -- a Noul: does this paper describe a comparator at all?
    which?     -- a Choice over the parsed candidates, plus "none"

That separation matters. "No comparator was used" and "a comparator exists but
the selector could not find the sentence" are different facts and must route
differently: the first is data, the second is a human's job.

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
SAMPLE = HERE / "sample_methods.json"

# The four PICO fields, with the wording a data-extraction form would use.
FIELDS = {
    "population": {
        "label": "Population",
        "task": "Which sentence states who was enrolled -- the participants, their condition, and the setting?",
        "reported": "Does this methods section describe the participants who were enrolled?",
        "true": "The text describes the people or units studied, even briefly",
        "false": "The text never says who or what was studied",
    },
    "intervention": {
        "label": "Intervention",
        "task": "Which sentence states what was done to the treatment group -- the intervention, its form and its delivery?",
        "reported": "Does this methods section describe an intervention that was administered?",
        "true": "The text names a treatment, exposure or procedure that was applied",
        "false": "The text is observational with nothing administered, or never says what was done",
    },
    "comparator": {
        "label": "Comparator",
        "task": "Which sentence states what the intervention was compared against -- the control, placebo, usual care or alternative arm?",
        "reported": "Does this methods section describe a comparison group or control condition?",
        "true": "The text names a control arm, placebo, waitlist, usual care or an alternative treatment",
        "false": "The study is single-arm, or the text never names anything the intervention was compared against",
    },
    "outcomes": {
        "label": "Outcomes",
        "task": "Which sentence states what was measured -- the primary outcome and how it was assessed?",
        "reported": "Does this methods section describe what was measured as an outcome?",
        "true": "The text names a measure, endpoint, scale or instrument that was recorded",
        "false": "The text never says what was measured",
    },
}

NO_MATCH = "no_candidate_states_it"
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def split_candidates(text: str, max_candidates: int = 40) -> list[str]:
    """Parse the methods text into the spans Jev is allowed to choose from.

    Done in Python precisely so the returned value is verbatim. `max_candidates`
    keeps one enormous methods section from blowing out the options list.
    """
    parts = [s.strip() for s in SENTENCE_BREAK.split(text.strip()) if s.strip()]
    return parts[:max_candidates]


def build_questions(records: list[dict], candidates: list[list[str]]) -> dict:
    """Two questions per field per record, all independent, all in one request."""
    questions: dict[str, dict] = {}
    for index, spans in enumerate(candidates):
        reference = f"`records[{index}].methods`"
        for field, spec in FIELDS.items():
            questions[f"has_{index}_{field}"] = noul(
                {"task": spec["reported"], "methods_section": reference},
                true=spec["true"],
                false=spec["false"],
            )
            options: dict[str, str] = {
                f"s{s_index}": span for s_index, span in enumerate(spans)
            }
            options[NO_MATCH] = (
                "None of the sentences above states this field; it is absent or only implied"
            )
            questions[f"pick_{index}_{field}"] = choice(
                {
                    "task": spec["task"],
                    "methods_section": reference,
                    "note": "Pick the single sentence that states it most directly.",
                },
                options,
            )
    return questions


def resolve(
    reported: float, selected: str, confidence: float, report_min: float, confidence_min: float
) -> tuple[str, str]:
    """Policy, in Python. Three outcomes, because two would hide the failure mode.

    A field is only recorded when the paper says it is there AND a specific span
    was picked AND the selection was not a coin flip between candidates.
    """
    if reported <= report_min:
        return "not_reported", f"not described in the methods (P={reported:.2f})"
    if selected == NO_MATCH:
        return "review", "described somewhere, but no single sentence states it"
    if confidence < confidence_min:
        return "review", f"candidates too close to call (confidence {confidence:.2f})"
    return "extracted", f"P(reported)={reported:.2f}, confidence {confidence:.2f}"


def main() -> None:
    st.set_page_config(page_title="Data Extractor", page_icon="🔬", layout="wide")
    st.title("🔬 Data Extractor")
    st.caption("Population, intervention, comparator, outcomes -- selected verbatim, never paraphrased.")

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
        st.caption("Tune against a hand-extracted pilot set from your own review.")
        report_min = st.slider("Below this P(field is described), call it not reported", 0.01, 0.6, 0.30, 0.01)
        confidence_min = st.slider("Minimum selection confidence to auto-record", 0.3, 0.95, 0.60, 0.01)
        max_candidates = st.slider("Max candidate sentences per paper", 10, 60, 40, 5)

    uploaded = st.file_uploader("Methods sections (JSON list of {id, title, methods})", type=["json"])
    if uploaded:
        records = json.loads(uploaded.read().decode("utf-8"))
    else:
        records = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(records)} methods sections.")

    candidates = [split_candidates(r["methods"], max_candidates) for r in records]
    st.caption(
        f"{sum(len(c) for c in candidates)} candidate spans parsed in Python. "
        "Jev picks among them; it is never asked to write one."
    )

    if not st.button(f"Extract from {len(records)} papers", type="primary"):
        return

    questions = build_questions(records, candidates)
    try:
        with st.spinner(f"{len(questions)} judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"records": records}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    detail: list[tuple[dict, list[tuple]]] = []
    for index, record in enumerate(records):
        row = {"paper": record.get("title", record.get("id", f"#{index}"))}
        cells = []
        for field, spec in FIELDS.items():
            reported = answers.noul(f"has_{index}_{field}")
            selected = answers.choice(f"pick_{index}_{field}")
            confidence = answers.confidence(f"pick_{index}_{field}")
            status, reason = resolve(reported, selected, confidence, report_min, confidence_min)
            if status == "extracted":
                span = candidates[index][int(selected[1:])]
            elif status == "not_reported":
                span = "— not reported —"
            else:
                span = "⚠️ needs a human"
            row[spec["label"]] = span
            cells.append((spec["label"], status, reason, span))
        rows.append(row)
        detail.append((record, cells))

    total_cells = len(records) * len(FIELDS)
    flagged = sum(1 for _, cells in detail for _, status, _, _ in cells if status == "review")
    columns = st.columns(3)
    columns[0].metric("Papers", len(records))
    columns[1].metric("Fields extracted", total_cells - flagged)
    columns[2].metric("Needs a human", flagged)

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Per-field provenance")
    for record, cells in detail:
        with st.expander(record.get("title", record.get("id", "paper"))):
            for label, status, reason, span in cells:
                mark = {"extracted": "✅", "not_reported": "➖", "review": "⚠️"}[status]
                st.markdown(f"{mark} **{label}** — {reason}")
                st.caption(span)

    st.divider()
    st.caption(
        f"{len(records)} papers × {len(FIELDS)} fields × 2 questions = {len(questions)} judgments, "
        f"1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
