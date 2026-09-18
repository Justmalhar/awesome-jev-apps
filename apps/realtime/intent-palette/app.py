"""intent-palette -- a command palette you type intent into, not command names.

Every keystroke is one Jev request: the whole action catalog is ranked in a
single Choice, and a companion Noul asks whether what has been typed so far is
even a complete intent yet. Both come back inside a keystroke, which is the
only reason this can sit in the input path at all.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
ACTIONS = json.loads((HERE / "sample_actions.json").read_text(encoding="utf-8"))
NO_MATCH = "none"
MIN_CHARS = 3
MAX_EVENTS = 14


def should_fire(text: str, last_fired: str | None, min_chars: int = MIN_CHARS) -> bool:
    """Debounce policy, in Python: fire only on a changed prefix of real length.

    This is the whole debounce -- there is no timer, because the round trip is
    shorter than the gap between two keystrokes of an average typist.
    """
    stripped = text.strip()
    return len(stripped) >= min_chars and stripped != (last_fired or "")


def keystroke_prefixes(text: str, min_chars: int = MIN_CHARS, max_events: int = MAX_EVENTS) -> list[str]:
    """The prefixes a replay should fire, one per simulated keystroke.

    Long queries are sampled evenly rather than truncated so the replay always
    ends on the full string -- a demo that stops mid-word teaches nothing.
    """
    stripped = text.strip()
    if len(stripped) < min_chars:
        return []
    prefixes = [stripped[:i] for i in range(min_chars, len(stripped) + 1)]
    if len(prefixes) <= max_events:
        return prefixes
    step = len(prefixes) / max_events
    sampled = [prefixes[int(i * step)] for i in range(max_events)]
    if sampled[-1] != prefixes[-1]:
        sampled[-1] = prefixes[-1]
    return sampled


def build_questions(typed: str) -> dict:
    """ONE request per keystroke: rank the catalog, and ask if it is ready yet."""
    options = {action["id"]: f"{action['label']} - {action['description']}" for action in ACTIONS}
    options[NO_MATCH] = "Nothing in this catalog does what the typed text asks for."

    return {
        "action": choice(
            {
                "task": "Select the action the user is trying to reach.",
                "typed_text": typed,
                "note": f"Choose '{NO_MATCH}' when no action in the catalog does this.",
            },
            options,
        ),
        # A Choice must nominate something even for "h". This is what makes the
        # palette stay quiet until the intent has actually arrived.
        "settled": noul(
            {
                "task": "Is the typed text already specific enough to name one intended action?",
                "typed_text": typed,
            },
            true="A reader of this text could name the single action the user wants",
            false="Still a fragment or an ambiguous opening that could lead to several different actions",
        ),
    }


def decide(settled: float, winner: str, top_probability: float, settle_at: float, show_at: float) -> str:
    """Policy lives here, so tuning the sliders re-runs no inference."""
    if settled < settle_at:
        return "keep_typing"
    if winner == NO_MATCH or top_probability < show_at:
        return "no_match"
    return "show"


def run_keystroke(client: JevClient, typed: str, settle_at: float, show_at: float) -> dict:
    answers = client.ask({"typed_text": typed}, build_questions(typed))
    probabilities = {k: v for k, v in answers.probabilities("action").items() if k != NO_MATCH}
    ranked = sorted(probabilities.items(), key=lambda kv: -kv[1])
    winner = answers.choice("action")
    settled = answers.noul("settled")
    top_probability = ranked[0][1] if ranked else 0.0
    return {
        "typed": typed,
        "ranked": ranked,
        "winner": winner,
        "settled": settled,
        "verdict": decide(settled, winner, top_probability, settle_at, show_at),
        "ms": answers.elapsed_s * 1000,
        "tokens": answers.input_tokens,
        "cost": answers.cost_usd,
    }


def main() -> None:
    st.set_page_config(page_title="Intent Palette", page_icon="⌨️", layout="wide")
    st.title("⌨️ Intent Palette")
    st.caption(
        f"Type what you want. The whole {len(ACTIONS)}-action catalog is ranked on "
        "**every keystroke**, in one request each."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    labels = {action["id"]: action["label"] for action in ACTIONS}

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        settle_at = st.slider("P(intent is settled) to show anything", 0.0, 1.0, 0.55, 0.05)
        show_at = st.slider("Top-action probability to commit", 0.0, 1.0, 0.25, 0.05)
        st.caption("Thresholds are Python policy; moving them re-runs no inference.")

    typed = st.text_input(
        "Command palette",
        value="get this in front of the legal team without letting them change it",
        help="Submit to fire one keystroke event; Replay fires the whole typing sequence.",
    )
    columns = st.columns(2)
    fire_one = columns[0].button("Fire this keystroke", type="primary")
    replay = columns[1].button("Replay the whole typing sequence")

    if not (fire_one or replay):
        st.info("Hit **Replay** to watch the palette converge one character at a time.")
        return
    if not should_fire(typed, None):
        st.warning(f"Type at least {MIN_CHARS} characters.")
        return

    sequence = keystroke_prefixes(typed) if replay else [typed.strip()]
    rows: list[dict] = []
    table = st.empty()
    live = st.empty()

    try:
        with JevClient(provider=provider) as client:
            for prefix in sequence:
                event = run_keystroke(client, prefix, settle_at, show_at)
                rows.append(event)
                table.dataframe(
                    [
                        {
                            "keystroke": f"{r['typed']}|",
                            "top action": labels.get(r["ranked"][0][0], "-") if r["ranked"] else "-",
                            "P": round(r["ranked"][0][1], 3) if r["ranked"] else 0.0,
                            "settled": round(r["settled"], 3),
                            "verdict": r["verdict"],
                            "ms": round(r["ms"]),
                        }
                        for r in rows
                    ],
                    width="stretch",
                )
                live.metric("Last keystroke", f"{event['ms']:.0f} ms", delta=event["verdict"])
    except JevError as exc:
        st.error(str(exc))
        return

    final = rows[-1]
    with st.container(border=True):
        if final["verdict"] == "show":
            st.success(f"→ **{labels[final['ranked'][0][0]]}**  `{final['ranked'][0][0]}`")
            for action_id, probability in final["ranked"][1:4]:
                st.caption(f"also considered: {labels[action_id]} · P = {probability:.3f}")
        elif final["verdict"] == "no_match":
            st.warning("No action in this catalog does that.")
        else:
            st.info(f"Still typing (settled P = {final['settled']:.2f}) — palette stays quiet.")

    latencies = [r["ms"] for r in rows]
    st.divider()
    footer = st.columns(5)
    footer[0].metric("Keystrokes decided", len(rows))
    footer[1].metric("Median latency", f"{statistics.median(latencies):.0f} ms")
    footer[2].metric("Slowest", f"{max(latencies):.0f} ms")
    footer[3].metric("Tokens", f"{sum(r['tokens'] for r in rows):,}")
    footer[4].metric("Cost", f"${sum(r['cost'] for r in rows):.6f}")
    st.caption(
        f"{len(rows)} requests, 2 questions each, measured end to end. "
        f"Human keystroke-to-keystroke at 60 wpm is ~200 ms — anything under that "
        f"is free in the input path."
    )


if __name__ == "__main__":
    main()
