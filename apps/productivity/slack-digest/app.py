"""Slack Digest -- what happened across your channels that actually concerns you.

This is a FILTER, not a summary. Jev cannot generate, so nothing here is
rewritten: every line you read is the message somebody actually posted. The
app's only job is deciding which ones you would have wanted to see.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_slack.json"

DEFAULT_HANDLE = "@maya"
DEFAULT_SCOPE = (
    "I own the checkout service and the payments integration, and I am the "
    "release manager for the mobile app this month. I care about anything that "
    "breaks payments, anything that changes the release process, and hiring for "
    "my own team. I do not need design critique, other teams' standups, or social chatter."
)

KINDS = {
    "question": "Someone is asking for information or help and has not been answered.",
    "decision": "States that something has been decided, changed, or agreed.",
    "incident": "Reports something broken, degraded, or blocked right now.",
    "announcement": "Broadcast information: a release, a policy, a schedule, a new joiner.",
    "coordination": "Scheduling, handoffs, and 'who is picking this up' traffic.",
    "social": "Chat, congratulations, jokes, lunch, and links shared for fun.",
    "other": "None of these describe it.",
}

RELEVANCE_LEVELS = [
    "Nothing in this message touches the work the reader says they own",
    "Adjacent: the same product area, but not the part the reader owns",
    "Directly about something the reader owns or is responsible for",
    "About the reader's own work and something is expected of them because of it",
]


def load_messages(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def mentions(text: str, handle: str) -> bool:
    """A literal @mention is a string match. Never ask a model to do this."""
    handle = handle.strip().lstrip("@")
    if not handle:
        return False
    return re.search(rf"@{re.escape(handle)}\b", text, flags=re.IGNORECASE) is not None


def build_questions(messages: list[dict], scope: str) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(messages)):
        message = {
            "channel": f"`messages[{index}].channel`",
            "author": f"`messages[{index}].author`",
            "text": f"`messages[{index}].text`",
            "replies": f"`messages[{index}].replies`",
        }
        questions[f"concerns_{index}"] = noul(
            {"task": "Does this message concern work the reader says they own?",
             "message": message, "what_the_reader_owns": scope},
            true="It touches a system, process, or responsibility the reader named as theirs",
            false="It belongs to someone else's area, or to nobody in particular",
        )
        questions[f"open_ask_{index}"] = noul(
            {"task": "Does this message contain a request or question that the replies have not resolved?",
             "message": message},
            true="Something is being asked and the thread shows no answer or commitment",
            false="No request was made, or the replies already settle it",
        )
        questions[f"decision_{index}"] = noul(
            {"task": "Does this message state that something has been decided or changed?",
             "message": message},
            true="Announces a decision, a reversal, a new rule, or a change that now applies",
            false="Discussion, opinion, or a proposal nobody has accepted yet",
        )
        questions[f"broken_{index}"] = noul(
            {"task": "Does this message report something broken, degraded, or blocked?",
             "message": message},
            true="A system is failing, a person is blocked, or work has stopped",
            false="Everything described is working as intended",
        )
        questions[f"kind_{index}"] = choice(
            {"task": "What kind of message is this?", "message": message}, KINDS
        )
        questions[f"relevance_{index}"] = score(
            {"task": "How closely does this bear on the reader's own responsibilities?",
             "message": message, "what_the_reader_owns": scope},
            RELEVANCE_LEVELS,
        )
    return questions


def surfaces(judgment: dict, mentioned: bool, *, relevance_gate: float, noul_gate: float) -> bool:
    """Pure policy. A direct mention always survives the filter."""
    if mentioned:
        return True
    if judgment["relevance"] < relevance_gate:
        return False
    return (
        judgment["concerns"] >= noul_gate
        or judgment["broken"] >= noul_gate
        or judgment["decision"] >= noul_gate
    )


def reasons(judgment: dict, mentioned: bool, *, noul_gate: float) -> list[str]:
    """Why it survived -- shown so the filter can be argued with."""
    tags = []
    if mentioned:
        tags.append("📣 you were named")
    if judgment["broken"] >= noul_gate:
        tags.append("🔥 something is broken")
    if judgment["decision"] >= noul_gate:
        tags.append("📌 a decision landed")
    if judgment["open_ask"] >= noul_gate:
        tags.append("❓ unanswered ask")
    if judgment["concerns"] >= noul_gate:
        tags.append("🎯 your area")
    return tags


def main() -> None:
    st.set_page_config(page_title="Slack Digest", page_icon="💬", layout="wide")
    st.title("💬 Slack Digest")
    st.caption("A filter, not a summary. Every line below is what somebody actually posted.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Filter")
        st.caption("Re-filtering makes **no** API calls.")
        relevance_gate = st.slider("Relevance needed to surface", 0.0, 3.0, 1.5, 0.1)
        noul_gate = st.slider("P(yes) for a reason to count", 0.0, 1.0, 0.6, 0.05)
        handle = st.text_input("Your handle", value=DEFAULT_HANDLE)
        export_path = st.text_input("Slack export JSON (optional)", value="")

    scope = st.text_area("What do you own?", value=DEFAULT_SCOPE, height=120)
    messages = load_messages(Path(export_path).expanduser() if export_path.strip() else None)
    if not messages:
        st.warning("No messages. Provide a Slack export JSON, or restore sample_slack.json.")
        return
    channels = sorted({m.get("channel", "?") for m in messages})
    if not export_path.strip():
        st.caption(f"Using the bundled sample: {len(messages)} messages across {len(channels)} channels.")

    if st.button(f"Filter {len(messages)} messages", type="primary"):
        questions = build_questions(messages, scope.strip())
        try:
            with st.spinner("Reading the channels..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"messages": messages, "reader_scope": scope}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["digest"] = {
            "messages": messages,
            "judgments": [
                {
                    "concerns": answers.noul(f"concerns_{i}"),
                    "open_ask": answers.noul(f"open_ask_{i}"),
                    "decision": answers.noul(f"decision_{i}"),
                    "broken": answers.noul(f"broken_{i}"),
                    "kind": answers.choice(f"kind_{i}"),
                    "kind_confidence": answers.confidence(f"kind_{i}"),
                    "relevance": answers.score(f"relevance_{i}"),
                }
                for i in range(len(messages))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    state = st.session_state.get("digest")
    if not state:
        st.info("Hit the button to filter the backlog.")
        return

    # --- policy only below this line ---
    kept: dict[str, list[tuple[dict, dict, list[str]]]] = {}
    for message, judgment in zip(state["messages"], state["judgments"]):
        mentioned = mentions(message.get("text", ""), handle)
        if not surfaces(judgment, mentioned, relevance_gate=relevance_gate, noul_gate=noul_gate):
            continue
        kept.setdefault(message.get("channel", "?"), []).append(
            (message, judgment, reasons(judgment, mentioned, noul_gate=noul_gate))
        )
    surfaced = sum(len(rows) for rows in kept.values())

    columns = st.columns(4)
    columns[0].metric("Worth reading", surfaced)
    columns[1].metric("Filtered out", len(state["messages"]) - surfaced)
    columns[2].metric("Channels with news", f"{len(kept)}/{len(channels)}")
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    if not kept:
        st.success("Nothing in the backlog concerns you. Close the tab.")
    for channel in sorted(kept, key=lambda name: -len(kept[name])):
        st.subheader(f"#{channel}")
        for message, judgment, tags in sorted(kept[channel], key=lambda row: -row[1]["relevance"]):
            with st.container(border=True):
                st.markdown(f"**{message.get('author', '?')}** · {message.get('time', '')}")
                st.text(message.get("text", ""))
                for reply in message.get("replies", []):
                    st.caption(f"↳ {reply}")
                st.caption(
                    " · ".join(tags + [f"{judgment['kind']}", f"relevance {judgment['relevance']:.2f}/3"])
                )

    quiet = [c for c in channels if c not in kept]
    if quiet:
        st.caption("Nothing for you in: " + ", ".join(f"#{c}" for c in quiet))

    st.divider()
    st.caption(
        f"{len(state['messages'])} messages × 6 judgments = {state['questions']} questions in 1 request · "
        f"{state['elapsed']:.1f}s · {state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Both gates re-filter the backlog for $0.00."
    )


if __name__ == "__main__":
    main()
