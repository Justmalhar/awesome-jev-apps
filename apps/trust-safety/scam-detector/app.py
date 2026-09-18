"""Scam Detector -- advance-fee, impersonation and phishing patterns, as YOUR
platform defines them.

Scam text is boring and it mutates weekly. Keyword rules catch last month's
wording and nothing else; a general "is this spam?" model has no idea that on
*your* platform the tell is asking a tenant for a deposit before a viewing, and
that on someone else's it is asking for a deposit before a viewing and that is
completely normal.

The patterns your fraud team has actually written down live in a text box, one
Noul each. Beside them sit six hazards that recur across every scam family --
advance fee, impersonation, credential request, urgency, moving the conversation
off-platform, and an irreversible payment method. They co-occur freely and never
in the same combination twice, which is precisely why each one is its own Noul
with its own threshold rather than a single "scam score".

Nothing here accuses anybody. It fills a queue and it puts a warning on a
message; a person decides what happens to an account.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_messages.json"
SAMPLE_PATTERNS = HERE / "sample_patterns.txt"
BODY_CHARS = 1500

# What the recipient is being moved toward. Ordered, concrete, and about the
# ASK -- not about how nasty the message sounds.
ASK_LEVELS = [
    "Nothing is being asked for: an ordinary message that wants a reply at most",
    "A small step with no cost: click a link, confirm a detail already public, or continue the chat elsewhere",
    "Money or identity documents: a payment, a deposit, a fee, a photo of an ID, or bank details",
    "Irreversible and unrecoverable: gift cards, crypto, a bank transfer to a new account, or a login code read aloud",
]

# The story the sender is telling. `no_pretext` is the mandatory escape hatch;
# most inbound messages on any platform are ordinary.
PRETEXT_KINDS = {
    "prize_or_refund": "You have won something, or are owed a refund, and need to do one thing to get it.",
    "authority_impersonation": "Claims to be the platform, a bank, a delivery firm, a tax office, or the police.",
    "person_impersonation": "Claims to be someone the recipient knows — family, a colleague, or a known contact.",
    "job_or_investment": "Offers work, an opportunity, or a return on money the recipient puts in.",
    "romance_or_friendship": "Builds rapport or affection ahead of any request.",
    "goods_transaction": "A purchase, sale, rental, or booking between the two parties.",
    "no_pretext": "No story is being told: an ordinary message with no persuasive frame.",
}

DEFAULT_GATES = {
    "pattern": 0.65,    # P(your fraud team's pattern matches) to escalate
    "hazard": 0.6,      # P(hazard present) to flag one hazard
    "quarantine": 3,    # hazards that must agree before quarantining
    "ask": 2.0,         # ask level at or above which money or identity is in play
    "warn": 1,          # hazards that must agree before a warning banner
    "confidence": 0.5,  # Choice confidence below which the pretext label is dropped
}


def parse_patterns(text: str) -> list[dict]:
    """`S4: Deposit before viewing` -> {"id": "S4", "text": "Deposit before viewing"}."""
    patterns: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, body = line.partition(":")
        head, body = head.strip(), body.strip()
        if sep and body and " " not in head and len(head) <= 12 and any(c.isdigit() for c in head):
            patterns.append({"id": head, "text": body})
        else:
            patterns.append({"id": f"S{len(patterns) + 1}", "text": line})
    return patterns


def build_questions(messages: list[dict], patterns: list[dict]) -> dict:
    """One Noul per operator pattern, plus six independent hazards."""
    questions: dict[str, dict] = {}
    for index in range(len(messages)):
        reference = {
            "sender": f"`messages[{index}].sender`",
            "channel": f"`messages[{index}].channel`",
            "subject": f"`messages[{index}].subject`",
            "body": f"`messages[{index}].body`",
        }
        for position, pattern in enumerate(patterns):
            questions[f"pattern_{index}_{position}"] = noul(
                {
                    "task": "Does this inbound message match the scam pattern described below?",
                    "pattern": f"`patterns[{position}].text`",
                    "message": reference,
                },
                true="The message does the thing this pattern describes",
                false="It does not, or the pattern describes a different situation",
            )
        # Six hazards. Each is independently true or false, they appear in a
        # different combination in every scam family, and each deserves its own
        # threshold -- so six Nouls, never one rubric and never one Choice.
        questions[f"advance_fee_{index}"] = noul(
            {"task": "Does the message require the recipient to pay or send something first, to receive something later?",
             "message": reference},
            true="A fee, deposit, tax, shipping charge, or unlock payment stands between the recipient and a promised benefit",
            false="Nothing must be paid up front to obtain whatever is on offer",
        )
        questions[f"impersonation_{index}"] = noul(
            {"task": "Does the sender claim to be an organisation or a person they are unlikely to be?",
             "message": reference},
            true="Presents as the platform, a bank, an official body, a delivery firm, or a known contact, in a way inconsistent with the sender details",
            false="The sender presents as themselves, or makes no claim about who they are",
        )
        questions[f"credential_request_{index}"] = noul(
            {"task": "Does the message try to obtain a password, one-time code, or account access?",
             "message": reference},
            true="Asks for a login, a verification or security code, remote access, or a click-through to sign in somewhere",
            false="No login, code, or account access is sought",
        )
        questions[f"urgency_{index}"] = noul(
            {"task": "Does the message apply time pressure to stop the recipient checking with anyone?",
             "message": reference},
            true="A deadline, a threat of loss, or an instruction to act now or keep it private",
            false="The recipient is free to take their time or consult somebody",
        )
        questions[f"off_platform_{index}"] = noul(
            {"task": "Does the message try to move the conversation off this platform?",
             "message": reference},
            true="Pushes to a messaging app, personal email, phone number, or outside website to continue",
            false="Expects the exchange to continue where it started",
        )
        questions[f"irreversible_payment_{index}"] = noul(
            {"task": "Does the message steer toward a payment method that cannot be reversed?",
             "message": reference},
            true="Gift cards, cryptocurrency, bank transfer to a new account, or cash outside any buyer protection",
            false="Either no payment, or one through a channel with normal protections",
        )
        questions[f"ask_{index}"] = score(
            {"task": "What is the recipient ultimately being moved toward doing?", "message": reference},
            ASK_LEVELS,
        )
        questions[f"pretext_{index}"] = choice(
            {"task": "What story is the sender telling to justify the request?", "message": reference},
            PRETEXT_KINDS,
        )
    return questions


def decide(pattern_hit: float, hazards: dict, ask: float, gates: dict) -> str:
    """Route one message. Pure -- retune the gates with no inference at all.

    Quarantine needs several independent hazards AND a real-world ask. One
    hazard on a message that asks for nothing is a false positive engine:
    urgency alone describes most of the legitimate messages on any platform.
    """
    flagged = [name for name, probability in hazards.items() if probability >= gates["hazard"]]
    if len(flagged) >= gates["quarantine"] and ask >= gates["ask"]:
        return "quarantine"
    if pattern_hit >= gates["pattern"]:
        return "human_review"
    if len(flagged) >= gates["quarantine"]:
        return "human_review"
    if len(flagged) >= gates["warn"]:
        return "warn"
    return "deliver"


def main() -> None:
    st.set_page_config(page_title="Scam Detector", page_icon="🎣", layout="wide")
    st.title("🎣 Scam Detector")
    st.caption("Advance fee, impersonation and phishing — against your own fraud team's patterns.")

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
            "pattern": st.slider("P(your pattern matches) to escalate", 0.0, 1.0, DEFAULT_GATES["pattern"], 0.05),
            "hazard": st.slider("P(hazard present) to flag it", 0.0, 1.0, DEFAULT_GATES["hazard"], 0.05),
            "quarantine": st.slider("Hazards that must agree to quarantine", 1, 6, DEFAULT_GATES["quarantine"], 1),
            "warn": st.slider("Hazards that must agree to warn", 1, 6, DEFAULT_GATES["warn"], 1),
            "ask": st.slider("Ask level required before quarantine", 0.0, 3.0, DEFAULT_GATES["ask"], 0.1),
            "confidence": st.slider("Confidence to trust the pretext label", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
        }
        batch_size = st.slider("Messages per request", 2, 25, 8, 1)

    patterns_text = st.text_area(
        "Your fraud team's known patterns — edit these, the questions follow",
        value=SAMPLE_PATTERNS.read_text(encoding="utf-8") if SAMPLE_PATTERNS.is_file() else "",
        height=200,
    )
    patterns = parse_patterns(patterns_text)
    if not patterns:
        st.warning("Write at least one pattern.")
        return

    uploaded = st.file_uploader("Messages JSON: [{sender, channel, subject, body}]", type=["json"])
    if uploaded:
        messages = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        messages = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(messages)} messages.")
    else:
        st.warning("Upload a messages JSON.")
        return

    for message in messages:
        message["body"] = str(message.get("body", ""))[:BODY_CHARS]

    if not st.button(f"Screen {len(messages)} messages", type="primary"):
        return

    groups = [messages[i : i + batch_size] for i in range(0, len(messages), batch_size)]
    screened: list[dict] = []
    cost = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Screening against {len(patterns)} patterns in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, patterns)
                    answers = client.ask({"patterns": patterns, "messages": group}, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, message in enumerate(group):
                        matched = {
                            pattern["id"]: answers.noul(f"pattern_{index}_{position}")
                            for position, pattern in enumerate(patterns)
                        }
                        hazards = {
                            "advance_fee": answers.noul(f"advance_fee_{index}"),
                            "impersonation": answers.noul(f"impersonation_{index}"),
                            "credential_request": answers.noul(f"credential_request_{index}"),
                            "urgency": answers.noul(f"urgency_{index}"),
                            "off_platform": answers.noul(f"off_platform_{index}"),
                            "irreversible_payment": answers.noul(f"irreversible_payment_{index}"),
                        }
                        screened.append(
                            {
                                **message,
                                "matched": matched,
                                "pattern_hit": max(matched.values()) if matched else 0.0,
                                "hazards": hazards,
                                "ask": answers.score(f"ask_{index}"),
                                "pretext": answers.choice(f"pretext_{index}"),
                                "pretext_confidence": answers.confidence(f"pretext_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in screened:
        item["action"] = decide(item["pattern_hit"], item["hazards"], item["ask"], gates)
        item["flagged"] = [k for k, v in item["hazards"].items() if v >= gates["hazard"]]
        if item["pretext_confidence"] < gates["confidence"]:
            item["pretext"] = "no_pretext"

    counts = Counter(item["action"] for item in screened)
    columns = st.columns(4)
    columns[0].metric("Messages", len(screened))
    columns[1].metric("Delivered", counts["deliver"])
    columns[2].metric("Quarantined", counts["quarantine"])
    columns[3].metric("To a human", counts["human_review"])

    st.subheader("🧑 Human review queue")
    st.caption("Your own patterns fired, or several hazards with nothing concrete being asked for.")
    queue = sorted([i for i in screened if i["action"] == "human_review"], key=lambda i: -i["ask"])
    if not queue:
        st.info("Empty this run. An always-empty queue means the gates are too wide.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['subject']}** · from `{item['sender']}` on {item['channel']}")
            st.write(item["body"])
            st.caption(
                f"worst pattern P={item['pattern_hit']:.2f} · ask level {item['ask']:.2f}/3 · "
                f"hazards: {', '.join(item['flagged']) or 'none'} · "
                f"pretext {item['pretext']} ({item['pretext_confidence']:.2f})"
            )

    for label, title in (("quarantine", "🔴 Quarantined"), ("warn", "🟡 Delivered with a warning"), ("deliver", "🟢 Delivered")):
        items = [i for i in screened if i["action"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})", expanded=label == "quarantine"):
            for item in items:
                st.markdown(f"**{item['subject']}** — {item['body'][:150]}")
                st.caption(f"ask {item['ask']:.2f}/3 · hazards: {', '.join(item['flagged']) or 'none'}")

    st.subheader("Which hazards are firing")
    st.caption("Counted in Python from the Nouls — the model is never asked for a tally.")
    firing = Counter()
    for item in screened:
        for name in item["flagged"]:
            firing[name] += 1
    st.bar_chart({name: firing.get(name, 0) for name in (
        "advance_fee", "impersonation", "credential_request", "urgency", "off_platform", "irreversible_payment")})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(screened)} messages in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(screened), 1):.8f} per message). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
