"""Community Moderator -- applies YOUR written rules, not a vendor's policy.

Every generic moderation API answers a question nobody actually asked: "is this
toxic in general?" A community does not run on general. It runs on the rules
pinned at the top of the forum, written by its own moderators, argued over, and
amended last Tuesday. A model that scores "toxicity" cannot tell you whether a
post breaks *rule 4*.

So the rules live in a text box in the sidebar. Edit them and the questions
change on the next run -- no retraining, no prompt engineering, no redeploy.
Each rule becomes its own Noul so each one gets its own threshold, and the
routing decision is made by `decide()` in Python where you can read it.

This assists human moderators. It does not replace them, and there is always
a human tier.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_posts.json"
SAMPLE_RULES = HERE / "sample_rules.txt"
BODY_CHARS = 2000

# Ordered rubric. Each level has to stand on its own -- "medium" tells a
# moderator nothing and tells the model less.
SEVERITY_LEVELS = [
    "Harmless: at worst slightly off-topic or untidy, a reader scrolls past without noticing",
    "Nuisance: unsolicited promotion, repetition, or bickering that clutters a thread but harms nobody",
    "Hostile: sustained rudeness or contempt aimed at another member, the kind that makes people leave",
    "Dangerous: threats, sharing of private personal details, or an organised pile-on against a person",
]

# Why the post is in the queue at all. A Choice must return something, so
# `no_concern` is the escape hatch -- without it a clean post gets filed under
# whichever complaint fits least badly, at entirely plausible confidence.
CONCERN_KINDS = {
    "spam_promotion": "Advertising, affiliate links, or repeated posting of the same commercial message.",
    "personal_attack": "Aimed at a specific member: insults, contempt, or sustained hostility toward them.",
    "off_topic": "On-topic for nothing: unrelated to the community's subject matter or the thread.",
    "misinformation": "States something as fact that the community's rules require to be sourced or corrected.",
    "privacy": "Discloses personal details about someone, or pressures another member to disclose them.",
    "no_concern": "Nothing here warrants a moderator's attention.",
}

DEFAULT_GATES = {
    "hit": 0.65,        # P(rule broken) at or above which we act
    "uncertain": 0.35,  # below this we are confident no rule was broken
    "block": 2.4,       # severity at or above which action is removal
    "warn": 0.0,        # any confirmed breach below `block` gets a warning
    "contested": 0.45,  # P(two moderators would disagree) that forces a human
}


def parse_rules(text: str) -> list[dict]:
    """`R4: No unsolicited advertising` -> {"id": "R4", "text": "No unsolicited..."}.

    Blank lines and `#` comments are ignored so operators can annotate their own
    rule set. A line without an `ID:` prefix gets a positional id.
    """
    rules: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, body = line.partition(":")
        head, body = head.strip(), body.strip()
        # An id must carry a digit ("R4", "3.1", "P7"). Otherwise a line like
        # "Warning: be civil" would have "Warning" stolen as its id.
        if sep and body and " " not in head and len(head) <= 12 and any(c.isdigit() for c in head):
            rules.append({"id": head, "text": body})
        else:
            rules.append({"id": f"R{len(rules) + 1}", "text": line})
    return rules


def build_questions(posts: list[dict], rules: list[dict]) -> dict:
    """One Noul per rule per post, plus three judgments that cut across rules."""
    questions: dict[str, dict] = {}
    for index in range(len(posts)):
        reference = {
            "author": f"`posts[{index}].author`",
            "thread": f"`posts[{index}].thread`",
            "body": f"`posts[{index}].body`",
        }
        for position, rule in enumerate(rules):
            questions[f"rule_{index}_{position}"] = noul(
                {
                    "task": "Does this post break the community rule quoted below?",
                    "rule": f"`rules[{position}].text`",
                    "post": reference,
                },
                true="The post plainly does the thing the rule forbids",
                false="The post does not do that thing, or the rule is about something else entirely",
            )
        questions[f"severity_{index}"] = score(
            {"task": "If a moderator acted on this post, what is the real-world harm they would be acting on?",
             "post": reference},
            SEVERITY_LEVELS,
        )
        # Independent of any rule: an item two moderators would argue about is
        # exactly the item you must not automate. Its own Noul, its own gate.
        questions[f"contested_{index}"] = noul(
            {"task": "Would two experienced moderators applying these same rules plausibly reach opposite decisions here?",
             "post": reference},
            true="Genuinely borderline: sarcasm, quoted material, in-group banter, or an unclear target",
            false="Any reasonable moderator reading these rules would land in the same place",
        )
        questions[f"concern_{index}"] = choice(
            {"task": "What is the single main reason this post would come to a moderator's attention?",
             "post": reference},
            CONCERN_KINDS,
        )
    return questions


def decide(hit: float, severity: float, contested: float, gates: dict) -> str:
    """Route one post. Pure, so you can tune gates without spending a token.

    Contested is checked first on purpose: an automated removal is precisely the
    action you least want taken on an item moderators would argue about.
    """
    if contested >= gates["contested"]:
        return "human_review"
    if hit >= gates["hit"]:
        return "block" if severity >= gates["block"] else "warn"
    if hit >= gates["uncertain"]:
        return "human_review"
    return "allow"


def main() -> None:
    st.set_page_config(page_title="Community Moderator", page_icon="📋", layout="wide")
    st.title("📋 Community Moderator")
    st.caption("Your written rules, applied post by post — with a human tier that never empties.")

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
            "hit": st.slider("P(rule broken) to act", 0.0, 1.0, DEFAULT_GATES["hit"], 0.05),
            "uncertain": st.slider("P(rule broken) below which it is clean", 0.0, 1.0, DEFAULT_GATES["uncertain"], 0.05),
            "block": st.slider("Severity to remove rather than warn", 0.0, 3.0, DEFAULT_GATES["block"], 0.1),
            "warn": DEFAULT_GATES["warn"],
            "contested": st.slider("P(moderators disagree) that forces a human", 0.0, 1.0, DEFAULT_GATES["contested"], 0.05),
        }
        batch_size = st.slider("Posts per request", 2, 25, 8, 1)

    rules_text = st.text_area(
        "Your community rules — edit these, the questions follow",
        value=SAMPLE_RULES.read_text(encoding="utf-8") if SAMPLE_RULES.is_file() else "",
        height=200,
    )
    rules = parse_rules(rules_text)
    if not rules:
        st.warning("Write at least one rule.")
        return

    uploaded = st.file_uploader("Posts JSON: [{author, thread, body}]", type=["json"])
    if uploaded:
        posts = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        posts = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(posts)} posts.")
    else:
        st.warning("Upload a posts JSON.")
        return

    for post in posts:
        post["body"] = str(post.get("body", ""))[:BODY_CHARS]

    st.caption(f"{len(rules)} rules × {len(posts)} posts = {len(rules) * len(posts)} rule judgments, plus 3 per post.")
    if not st.button(f"Review {len(posts)} posts", type="primary"):
        return

    groups = [posts[i : i + batch_size] for i in range(0, len(posts), batch_size)]
    reviewed: list[dict] = []
    cost = tokens = elapsed = questions_asked = 0.0

    try:
        with st.spinner(f"Applying {len(rules)} rules in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, rules)
                    answers = client.ask({"rules": rules, "posts": group}, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    questions_asked += len(questions)
                    for index, post in enumerate(group):
                        broken = {
                            rule["id"]: answers.noul(f"rule_{index}_{position}")
                            for position, rule in enumerate(rules)
                        }
                        # max() is Python's job, not the model's.
                        top = max(broken.values()) if broken else 0.0
                        reviewed.append(
                            {
                                **post,
                                "broken": broken,
                                "hit": top,
                                "severity": answers.score(f"severity_{index}"),
                                "contested": answers.noul(f"contested_{index}"),
                                "concern": answers.choice(f"concern_{index}"),
                                "concern_confidence": answers.confidence(f"concern_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in reviewed:
        item["action"] = decide(item["hit"], item["severity"], item["contested"], gates)

    counts = Counter(item["action"] for item in reviewed)
    columns = st.columns(5)
    columns[0].metric("Posts", len(reviewed))
    columns[1].metric("Allowed", counts["allow"])
    columns[2].metric("Warned", counts["warn"])
    columns[3].metric("Blocked", counts["block"])
    columns[4].metric("To a human", counts["human_review"])

    st.subheader("🧑 Human review queue")
    st.caption("Contested, or sitting in the grey band. Nothing here is auto-actioned.")
    queue = sorted(
        [i for i in reviewed if i["action"] == "human_review"], key=lambda i: -i["contested"]
    )
    if not queue:
        st.info("Empty this run. If it is empty every run, your gates are too wide.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['author']}** in _{item['thread']}_ · contested P={item['contested']:.2f}")
            st.write(item["body"])
            st.caption(
                f"worst rule P={item['hit']:.2f} · severity {item['severity']:.2f}/3 · "
                f"{item['concern']} ({item['concern_confidence']:.2f})"
            )

    for label, title in (("block", "🔴 Proposed removals"), ("warn", "🟡 Proposed warnings"), ("allow", "🟢 Left alone")):
        items = [i for i in reviewed if i["action"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})", expanded=label == "block"):
            for item in items:
                fired = ", ".join(f"{rid} {p:.2f}" for rid, p in sorted(item["broken"].items(), key=lambda kv: -kv[1])[:3])
                st.markdown(f"**{item['author']}** — {item['body'][:160]}")
                st.caption(f"severity {item['severity']:.2f}/3 · rules: {fired}")

    st.subheader("Which rules are actually firing")
    st.caption("Counted in Python from the Nouls — a rule that never fires is a rule worth rewriting.")
    firing = Counter()
    for item in reviewed:
        for rid, probability in item["broken"].items():
            if probability >= gates["hit"]:
                firing[rid] += 1
    st.bar_chart({rule["id"]: firing.get(rule["id"], 0) for rule in rules})

    st.divider()
    st.caption(
        f"{int(questions_asked)} questions over {len(reviewed)} posts in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(reviewed), 1):.8f} per post). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
