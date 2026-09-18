"""Interview Coder -- apply a thematic codebook to interview transcripts.

Qualitative coding is the slow half of qualitative research. A codebook of a
dozen themes, forty transcripts, and two coders who must agree: every passage
gets read against every code, twice, and inter-rater reliability is computed
over the result.

The structural point: codes are NOT mutually exclusive. A single passage can be
"cost concern" and "trust in clinician" and "waiting time" at once -- that is
the normal case, not an edge case. So this is one Noul per code per passage,
never a Choice over codes and never a rubric. Collapsing co-occurring codes into
one Choice would force the model to nominate a winner and quietly delete the
co-occurrence that the analysis is about.

Because each code is its own Noul, each code gets its own threshold. A code with
a fuzzy boundary ("emotional burden") can be tuned to flag more for human review
without touching a crisp one ("cost concern"), and retuning re-runs no inference.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_TRANSCRIPT = HERE / "sample_transcript.txt"
SAMPLE_CODEBOOK = HERE / "sample_codebook.json"

TURN = re.compile(r"^(?P<speaker>[A-Z][A-Za-z0-9 .'-]{0,30}):\s*(?P<text>.+)$")
CHARS_PER_TOKEN = 4  # crude but stable; we only need a safe upper bound


def split_passages(transcript: str, min_chars: int = 40) -> list[dict]:
    """Split a transcript into speaker turns. Interviewer turns are kept as
    context but are not coded -- codes describe what participants said."""
    passages: list[dict] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        match = TURN.match(line)
        if not match:
            if passages:  # continuation of the previous turn
                passages[-1]["text"] += " " + line
            continue
        speaker = match.group("speaker").strip()
        passages.append({"speaker": speaker, "text": match.group("text").strip()})
    return [p for p in passages if len(p["text"]) >= min_chars]


def plan_batches(sizes: list[int], budget: int) -> list[list[int]]:
    """Greedily pack passage indices into requests that fit the context window.

    This is batching for SIZE, not a dependency -- every question is still
    independent, and we send as few requests as the window allows. An item
    larger than the budget gets a request to itself rather than being dropped.
    """
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, size in enumerate(sizes):
        if current and used + size > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += size
    if current:
        batches.append(current)
    return batches


def build_questions(passages: list[dict], indices: list[int], codebook: list[dict]) -> dict:
    """One Noul per (passage, code). Co-occurring codes stay independent."""
    questions: dict[str, dict] = {}
    for index in indices:
        for code in codebook:
            questions[f"c_{index}_{code['code']}"] = noul(
                {
                    "task": "Does this passage from an interview express the theme below?",
                    "theme": code["name"],
                    "definition": code["definition"],
                    "passage": f"`passages[{index}].text`",
                },
                true=code["applies_when"],
                false=code["does_not_apply_when"],
            )
    return questions


def assign_codes(
    probabilities: dict[str, float], apply_at: float, review_at: float
) -> tuple[list[str], list[str]]:
    """Two bands, in Python. Applied codes, and codes close enough to argue about.

    Anything above `apply_at` is coded. Anything between `review_at` and
    `apply_at` is surfaced to the human rather than silently dropped -- in
    qualitative work a missed theme is far more expensive than a spurious one
    a coder rejects in three seconds.
    """
    applied = sorted(c for c, p in probabilities.items() if p >= apply_at)
    borderline = sorted(
        c for c, p in probabilities.items() if review_at <= p < apply_at
    )
    return applied, borderline


def main() -> None:
    st.set_page_config(page_title="Interview Coder", page_icon="🗣️", layout="wide")
    st.title("🗣️ Interview Coder")
    st.caption("Apply a thematic codebook to transcripts. Codes co-occur, so every code is its own question.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Coding thresholds")
        st.caption("Calibrate against a double-coded pilot transcript.")
        apply_at = st.slider("Apply the code at or above", 0.4, 0.95, 0.65, 0.01)
        review_at = st.slider("Surface for a human above", 0.1, 0.6, 0.35, 0.01)
        if review_at >= apply_at:
            st.warning("The review band must sit below the apply threshold.")
        st.divider()
        st.caption(f"Context window: {provider.context_tokens:,} tokens.")

    left, right = st.columns([3, 2])
    with left:
        transcript = st.text_area(
            "Transcript",
            value=SAMPLE_TRANSCRIPT.read_text(encoding="utf-8"),
            height=300,
        )
    with right:
        codebook_text = st.text_area(
            "Codebook (JSON)",
            value=SAMPLE_CODEBOOK.read_text(encoding="utf-8"),
            height=300,
        )

    try:
        codebook = json.loads(codebook_text)
        assert codebook and all(
            {"code", "name", "definition", "applies_when", "does_not_apply_when"} <= set(c)
            for c in codebook
        )
    except (json.JSONDecodeError, AssertionError) as exc:
        st.error(f"Codebook must be a JSON list of code objects: {exc}")
        return

    passages = split_passages(transcript)
    coded_passages = [p for p in passages if not p["speaker"].lower().startswith("interviewer")]
    if not coded_passages:
        st.warning("No participant turns found. Lines must look like `Speaker: text`.")
        return

    per_passage_tokens = [
        len(p["text"]) // CHARS_PER_TOKEN + len(codebook) * 60 for p in coded_passages
    ]
    budget = int(provider.context_tokens * 0.4)
    batches = plan_batches(per_passage_tokens, budget)

    st.caption(
        f"{len(coded_passages)} participant passages × {len(codebook)} codes = "
        f"{len(coded_passages) * len(codebook)} judgments, packed into {len(batches)} request(s)."
    )

    if not st.button("Code the transcript", type="primary"):
        return

    results: dict[int, dict[str, float]] = {}
    try:
        with JevClient(provider=provider) as client:
            for number, indices in enumerate(batches, start=1):
                with st.spinner(f"Request {number} of {len(batches)}..."):
                    questions = build_questions(coded_passages, indices, codebook)
                    answers = client.ask({"passages": coded_passages}, questions)
                for index in indices:
                    results[index] = {
                        code["code"]: answers.noul(f"c_{index}_{code['code']}")
                        for code in codebook
                    }
            elapsed = answers.elapsed_s
            totals = (client.total_cost_usd, client.total_input_tokens, client.calls)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    tally: Counter[str] = Counter()
    uncoded = 0
    for index, passage in enumerate(coded_passages):
        applied, borderline = assign_codes(results[index], apply_at, review_at)
        tally.update(applied)
        if not applied and not borderline:
            uncoded += 1
        rows.append(
            {
                "speaker": passage["speaker"],
                "passage": passage["text"],
                "codes": ", ".join(applied) or "—",
                "borderline": ", ".join(borderline) or "—",
            }
        )

    columns = st.columns(4)
    columns[0].metric("Passages", len(coded_passages))
    columns[1].metric("Code applications", sum(tally.values()))
    columns[2].metric("Multi-coded passages", sum(1 for r in rows if "," in r["codes"]))
    columns[3].metric("Uncoded", uncoded)
    if uncoded:
        st.caption(
            f"{uncoded} passage(s) matched nothing. In grounded work that is a signal the "
            "codebook is incomplete, not that the passage is empty."
        )

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Code frequency")
    names = {c["code"]: c["name"] for c in codebook}
    st.dataframe(
        pd.DataFrame(
            [{"code": c, "theme": names[c], "passages": tally.get(c, 0)} for c in names],
        ).sort_values("passages", ascending=False),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Per-passage probabilities")
    for index, passage in enumerate(coded_passages):
        with st.expander(f"{passage['speaker']}: {passage['text'][:80]}…"):
            st.write(passage["text"])
            for code in codebook:
                probability = results[index][code["code"]]
                mark = "✅" if probability >= apply_at else ("⚠️" if probability >= review_at else "·")
                st.caption(f"{mark} {probability:.2f} — {code['code']}: {code['name']}")

    cost, tokens, calls = totals
    st.divider()
    st.caption(
        f"{len(coded_passages) * len(codebook)} judgments over {calls} request(s), "
        f"last request {elapsed:.1f}s, ${cost:.6f} total ({tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
