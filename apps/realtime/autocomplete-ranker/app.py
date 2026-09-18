"""autocomplete-ranker -- rank completion candidates by what the user means.

The entire candidate set is one Choice, so the whole list is ranked in ONE
request rather than scored one candidate at a time. A companion Noul asks
whether any suggestion is worth showing at all -- the question that stops an
autocomplete from interrupting someone who already knows what they are typing.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
CONTEXTS = json.loads((HERE / "sample_contexts.json").read_text(encoding="utf-8"))
NO_MATCH = "none"


def build_questions(context: dict) -> dict:
    """ONE request for the whole candidate set."""
    options = {c["id"]: c["text"] for c in context["candidates"]}
    options[NO_MATCH] = "None of these is what the user is heading towards."

    return {
        "intended": choice(
            {
                "task": "Which candidate is the user actually heading towards?",
                "surface": context["surface"],
                "situation": "`situation`",
                "typed_so_far": "`typed`",
                "note": f"Choose '{NO_MATCH}' if none of them is where this is going.",
            },
            options,
        ),
        # A Choice must nominate a winner even when the list is useless. This is
        # the separate question of whether to show the popup at all.
        "worth_showing": noul(
            {
                "task": "Would any of these candidates actually save the user typing?",
                "typed_so_far": "`typed`",
                "situation": "`situation`",
            },
            true="At least one candidate is plainly where this input is going",
            false="The user is typing something none of these covers, and a popup would be in the way",
        ),
    }


def rank(probabilities: dict[str, float], candidates: list[dict], semantic_weight: float) -> list[tuple[str, float]]:
    """Blend semantic probability with the frequency prior you already have.

    Pure policy: the popularity column is your existing ranker. Sliding the
    weight to 0 gives you back exactly that ranker, which is the honest baseline
    to compare against.
    """
    popularity = {c["id"]: float(c["popularity"]) for c in candidates}
    blended = {
        cid: semantic_weight * probabilities.get(cid, 0.0)
        + (1 - semantic_weight) * popularity.get(cid, 0.0)
        for cid in popularity
    }
    return sorted(blended.items(), key=lambda kv: (-kv[1], kv[0]))


def main() -> None:
    st.set_page_config(page_title="Autocomplete Ranker", page_icon="⚡", layout="wide")
    st.title("⚡ Autocomplete Ranker")
    st.caption("The whole candidate list ranked in **one** request, inside the render loop.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        semantic_weight = st.slider("Weight on meaning vs. popularity", 0.0, 1.0, 0.7, 0.05)
        show_at = st.slider("P(worth showing) below which the popup stays closed", 0.0, 1.0, 0.5, 0.05)
        st.caption("Both are Python policy — re-ranking runs no new inference.")

    labels = {c["id"]: f"{c['surface']} · \"{c['typed']}\"" for c in CONTEXTS}
    picked = st.selectbox("Scenario", [c["id"] for c in CONTEXTS], format_func=lambda cid: labels[cid])
    context = next(c for c in CONTEXTS if c["id"] == picked)

    st.code(f"situation: {context['context']}\ntyped:     {context['typed']}|", language=None)

    if not st.button("Rank the candidates", type="primary"):
        st.info(f"{len(context['candidates'])} candidates. One request ranks all of them.")
        return

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(
                {"situation": context["context"], "typed": context["typed"],
                 "candidates": [c["text"] for c in context["candidates"]]},
                build_questions(context),
            )
    except JevError as exc:
        st.error(str(exc))
        return

    probabilities = {k: v for k, v in answers.probabilities("intended").items() if k != NO_MATCH}
    worth_showing = answers.noul("worth_showing")
    ranked = rank(probabilities, context["candidates"], semantic_weight)
    texts = {c["id"]: c["text"] for c in context["candidates"]}
    popularity = {c["id"]: c["popularity"] for c in context["candidates"]}

    if worth_showing < show_at or answers.choice("intended") == NO_MATCH:
        st.warning(
            f"Popup stays closed — nothing here is where the user is going "
            f"(P = {worth_showing:.2f})."
        )
    else:
        st.success(f"Show the popup (P = {worth_showing:.2f}) — top suggestion: **{texts[ranked[0][0]]}**")

    baseline = rank(probabilities, context["candidates"], 0.0)
    st.dataframe(
        [
            {
                "rank": position,
                "candidate": texts[cid],
                "blended": round(blend, 3),
                "P(intended)": round(probabilities.get(cid, 0.0), 3),
                "popularity": popularity[cid],
                "popularity-only rank": [c for c, _ in baseline].index(cid) + 1,
            }
            for position, (cid, blend) in enumerate(ranked, start=1)
        ],
        width="stretch",
    )

    history = st.session_state.setdefault("latencies", [])
    history.append(answers.elapsed_s * 1000)
    st.divider()
    footer = st.columns(5)
    footer[0].metric("Candidates ranked", len(context["candidates"]))
    footer[1].metric("Requests", 1)
    footer[2].metric("Latency", f"{answers.elapsed_s * 1000:.0f} ms")
    footer[3].metric("Median this session", f"{statistics.median(history):.0f} ms")
    footer[4].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{answers.input_tokens:,} input tokens, 2 questions, measured. "
        f"Scoring these {len(context['candidates'])} candidates one at a time would be "
        f"{len(context['candidates'])} round trips — and a popup that arrives after the next keystroke."
    )


if __name__ == "__main__":
    main()
