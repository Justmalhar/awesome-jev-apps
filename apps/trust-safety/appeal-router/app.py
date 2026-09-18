"""Appeal Router -- which appeals plausibly overturn, and which the model
should refuse to call.

Appeal queues are where moderation goes to die. They are long, they are
first-in-first-out, and the overturn rate in most of them is somewhere between
5% and 20% -- which means the great majority of the reviewer's day is spent
confirming decisions that were right, while the wrongly-actioned user waits
three weeks behind them.

This is a second opinion, not a decision. It reads the original decision, the
rule that was cited, the content, and what the appellant said, and it does one
useful thing: it orders the queue so the plausible overturns surface first.

Abstention is the feature. A second opinion that never says "I cannot tell"
is not a second opinion, it is a coin toss with a confidence score -- so
`cannot_tell` is an explicit option, and low-confidence calls are routed to a
senior reviewer by Python rather than guessed at.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_appeals.json"
SAMPLE_RULES = HERE / "sample_rules.txt"
TEXT_CHARS = 1200

# The second opinion itself. `cannot_tell` is the abstention -- a Choice must
# return something, and without it every unreadable appeal becomes a confident
# uphold, which is the exact failure mode appeal queues already have.
VIEWS = {
    "overturn": "On this record, the original decision looks wrong and the appeal should succeed.",
    "uphold": "On this record, the original decision looks right and the appeal should fail.",
    "cannot_tell": "The record does not contain enough to have a view either way.",
}

# What the original decision is costing the user while the appeal waits.
# Used only to order the queue -- never to decide the appeal.
COST_LEVELS = [
    "One post removed and nothing else; the account carries on exactly as before",
    "A visible strike or reduced reach: noticeable, survivable, and reversible later",
    "A capability they depend on is suspended: posting, messaging, selling, or monetisation",
    "The account itself is gone, and their audience, income, and history went with it",
]

DEFAULT_GATES = {
    "confidence": 0.6,  # Choice confidence below which the app abstains
    "signal": 0.6,      # P(signal present) to treat one of the three signals as present
    "rule_fits": 0.5,   # P(cited rule actually covers the content) below which an uphold is suspect
    "cost": 2.0,        # cost level at or above which even a clear overturn is expedited
}


def parse_rules(text: str) -> list[dict]:
    """`R4: No crossposting` -> {"id": "R4", "text": "No crossposting"}."""
    rules: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, body = line.partition(":")
        head, body = head.strip(), body.strip()
        if sep and body and " " not in head and len(head) <= 12 and any(c.isdigit() for c in head):
            rules.append({"id": head, "text": body})
        else:
            rules.append({"id": f"R{len(rules) + 1}", "text": line})
    return rules


def resolve_citations(appeals: list[dict], rules: list[dict]) -> list[str]:
    """Attach the cited rule's TEXT to each appeal, in Python.

    The model is never asked to look a rule id up in a list -- that is a lookup,
    it is exact, and Python does it perfectly. A citation that is not in the
    rulebook is a process error on its face, so it is reported rather than
    silently dropped.
    """
    book = {rule["id"]: rule["text"] for rule in rules}
    unknown: list[str] = []
    for appeal in appeals:
        cited = str(appeal.get("cited_rule", "")).strip()
        appeal["cited_rule_text"] = book.get(cited, "")
        if cited not in book:
            unknown.append(cited or "(none cited)")
    return unknown


def build_questions(appeals: list[dict]) -> dict:
    """One view, three independent signals, and one cost ordering per appeal."""
    questions: dict[str, dict] = {}
    for index in range(len(appeals)):
        reference = {
            "content_that_was_actioned": f"`appeals[{index}].content`",
            "action_taken": f"`appeals[{index}].action`",
            "rule_the_moderator_cited": f"`appeals[{index}].cited_rule_text`",
            "reason_given_to_the_user": f"`appeals[{index}].reason_given`",
            "what_the_appellant_says": f"`appeals[{index}].appellant_statement`",
        }
        questions[f"view_{index}"] = choice(
            {"task": "Reviewing this moderation decision fresh, should the appeal succeed?",
             "appeal": reference},
            VIEWS,
        )
        # Three signals that are independent of the view and of each other. An
        # appeal can bring new information and still deserve to fail; a decision
        # can be procedurally broken and still have reached the right answer.
        questions[f"rule_fits_{index}"] = noul(
            {"task": "Does the actioned content actually do the thing the cited rule forbids?",
             "appeal": reference},
            true="The content plainly does what that specific rule prohibits",
            false="The content does not do that, or the cited rule governs something else entirely",
        )
        questions[f"new_information_{index}"] = noul(
            {"task": "Does the appellant supply context that the original reviewer could not have had?",
             "appeal": reference},
            true="Offers an explanation, permission, or circumstance that is not visible in the content itself",
            false="Only restates the content, disputes the rule, or objects without adding anything new",
        )
        questions[f"process_error_{index}"] = noul(
            {"task": "Is there a defect in how the decision was made, separate from whether it was right?",
             "appeal": reference},
            true="The wrong rule is cited, the reason contradicts the action, or no usable reason was given",
            false="The decision cites a relevant rule and gives a reason that matches the action taken",
        )
        questions[f"cost_{index}"] = score(
            {"task": "What is this decision costing the user while the appeal waits?",
             "appeal": reference},
            COST_LEVELS,
        )
    return questions


def decide(view: str, confidence: float, signals: dict, cost: float, gates: dict) -> str:
    """Route one appeal. Pure -- this is an ordering, never an outcome.

    Every route ends with a human. The only thing being decided is which human
    and in what order.
    """
    if view == "cannot_tell" or confidence < gates["confidence"]:
        return "abstain"
    if signals["process_error"] >= gates["signal"]:
        # Procedurally broken decisions are re-made, not re-argued, whichever
        # way the content itself points.
        return "priority_review"
    if view == "overturn":
        return "priority_review" if cost >= gates["cost"] else "overturn_recommended"
    if signals["new_information"] >= gates["signal"]:
        return "priority_review"
    if signals["rule_fits"] < gates["rule_fits"]:
        return "priority_review"
    return "uphold_recommended"


def main() -> None:
    st.set_page_config(page_title="Appeal Router", page_icon="⚖️", layout="wide")
    st.title("⚖️ Appeal Router")
    st.caption("A second opinion that is allowed to abstain — ordering the queue, never deciding it.")

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
            "confidence": st.slider("Confidence below which we abstain", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
            "signal": st.slider("P(signal present) to count a signal", 0.0, 1.0, DEFAULT_GATES["signal"], 0.05),
            "rule_fits": st.slider("P(cited rule fits) below which an uphold is suspect", 0.0, 1.0, DEFAULT_GATES["rule_fits"], 0.05),
            "cost": st.slider("Cost to the user that expedites an overturn", 0.0, 3.0, DEFAULT_GATES["cost"], 0.1),
        }
        batch_size = st.slider("Appeals per request", 2, 25, 8, 1)

    rules_text = st.text_area(
        "Your rulebook — the cited rule's text is looked up here, in Python",
        value=SAMPLE_RULES.read_text(encoding="utf-8") if SAMPLE_RULES.is_file() else "",
        height=200,
    )
    rules = parse_rules(rules_text)
    if not rules:
        st.warning("Paste the rulebook the original decisions cite.")
        return

    uploaded = st.file_uploader(
        "Appeals JSON: [{content, action, cited_rule, reason_given, appellant_statement}]", type=["json"]
    )
    if uploaded:
        appeals = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        appeals = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(appeals)} appeals.")
    else:
        st.warning("Upload an appeals JSON.")
        return

    for appeal in appeals:
        for field in ("content", "appellant_statement", "reason_given"):
            appeal[field] = str(appeal.get(field, ""))[:TEXT_CHARS]

    unknown = resolve_citations(appeals, rules)
    if unknown:
        st.warning(
            f"{len(unknown)} appeal(s) cite a rule that is not in this rulebook: "
            f"{', '.join(sorted(set(unknown)))}. That is a process defect in itself — "
            "found in Python, not asked of the model."
        )

    if not st.button(f"Route {len(appeals)} appeals", type="primary"):
        return

    groups = [appeals[i : i + batch_size] for i in range(0, len(appeals), batch_size)]
    routed: list[dict] = []
    cost_usd = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Taking a second look in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group)
                    answers = client.ask({"appeals": group}, questions)
                    cost_usd += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, appeal in enumerate(group):
                        signals = {
                            "rule_fits": answers.noul(f"rule_fits_{index}"),
                            "new_information": answers.noul(f"new_information_{index}"),
                            "process_error": answers.noul(f"process_error_{index}"),
                        }
                        routed.append(
                            {
                                **appeal,
                                "view": answers.choice(f"view_{index}"),
                                "confidence": answers.confidence(f"view_{index}"),
                                "probabilities": answers.probabilities(f"view_{index}"),
                                "signals": signals,
                                "user_cost": answers.score(f"cost_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in routed:
        item["route"] = decide(
            item["view"], item["confidence"], item["signals"], item["user_cost"], gates
        )

    counts = Counter(item["route"] for item in routed)
    columns = st.columns(4)
    columns[0].metric("Appeals", len(routed))
    columns[1].metric("Priority", counts["priority_review"])
    columns[2].metric("Likely overturn", counts["overturn_recommended"])
    columns[3].metric("Abstained", counts["abstain"])

    st.subheader("🔴 Look at these first")
    st.caption("Process defects, costly overturns, and upholds the cited rule does not support.")
    priority = sorted([i for i in routed if i["route"] == "priority_review"], key=lambda i: -i["user_cost"])
    if not priority:
        st.info("Nothing expedited this run.")
    for item in priority:
        with st.container(border=True):
            st.markdown(f"**{item['action']}** citing `{item['cited_rule']}` · cost to user {item['user_cost']:.2f}/3")
            st.write(item["content"])
            st.caption(f"Appellant: {item['appellant_statement']}")
            st.caption(
                f"view {item['view']} ({item['confidence']:.2f}) · "
                + " · ".join(f"{k} {v:.2f}" for k, v in item["signals"].items())
            )

    st.subheader("🧑 Abstained — senior reviewer")
    st.caption("The model declined, or was not confident enough to be worth reading. These are not 'uphold'.")
    abstained = [i for i in routed if i["route"] == "abstain"]
    if not abstained:
        st.info("Nothing abstained this run. If that is true every run, the confidence gate is too low.")
    for item in abstained:
        st.markdown(f"**{item['action']}** citing `{item['cited_rule']}` — {item['content'][:140]}")
        st.caption(
            f"best view {item['view']} at {item['confidence']:.2f} · "
            + ", ".join(f"{k} {v:.2f}" for k, v in sorted(item["probabilities"].items(), key=lambda kv: -kv[1]))
        )

    for label, title in (("overturn_recommended", "🟡 Likely overturn"), ("uphold_recommended", "🟢 Likely uphold")):
        items = [i for i in routed if i["route"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})"):
            for item in items:
                st.markdown(f"**{item['action']}** citing `{item['cited_rule']}` — {item['content'][:140]}")
                st.caption(
                    f"confidence {item['confidence']:.2f} · cost {item['user_cost']:.2f}/3 · "
                    + " · ".join(f"{k} {v:.2f}" for k, v in item["signals"].items())
                )

    st.subheader("Where the queue went")
    st.caption("Counted in Python from the routes — the model is never asked for a tally.")
    st.bar_chart({key: value for key, value in counts.most_common()})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(routed)} appeals in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost_usd:.6f} · {elapsed:.2f}s "
        f"(${cost_usd / max(len(routed), 1):.8f} per appeal). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
