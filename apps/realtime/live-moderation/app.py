"""live-moderation -- judge a live chat stream one message at a time.

Each message is ONE request carrying four independent Nouls and one severity
Score. The decision lands in roughly the time it takes the message to render,
which is what lets moderation sit in the send path instead of in a queue that
a human drains twenty minutes later.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
STREAM = json.loads((HERE / "sample_stream.json").read_text(encoding="utf-8"))

SEVERITY_LEVELS = [
    "Ordinary participation: a question, an answer, or a neutral remark nobody would report",
    "Mildly disruptive: off-topic chatter, blunt criticism of the product, or low-effort noise",
    "Directed at a person: dismissive or belittling language aimed at another participant by name",
    "Sustained hostility or commercial spam that a moderator would remove on sight",
]


def build_questions(message: dict, topic: str) -> dict:
    """ONE request per message. Four independent properties, four Nouls.

    These can all be true at once -- spam that is also a personal attack -- so
    they are not one Choice and not levels on a rubric. Separate Nouls are what
    lets each one carry its own threshold.
    """
    return {
        "attacks_person": noul(
            {
                "task": "Is this message directed at another participant in a demeaning way?",
                "message": "`message.text`",
            },
            true="Belittles, insults, or tells off another named participant",
            false="Criticises the product, the company, or nobody in particular",
        ),
        "is_promotion": noul(
            {
                "task": "Is this message promoting something unrelated to the channel?",
                "message": "`message.text`",
            },
            true="Pushes a link, newsletter, service, or offer of the author's own",
            false="Links or references that serve the conversation, such as docs or a release note",
        ),
        "off_topic": noul(
            {
                "task": "Is this message unrelated to the channel topic?",
                "channel_topic": topic,
                "message": "`message.text`",
            },
            true="About something else entirely, or content-free keyboard mashing",
            false="About the product, the release, pricing, migration, or another participant's question about them",
        ),
        "needs_human": noul(
            {
                "task": "Does this message report a real problem a human should pick up now?",
                "message": "`message.text`",
            },
            true="Reports users blocked, broken access, or an outage affecting the author's work",
            false="An ordinary question, opinion, or complaint with nothing currently broken",
        ),
        "severity": score(
            {
                "task": "Place this message on the moderation severity rubric.",
                "channel_topic": topic,
                "message": "`message.text`",
            },
            SEVERITY_LEVELS,
        ),
    }


def decide(signals: dict, remove_at: float, hold_at: float) -> str:
    """Policy in Python: tuning these thresholds re-runs no inference."""
    if signals["is_promotion"] >= remove_at or signals["attacks_person"] >= remove_at:
        return "remove"
    if signals["severity"] >= 2.0 or signals["attacks_person"] >= hold_at:
        return "hold"
    if signals["is_promotion"] >= hold_at or signals["off_topic"] >= hold_at:
        return "hold"
    return "allow"


VERDICT_STYLE = {"remove": "🔴", "hold": "🟠", "allow": "🟢"}


def main() -> None:
    st.set_page_config(page_title="Live Moderation", page_icon="🛡️", layout="wide")
    st.title("🛡️ Live Moderation")
    st.caption(
        "Every message judged as it arrives — five questions, one request, "
        "latency measured per message."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        remove_at = st.slider("Auto-remove above", 0.0, 1.0, 0.8, 0.05)
        hold_at = st.slider("Hold for review above", 0.0, 1.0, 0.5, 0.05)
        escalate_at = st.slider("Ping a human above", 0.0, 1.0, 0.6, 0.05)
        st.caption(f"Channel: `{STREAM['channel']}` · {len(STREAM['messages'])} messages bundled.")

    st.text_area("Channel topic (state sent with every message)", STREAM["topic"], height=68, disabled=True)

    if not st.button("Start the stream", type="primary"):
        st.info("Press **Start the stream** to replay the bundled chat message by message.")
        return

    feed = st.container()
    rows: list[dict] = []
    ticker = st.empty()

    try:
        with JevClient(provider=provider) as client:
            for message in STREAM["messages"]:
                answers = client.ask(
                    {"channel_topic": STREAM["topic"], "message": message},
                    build_questions(message, STREAM["topic"]),
                )
                signals = {
                    key: answers.noul(key)
                    for key in ("attacks_person", "is_promotion", "off_topic", "needs_human")
                }
                signals["severity"] = answers.score("severity")
                verdict = decide(signals, remove_at, hold_at)
                rows.append(
                    {
                        **message,
                        **signals,
                        "verdict": verdict,
                        "ms": answers.elapsed_s * 1000,
                        "tokens": answers.input_tokens,
                        "cost": answers.cost_usd,
                    }
                )
                ticker.metric("Last decision", f"{answers.elapsed_s * 1000:.0f} ms", delta=verdict)

                with feed.container(border=True):
                    header = st.columns([6, 2])
                    header[0].markdown(
                        f"{VERDICT_STYLE[verdict]} **{message['author']}** · `{verdict}` "
                        f"· {answers.elapsed_s * 1000:.0f} ms"
                    )
                    if signals["needs_human"] >= escalate_at:
                        header[1].warning("ping a human")
                    st.write(message["text"])
                    st.caption(
                        f"attack {signals['attacks_person']:.2f} · promo {signals['is_promotion']:.2f} "
                        f"· off-topic {signals['off_topic']:.2f} · human {signals['needs_human']:.2f} "
                        f"· severity {signals['severity']:.2f}"
                    )
    except JevError as exc:
        st.error(str(exc))
        return

    latencies = [row["ms"] for row in rows]
    st.divider()
    footer = st.columns(6)
    footer[0].metric("Messages", len(rows))
    footer[1].metric("Requests", len(rows))
    footer[2].metric("Questions", len(rows) * 5)
    footer[3].metric("Median latency", f"{statistics.median(latencies):.0f} ms")
    footer[4].metric("Slowest", f"{max(latencies):.0f} ms")
    footer[5].metric("Cost", f"${sum(row['cost'] for row in rows):.6f}")
    st.caption(
        f"{sum(row['tokens'] for row in rows):,} input tokens, measured. "
        f"Removed {sum(1 for r in rows if r['verdict'] == 'remove')}, "
        f"held {sum(1 for r in rows if r['verdict'] == 'hold')}."
    )


if __name__ == "__main__":
    main()
