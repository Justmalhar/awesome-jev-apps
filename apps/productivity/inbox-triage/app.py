"""Inbox Triage -- what in my mail actually needs me today.

Not a summarizer. Jev cannot generate, so this never rewrites your mail -- it
sorts it, and every subject line you see is your own text.

The judgment that matters is not "is this important?" (unanswerable in the
abstract) but "does this need an action FROM ME, and by when?". Those are two
independent properties plus a degree, so they are three questions, not one.

Reads a folder of .eml files, or the bundled sample.

    uv run streamlit run app.py
"""

from __future__ import annotations

import email
import json
from email import policy
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_inbox.json"
BODY_CHARS = 1200

CATEGORIES = {
    "needs_reply": "A person is waiting on a response from the recipient.",
    "needs_action": "Requires the recipient to do something other than reply (review, pay, book, sign).",
    "fyi": "Informational. A human sent it but no response is expected.",
    "automated": "System notification, receipt, digest, alert, or calendar invite.",
    "promotional": "Marketing, newsletter, sales outreach, or recruiting spam.",
}


def load_emails(folder: Path | None) -> list[dict]:
    """Parse .eml files, or fall back to the bundled sample."""
    if folder and folder.is_dir():
        messages = []
        for path in sorted(folder.glob("*.eml")):
            parsed = email.message_from_bytes(path.read_bytes(), policy=policy.default)
            body_part = parsed.get_body(preferencelist=("plain", "html"))
            body = body_part.get_content() if body_part else ""
            messages.append(
                {
                    "from": str(parsed.get("From", "")),
                    "subject": str(parsed.get("Subject", "")),
                    "date": str(parsed.get("Date", "")),
                    "body": body[:BODY_CHARS],
                }
            )
        if messages:
            return messages
    return json.loads(SAMPLE.read_text(encoding="utf-8")) if SAMPLE.is_file() else []


def build_questions(messages: list[dict]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(messages)):
        reference = {
            "from": f"`emails[{index}].from`",
            "subject": f"`emails[{index}].subject`",
            "body": f"`emails[{index}].body`",
        }
        # Independent properties -> one Noul each. Do not collapse into a rubric.
        questions[f"mine_{index}"] = noul(
            {"task": "Does this email require an action or reply from the recipient personally?",
             "email": reference},
            true="The recipient is the one being asked to do or decide something",
            false="No action is needed, or it is addressed to someone else / everyone",
        )
        questions[f"deadline_{index}"] = noul(
            {"task": "Does this email state or clearly imply a deadline?", "email": reference},
            true="Names a date, time, or window by which something must happen",
            false="No time constraint stated or implied",
        )
        questions[f"waiting_{index}"] = noul(
            {"task": "Is the sender following up because a previous message went unanswered?",
             "email": reference},
            true="References an earlier message, chases, or expresses waiting",
            false="First contact on this topic",
        )
        questions[f"kind_{index}"] = choice(
            {"task": "What kind of email is this?", "email": reference}, CATEGORIES
        )
        questions[f"urgency_{index}"] = score(
            {"task": "How soon does this need attention?", "email": reference},
            [
                "No time pressure at all; could be ignored indefinitely",
                "Should be handled in the next week or two",
                "Should be handled in the next day or two",
                "Needs attention today; delay causes a real problem",
            ],
        )
    return questions


def main() -> None:
    st.set_page_config(page_title="Inbox Triage", page_icon="📥", layout="wide")
    st.title("📥 Inbox Triage")
    st.caption("What actually needs you today. Your mail is sorted, never rewritten.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        mine_gate = st.slider("P(needs me) to count as mine", 0.0, 1.0, 0.6, 0.05)
        today_gate = st.slider("Urgency to land in Today", 0.0, 3.0, 2.2, 0.1)
        folder_text = st.text_input("Folder of .eml files (optional)", value="")

    folder = Path(folder_text).expanduser() if folder_text.strip() else None
    messages = load_emails(folder)
    if not messages:
        st.warning("No messages. Point at a folder of .eml files, or add sample_inbox.json.")
        return
    if folder is None:
        st.caption(f"Using the bundled sample: {len(messages)} messages.")

    if not st.button(f"Triage {len(messages)} messages", type="primary"):
        return

    try:
        with st.spinner("Judging..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"emails": messages}, build_questions(messages))
    except JevError as exc:
        st.error(str(exc))
        return

    triaged = []
    for index, message in enumerate(messages):
        triaged.append(
            {
                **message,
                "mine": answers.noul(f"mine_{index}"),
                "deadline": answers.noul(f"deadline_{index}"),
                "waiting": answers.noul(f"waiting_{index}"),
                "kind": answers.choice(f"kind_{index}"),
                "kind_confidence": answers.confidence(f"kind_{index}"),
                "urgency": answers.score(f"urgency_{index}"),
            }
        )

    # ── Policy in code. Move a gate, re-derive everything, spend nothing. ──
    today, this_week, rest = [], [], []
    for item in triaged:
        if item["mine"] >= mine_gate and item["urgency"] >= today_gate:
            today.append(item)
        elif item["mine"] >= mine_gate:
            this_week.append(item)
        else:
            rest.append(item)
    for bucket in (today, this_week):
        bucket.sort(key=lambda i: -(i["urgency"] + i["waiting"]))

    columns = st.columns(4)
    columns[0].metric("Today", len(today))
    columns[1].metric("This week", len(this_week))
    columns[2].metric("Not yours", len(rest))
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")

    def render(items: list[dict], empty: str) -> None:
        if not items:
            st.caption(empty)
            return
        for item in items:
            flags = []
            if item["waiting"] > 0.6:
                flags.append("🔁 chasing you")
            if item["deadline"] > 0.6:
                flags.append("⏰ has a deadline")
            if item["kind_confidence"] < 0.5:
                flags.append("❓ unsure what this is")
            with st.container(border=True):
                st.markdown(f"**{item['subject']}**")
                st.caption(
                    f"from {item['from']} · {item['kind']} · urgency {item['urgency']:.2f}/3 · "
                    f"needs you P={item['mine']:.2f}"
                    + ("  ·  " + " · ".join(flags) if flags else "")
                )
                with st.expander("Body"):
                    st.text(item["body"][:BODY_CHARS])

    tabs = st.tabs([f"🔥 Today ({len(today)})", f"📌 This week ({len(this_week)})",
                    f"💤 Everything else ({len(rest)})"])
    with tabs[0]:
        render(today, "Nothing needs you today.")
    with tabs[1]:
        render(this_week, "Nothing queued.")
    with tabs[2]:
        render(rest, "Empty.")

    st.divider()
    st.caption(
        f"{len(messages)} messages × 5 judgments = {len(messages) * 5} questions in 1 request · "
        f"{answers.elapsed_s:.1f}s · {answers.input_tokens:,} tokens. "
        "Both sliders above re-sort everything without another call."
    )


if __name__ == "__main__":
    main()
