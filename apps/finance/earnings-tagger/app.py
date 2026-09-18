"""Earnings Tagger -- tag transcript passages for guidance changes, hedging and tone.

An earnings call has two halves and they are written by different people. The
prepared remarks are drafted, reviewed and lawyered. The Q&A is not. The
interesting signal is rarely inside either half -- it is the DIFFERENCE between
them, and the difference between this speaker and that one.

Which is why the split here is unusual:

  Jev      tags each passage independently: does this change guidance, is it
           hedged, is the question being answered, how firmly is it committed to.
  pandas   computes every shift. The hedging delta between prepared remarks and
           Q&A, per speaker, is a subtraction -- and jev-1.13 cannot subtract, so
           it is never asked to compare two passages, two sections, or two
           quarters.

"Tone shift" sounds like a judgment. It is a difference of two measured rates.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_transcript.json"

NO_GUIDANCE = "no_forward_guidance_in_this_passage"

GUIDANCE_MOVES = {
    "raised": "The passage sets a forward expectation above one the company gave before.",
    "lowered": "The passage sets a forward expectation below one the company gave before.",
    "reaffirmed": "The passage restates a forward expectation the company already gave, unchanged.",
    "range_widened": "The passage keeps a forward expectation but presents it as a wider or looser range than before.",
    "withdrawn": "The passage removes a forward expectation the company previously gave, or stops disclosing that measure.",
    "introduced": "The passage gives a forward expectation for something the company had not guided before.",
    "declined_to_guide": "The speaker is asked for a forward expectation and explicitly declines to give one today.",
    NO_GUIDANCE: "The passage is about results, history or commentary, and sets no forward expectation at all.",
}

# Three recognisable stances a listener would act on differently.
COMMITMENT_LEVELS = [
    "The speaker distances themselves from what is being said, attributing it to others, to timing, or to conditions outside their control.",
    "The speaker states a position but leaves room to move: a direction of travel, a characterisation, something they will revisit.",
    "The speaker puts their own credibility behind a specific claim and invites being held to it.",
]


def build_questions(passages: list[dict]) -> dict:
    """Four independent tags per passage, in one request.

    Hedged and guidance-changing are not opposites and not points on a scale -- a
    raised outlook can be delivered flatly or wrapped in qualifiers, and the
    second is the tradeable observation. So: one Choice, two Nouls, one Score.
    """
    questions: dict[str, dict] = {}
    for index in range(len(passages)):
        reference = {
            "speaker": f"`passages[{index}].speaker`",
            "role": f"`passages[{index}].role`",
            "part_of_call": f"`passages[{index}].section`",
            "passage": f"`passages[{index}].text`",
        }
        questions[f"guidance_{index}"] = choice(
            {
                "task": "What does this passage do to the company's forward guidance?",
                "spoken": reference,
            },
            GUIDANCE_MOVES,
        )
        questions[f"hedging_{index}"] = noul(
            {
                "task": "Is this passage wrapped in qualifying language that softens what is being claimed?",
                "spoken": reference,
            },
            true="Qualifiers, conditionals, vagueness or caveats that leave the speaker room to be right either way.",
            false="Direct statement with the claim left exposed and unqualified.",
        )
        questions[f"deflection_{index}"] = noul(
            {
                "task": "Is the speaker avoiding giving the substance that was asked for?",
                "spoken": reference,
            },
            true="The substance is deferred, redirected to a later date or an offline conversation, or answered with something adjacent to what was asked.",
            false="The speaker gives the substance directly, including when the direct answer is a refusal with a stated reason.",
        )
        questions[f"commitment_{index}"] = score(
            {
                "task": "How much of their own credibility is the speaker putting behind this passage?",
                "spoken": reference,
            },
            COMMITMENT_LEVELS,
        )
    return questions


GUIDANCE_MOVED = {"raised", "lowered", "range_widened", "withdrawn", "introduced"}
SOFTENING = {"lowered", "range_widened", "withdrawn", "declined_to_guide"}


def notable(move: str, hedging: float, deflection: float, hedge_gate: float, deflect_gate: float) -> str:
    """What a reader should look at first. Pure policy, in Python.

    The pairing that matters is a softening of guidance delivered with hedging --
    each is ordinary alone and the combination is the thing analysts circle.
    """
    if move in SOFTENING and hedging >= hedge_gate:
        return "softened_and_hedged"
    if move in GUIDANCE_MOVED:
        return "guidance_moved"
    if deflection >= deflect_gate and hedging >= hedge_gate:
        return "hedged_non_answer"
    if deflection >= deflect_gate:
        return "non_answer"
    return ""


def section_shift(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """The 'tone shift': a per-speaker difference between two measured rates.

    This is arithmetic, so it happens here. The model tagged 22 passages in
    isolation and was never shown two of them side by side.
    """
    pivot = frame.pivot_table(index="speaker", columns="section", values=column, aggfunc="mean")
    for section in ("prepared_remarks", "qa"):
        if section not in pivot.columns:
            pivot[section] = float("nan")
    pivot["shift"] = pivot["qa"] - pivot["prepared_remarks"]
    return pivot.sort_values("shift", ascending=False).round(3)


def main() -> None:
    st.set_page_config(page_title="Earnings Tagger", page_icon="🎙️", layout="wide")
    st.title("🎙️ Earnings Tagger")
    st.caption("Guidance changes, hedging and tone shifts across an earnings call transcript.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Gates")
        hedge_gate = st.slider("Call it hedged above P=", 0.0, 1.0, 0.6, 0.05)
        deflect_gate = st.slider("Call it a non-answer above P=", 0.0, 1.0, 0.6, 0.05)
        guidance_confidence = st.slider("Trust the guidance label above confidence", 0.0, 1.0, 0.6, 0.05)

    uploaded = st.file_uploader("Transcript JSON", type=["json"])
    transcript = (
        json.loads(uploaded.read().decode("utf-8"))
        if uploaded
        else json.loads(SAMPLE.read_text(encoding="utf-8"))
    )
    passages = transcript["passages"]
    st.subheader(f"{transcript.get('company', 'Transcript')} — {transcript.get('period', '')}")
    if uploaded is None:
        st.caption(f"Bundled sample: {len(passages)} passages across prepared remarks and Q&A.")
    st.dataframe(pd.DataFrame(passages).head(5), width="stretch", hide_index=True)

    if not st.button(f"Tag {len(passages)} passages", type="primary"):
        return

    questions = build_questions(passages)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"passages": passages}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, passage in enumerate(passages):
        move = answers.choice(f"guidance_{index}")
        confidence = answers.confidence(f"guidance_{index}")
        hedging = answers.noul(f"hedging_{index}")
        deflection = answers.noul(f"deflection_{index}")
        rows.append(
            {
                "passage_id": passage["passage_id"],
                "section": passage["section"],
                "speaker": passage["speaker"],
                "role": passage.get("role", ""),
                "guidance_move": move,
                "guidance_confidence": confidence,
                "trusted": confidence >= guidance_confidence,
                "hedging": hedging,
                "deflection": deflection,
                "commitment": answers.score(f"commitment_{index}"),
                "flag": notable(move, hedging, deflection, hedge_gate, deflect_gate),
                "text": passage["text"],
            }
        )

    out = pd.DataFrame(rows)
    moved = out[(out["guidance_move"].isin(GUIDANCE_MOVED)) & out["trusted"]]

    columns = st.columns(4)
    columns[0].metric("Passages", len(out))
    columns[1].metric("Guidance moves", len(moved))
    columns[2].metric("Hedged", int((out["hedging"] >= hedge_gate).sum()))
    columns[3].metric("Non-answers", int((out["deflection"] >= deflect_gate).sum()))

    st.subheader("Guidance movements")
    st.caption("Low-confidence labels are excluded here and listed further down rather than silently believed.")
    st.dataframe(
        moved[["passage_id", "section", "speaker", "guidance_move", "guidance_confidence", "hedging", "text"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Tone shift: prepared remarks versus Q&A")
    st.caption(
        "Every number in these two tables is a subtraction pandas performed over "
        "independently tagged passages. The model never saw two passages side by "
        "side and was never asked to compare anything — it cannot."
    )
    left, right = st.columns(2)
    left.markdown("**Hedging**")
    left.dataframe(section_shift(out, "hedging"), width="stretch")
    right.markdown("**Commitment**")
    right.dataframe(section_shift(out, "commitment"), width="stretch")

    st.subheader("Flagged passages")
    flagged = out[out["flag"] != ""]
    st.dataframe(
        flagged[["passage_id", "section", "speaker", "flag", "guidance_move", "hedging", "deflection", "text"]]
        .sort_values("flag"),
        width="stretch", hide_index=True,
    )

    st.subheader("Guidance labels the model was unsure about")
    st.caption(
        "A Choice always returns a label. These are the passages where the distribution "
        "was flat — which, on a transcript, usually means the passage is genuinely "
        "ambiguous about whether it constitutes guidance at all."
    )
    st.dataframe(
        out[~out["trusted"]][["passage_id", "speaker", "guidance_move", "guidance_confidence", "text"]]
        .sort_values("guidance_confidence"),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download tagged transcript CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="tagged_transcript.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {len(questions)} questions over {len(passages)} passages, "
        f"{answers.input_tokens:,} input tokens, ${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s."
    )


if __name__ == "__main__":
    main()
