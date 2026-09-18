"""Survey Coder -- code free-text survey answers against a fixed codebook.

The real market-research workflow: 3,000 people answered "why did you cancel?",
and a human coder reads every one and ticks codes from a codebook agreed with
the client. It takes days, the codebook must be applied consistently, and
several codes routinely apply to one answer.

Two ideas worth stealing:

1. ONE NOUL PER (ANSWER, CODE) PAIR. "It got too expensive for something I only
   opened twice a month" is BOTH `price` AND `low_usage`. Multi-code answers are
   the normal case in survey coding, not an edge case, so the codebook is not a
   Choice and it is not levels on a Score -- it is ten independent yes/no
   questions per answer, each with its own probability and its own threshold.

2. THRESHOLDS LIVE IN THE SIDEBAR, NOT IN THE PROMPT. The model returns a
   probability per (answer, code). Where you cut is a research decision that
   changes between studies and between clients. Moving the slider re-derives the
   frequency table, the co-occurrence matrix and the human queue without one new
   request -- inference happens once, tuning is free.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_RESPONSES = HERE / "sample_responses.csv"
SAMPLE_CODEBOOK = HERE / "sample_codebook.json"

CHARS_PER_TOKEN = 4


def load_codebook(path: Path | None = None) -> list[dict]:
    payload = json.loads((path or SAMPLE_CODEBOOK).read_text(encoding="utf-8"))
    return list(payload["codes"] if isinstance(payload, dict) else payload)


def build_questions(responses: list[str], codes: list[dict]) -> dict:
    """One Noul per (answer, code). N answers x M codes, all in one ask()."""
    questions: dict[str, dict] = {}
    for index in range(len(responses)):
        for code in codes:
            questions[f"r{index}__{code['id']}"] = noul(
                {
                    "task": "Does this survey answer give the reason described below?",
                    "survey_question": "What is the main reason you cancelled your subscription?",
                    "answer": f"`responses[{index}].text`",
                    "reason": {"label": code["label"], "means": code["description"]},
                },
                true=f"The answer gives this reason, stated outright or clearly implied: {code['description']}",
                false=(
                    "The answer gives some other reason, or is blank, unreadable, or content-free "
                    "('n/a', 'idk'). Several reasons may apply to one answer -- judge only this one, "
                    "and do not withhold it because another reason also fits."
                ),
            )
    return questions


def question_tokens_per_answer(codes: list[dict]) -> int:
    """Measure the real per-answer question payload instead of guessing at it."""
    probe = build_questions(["x"], codes)
    return len(json.dumps(probe, default=str)) // CHARS_PER_TOKEN


def batch_responses(
    responses: list[str], codes: list[dict], context_tokens: int, headroom: float = 0.4
) -> list[list[int]]:
    """Pack answer indices into requests sized against the provider's context window.

    The questions dominate: ten Nouls per answer is roughly 700 tokens of
    criteria against maybe 40 tokens of answer text. Both are counted.
    TypeSafe serves 64k and OpenRouter 32k, so this reads provider.context_tokens.
    """
    per_answer_questions = question_tokens_per_answer(codes)
    budget = max(int(context_tokens * headroom), per_answer_questions * 2)
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, text in enumerate(responses):
        cost = per_answer_questions + len(str(text)) // CHARS_PER_TOKEN
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        batches.append(current)
    return batches


def decide(probability: float, apply_at: float, band: float) -> str:
    """Three states, in Python, over an already-returned probability.

    A code inside the uncertain band is NOT applied and NOT rejected -- it is a
    question for a human. Widening the band moves codes out of the frequency
    table and into the queue; it never re-runs inference.
    """
    if probability >= apply_at + band:
        return "applied"
    if probability <= apply_at - band:
        return "not_applied"
    return "review"


def coded_frame(long: pd.DataFrame, apply_at: float, band: float) -> pd.DataFrame:
    """Add the decision column. Pure pandas over the stored probabilities."""
    out = long.copy()
    out["decision"] = [decide(p, apply_at, band) for p in out["probability"]]
    return out


def main() -> None:
    st.set_page_config(page_title="Survey Coder", page_icon="📋", layout="wide")
    st.title("📋 Survey Coder")
    st.caption("Apply a fixed codebook to thousands of open-ends. Several codes per answer, by design.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(
            f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token context",
            language=None,
        )
        st.divider()
        st.subheader("Coding policy")
        apply_at = st.slider("Apply a code at probability above", 0.05, 0.95, 0.50, 0.05)
        band = st.slider(
            "Uncertain band (±)", 0.0, 0.3, 0.10, 0.01,
            help="Codes landing this close to the threshold are neither applied nor dropped — "
                 "the answer goes to a human coder.",
        )
        st.caption(
            "Both thresholds are applied in Python to probabilities already returned. "
            "Move them freely: nothing below re-runs inference."
        )

    left, right = st.columns([3, 2])
    uploaded = left.file_uploader("Responses CSV (needs a `response` column)", type=["csv"])
    codebook_upload = right.file_uploader("Codebook JSON", type=["json"])

    if uploaded is not None:
        frame = pd.read_csv(io.BytesIO(uploaded.read()))
    elif SAMPLE_RESPONSES.is_file():
        frame = pd.read_csv(SAMPLE_RESPONSES)
    else:
        st.warning("Upload a CSV with a `response` column.")
        return
    if "response" not in frame.columns:
        st.error("CSV needs a `response` column.")
        return

    try:
        codes = (
            list(json.loads(codebook_upload.getvalue().decode("utf-8"))["codes"])
            if codebook_upload is not None
            else load_codebook()
        )
    except (KeyError, ValueError) as exc:
        st.error(f"Codebook JSON needs a `codes` list of {{id, label, description}}: {exc}")
        return

    if uploaded is None and codebook_upload is None:
        st.caption(
            f"Using the bundled sample: {len(frame)} cancellation open-ends "
            f"against a {len(codes)}-code codebook."
        )

    left.dataframe(frame.head(8), width="stretch", hide_index=True)
    right.dataframe(pd.DataFrame(codes), width="stretch", hide_index=True, height=300)

    responses = [str(text) for text in frame["response"].tolist()]
    labels = [str(row) for row in frame.get("response_id", pd.Series(range(len(frame)))).tolist()]
    batches = batch_responses(responses, codes, provider.context_tokens)

    st.caption(
        f"{len(responses)} answers x {len(codes)} codes = {len(responses) * len(codes)} Nouls, "
        f"packed into {len(batches)} request(s) against a {provider.context_tokens:,} token window."
    )
    if not st.button(f"Code {len(responses)} answers", type="primary"):
        return

    rows: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_seconds = 0.0
    question_count = 0
    progress = st.progress(0.0, text="Coding...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, indices in enumerate(batches):
                slice_texts = [responses[i] for i in indices]
                state = {
                    "responses": [
                        {"id": labels[i], "text": responses[i]} for i in indices
                    ]
                }
                questions = build_questions(slice_texts, codes)
                answers = client.ask(state, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_seconds += answers.elapsed_s
                question_count += len(questions)
                for local, original in enumerate(indices):
                    for code in codes:
                        rows.append(
                            {
                                "response_id": labels[original],
                                "response": responses[original],
                                "code": code["id"],
                                "probability": answers.noul(f"r{local}__{code['id']}"),
                            }
                        )
                progress.progress((batch_index + 1) / len(batches), text="Coding...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    # ── Everything below is pandas over stored probabilities. No model arithmetic. ──
    long = coded_frame(pd.DataFrame(rows), apply_at, band)
    applied = long[long["decision"] == "applied"]
    review = long[long["decision"] == "review"]

    wide = long.pivot(index="response_id", columns="code", values="probability")
    applied_matrix = (
        long.assign(hit=(long["decision"] == "applied").astype(int))
        .pivot(index="response_id", columns="code", values="hit")
        .reindex(columns=[code["id"] for code in codes], fill_value=0)
    )
    codes_per_answer = applied_matrix.sum(axis=1)
    uncoded_ids = codes_per_answer[codes_per_answer == 0].index.tolist()
    queue_ids = sorted(set(review["response_id"]))

    columns = st.columns(5)
    columns[0].metric("Answers", len(responses))
    columns[1].metric("Codes applied", int(applied_matrix.to_numpy().sum()))
    columns[2].metric("Multi-code answers", int((codes_per_answer > 1).sum()))
    columns[3].metric("To a human coder", len(queue_ids))
    columns[4].metric("Cost", f"${total_cost:.6f}")

    label_of = {code["id"]: code["label"] for code in codes}

    left, right = st.columns(2)
    with left:
        st.subheader("Code frequency")
        st.caption("Counted by pandas from the applied flags. The model was never shown a total.")
        frequency = (
            pd.DataFrame(
                {
                    "code": applied_matrix.columns,
                    "label": [label_of[c] for c in applied_matrix.columns],
                    "answers": applied_matrix.sum(axis=0).to_numpy(),
                }
            )
            .assign(pct=lambda d: (d["answers"] / len(responses) * 100).round(1))
            .sort_values("answers", ascending=False)
        )
        st.bar_chart(frequency.set_index("code")["answers"])
        st.dataframe(frequency, width="stretch", hide_index=True)

    with right:
        st.subheader("Co-occurrence")
        st.caption("Which codes get ticked together. This is the pair structure a Choice would have destroyed.")
        matrix = applied_matrix.T.dot(applied_matrix)
        pairs = [
            {"code_a": a, "code_b": b, "answers": int(matrix.loc[a, b])}
            for position, a in enumerate(matrix.columns)
            for b in matrix.columns[position + 1 :]
            if matrix.loc[a, b]
        ]
        if pairs:
            st.dataframe(
                pd.DataFrame(pairs).sort_values("answers", ascending=False),
                width="stretch", hide_index=True,
            )
        else:
            st.caption("No answer received two codes at this threshold.")
        st.dataframe(matrix, width="stretch")

    st.subheader(f"🕳️ Uncoded answers ({len(uncoded_ids)})")
    st.caption(
        "No code cleared the threshold. Some of these are genuinely content-free ('n/a', 'idk'). "
        "The rest are the signal that the codebook has a gap — read them before the next wave."
    )
    st.dataframe(
        long[long["response_id"].isin(uncoded_ids)][["response_id", "response"]].drop_duplicates(),
        width="stretch", hide_index=True,
    )

    st.subheader(f"⚠️ Human coder queue ({len(queue_ids)})")
    st.caption(f"At least one code landed within ±{band:.2f} of the {apply_at:.2f} threshold.")
    st.dataframe(
        review[["response_id", "response", "code", "probability"]]
        .assign(probability=lambda d: d["probability"].round(3))
        .sort_values("probability", ascending=False),
        width="stretch", hide_index=True,
    )

    export = wide.round(4).reset_index()
    export.columns = ["response_id"] + [f"p_{column}" for column in wide.columns]
    export = export.merge(
        long[["response_id", "response"]].drop_duplicates(), on="response_id", how="left"
    )
    st.download_button(
        "Download raw probabilities (re-threshold offline)",
        export.to_csv(index=False).encode("utf-8"),
        file_name="coded_responses.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{question_count} Nouls over {len(batches)} request(s) · {total_tokens:,} input tokens · "
        f"{total_seconds:.1f}s · ${total_cost:.6f} "
        f"(${total_cost / max(len(responses), 1):.6f} per answer). "
        "Re-thresholding costs nothing: the probabilities above are already stored."
    )


if __name__ == "__main__":
    main()
