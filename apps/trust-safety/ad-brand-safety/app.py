"""Ad Brand Safety -- is THIS creative safe beside THIS page?

Keyword blocklists are the industry's standing joke. They defund news sites over
the word "shooting" in a film review, and they sail past the placement that
actually embarrasses the brand, because the damage is never in a keyword -- it
is in the relationship between two things: what the ad says, and what the page
it lands next to is about. A cheerful "treat yourself this weekend" is fine
everywhere and ruinous beside one specific story.

That relationship needs both states judged together. So the creative and the
placement context both go into state, and every question is about the pair.

The brand's own exclusion rules -- which are a legal document at most
advertisers, not a vendor taxonomy -- live in a text box. One Noul per rule.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE_PLACEMENTS = HERE / "sample_placements.json"
SAMPLE_CREATIVE = HERE / "sample_creative.json"
SAMPLE_RULES = HERE / "sample_rules.txt"
SUMMARY_CHARS = 1200

# The pair, judged as a pair. Each level is a situation a media buyer has
# actually argued about in a meeting.
SUITABILITY_LEVELS = [
    "Reads as if the page and the ad were chosen for each other; the reader would not blink",
    "Unrelated but harmless: the page is about something else entirely and nothing rubs against the ad",
    "Awkward: the page's subject makes the ad's wording land oddly, and an attentive reader would notice",
    "Damaging: a reader could reasonably infer the brand endorses, profits from, or makes light of the page's subject",
]

# What the page is really about. `unclear` is the mandatory escape hatch --
# most pages fit none of these and a Choice always returns something.
CONTEXT_KINDS = {
    "hard_news_distress": "Reporting on an accident, disaster, crime, or bereavement involving real people.",
    "conflict_politics": "Contested politics, elections, or an active dispute where taking a side is a position.",
    "consumer_complaint": "Criticism of a product, company, or industry, including recalls and refunds.",
    "health_medical": "Illness, treatment, mental health, or medical advice.",
    "entertainment_lifestyle": "Reviews, recipes, sport, travel, hobbies, and other everyday leisure content.",
    "user_generated_unmoderated": "A comment thread, forum, or feed whose content is not editorially controlled.",
    "unclear": "The page's subject cannot be determined from what is here.",
}

DEFAULT_GATES = {
    "rule": 0.65,       # P(exclusion rule breached) to act
    "uncertain": 0.35,  # below this the rule is clean
    "hazard": 0.6,      # P(hazard) to flag one of the independent hazards
    "block": 2.4,       # suitability at or above which a placement is damaging
    "caution": 1.6,     # suitability at or above which we keep serving but throttle
    "confidence": 0.5,  # Choice confidence below which the context label is dropped
}


def parse_rules(text: str) -> list[dict]:
    """`B2: Never beside active conflict` -> {"id": "B2", "text": "Never beside..."}."""
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
            rules.append({"id": f"B{len(rules) + 1}", "text": line})
    return rules


def build_questions(placements: list[dict], rules: list[dict]) -> dict:
    """Every question names BOTH states: the creative and one placement."""
    questions: dict[str, dict] = {}
    for index in range(len(placements)):
        pair = {
            "ad_headline": "`creative.headline`",
            "ad_body": "`creative.body`",
            "ad_brand": "`creative.brand`",
            "page_title": f"`placements[{index}].page_title`",
            "page_summary": f"`placements[{index}].page_summary`",
            "page_site": f"`placements[{index}].site`",
        }
        for position, rule in enumerate(rules):
            questions[f"rule_{index}_{position}"] = noul(
                {
                    "task": "Would running this ad on this page breach the brand exclusion rule quoted below?",
                    "rule": f"`rules[{position}].text`",
                    "pair": pair,
                },
                true="This page is the situation the rule was written to keep the brand away from",
                false="The rule is about a different situation, or this page is not it",
            )
        # Three independent ways a pairing goes wrong. They co-occur freely, so
        # each is its own Noul with its own gate: a tonal clash is a media
        # buyer's problem, an implied endorsement is a legal one.
        questions[f"implied_comment_{index}"] = noul(
            {"task": "Placed here, would the ad read as if it were commenting on the page's subject?",
             "pair": pair},
            true="The ad's own wording could be read as a remark about what the page describes",
            false="The ad's wording has no plausible reading as a remark about this page",
        )
        questions[f"tone_clash_{index}"] = noul(
            {"task": "Does the ad's tone clash with the emotional register of the page?",
             "pair": pair},
            true="Upbeat, jokey, or celebratory wording beside distressing, sombre, or angry content",
            false="The registers sit comfortably together, or both are neutral",
        )
        questions[f"audience_mismatch_{index}"] = noul(
            {"task": "Is the page's likely audience the wrong one for what this ad offers?",
             "pair": pair},
            true="The people reading this page are not plausibly in the market for this product, or should not be shown it",
            false="The page's readers are a plausible and appropriate audience for the offer",
        )
        questions[f"suitability_{index}"] = score(
            {"task": "How badly would this pairing reflect on the advertiser if a journalist screenshotted it?",
             "pair": pair},
            SUITABILITY_LEVELS,
        )
        questions[f"context_{index}"] = choice(
            {"task": "What is this page mainly about?", "page": pair}, CONTEXT_KINDS
        )
    return questions


def decide(rule_hit: float, hazards: dict, suitability: float, gates: dict) -> str:
    """Route one placement. Pure -- retune gates without spending a token."""
    flagged = [name for name, probability in hazards.items() if probability >= gates["hazard"]]
    if rule_hit >= gates["rule"]:
        return "block" if suitability >= gates["block"] else "human_review"
    if flagged:
        return "human_review"
    if suitability >= gates["block"]:
        # Damaging, but no rule of the brand's own covers it. That is a gap in
        # the rule set, and a person should see it before the money moves.
        return "human_review"
    if suitability >= gates["caution"]:
        return "reduce"
    if rule_hit >= gates["uncertain"]:
        return "human_review"
    return "run"


def main() -> None:
    st.set_page_config(page_title="Ad Brand Safety", page_icon="📺", layout="wide")
    st.title("📺 Ad Brand Safety")
    st.caption("One creative, many placements — judged as pairs, against the brand's own exclusion rules.")

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
            "rule": st.slider("P(exclusion rule breached) to act", 0.0, 1.0, DEFAULT_GATES["rule"], 0.05),
            "uncertain": st.slider("P(rule breached) below which it is clean", 0.0, 1.0, DEFAULT_GATES["uncertain"], 0.05),
            "hazard": st.slider("P(hazard) to flag", 0.0, 1.0, DEFAULT_GATES["hazard"], 0.05),
            "block": st.slider("Damage level that blocks", 0.0, 3.0, DEFAULT_GATES["block"], 0.1),
            "caution": st.slider("Damage level that throttles", 0.0, 3.0, DEFAULT_GATES["caution"], 0.1),
            "confidence": st.slider("Confidence to trust the context label", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
        }
        batch_size = st.slider("Placements per request", 2, 25, 8, 1)

    left, right = st.columns(2)
    with left:
        creative_text = st.text_area(
            "The creative (JSON: brand, headline, body)",
            value=SAMPLE_CREATIVE.read_text(encoding="utf-8") if SAMPLE_CREATIVE.is_file() else "",
            height=190,
        )
    with right:
        rules_text = st.text_area(
            "The brand's exclusion rules — edit these, the questions follow",
            value=SAMPLE_RULES.read_text(encoding="utf-8") if SAMPLE_RULES.is_file() else "",
            height=190,
        )

    try:
        creative = json.loads(creative_text)
    except json.JSONDecodeError as exc:
        st.error(f"Creative is not valid JSON: {exc}")
        return
    rules = parse_rules(rules_text)
    if not rules:
        st.warning("Write at least one exclusion rule.")
        return

    uploaded = st.file_uploader("Placements JSON: [{site, page_title, page_summary}]", type=["json"])
    if uploaded:
        placements = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE_PLACEMENTS.is_file():
        placements = json.loads(SAMPLE_PLACEMENTS.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(placements)} placements.")
    else:
        st.warning("Upload a placements JSON.")
        return

    for placement in placements:
        placement["page_summary"] = str(placement.get("page_summary", ""))[:SUMMARY_CHARS]

    if not st.button(f"Check {len(placements)} placements", type="primary"):
        return

    groups = [placements[i : i + batch_size] for i in range(0, len(placements), batch_size)]
    judged: list[dict] = []
    cost = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Judging {len(placements)} pairings in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, rules)
                    answers = client.ask(
                        {"creative": creative, "rules": rules, "placements": group}, questions
                    )
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, placement in enumerate(group):
                        breaches = {
                            rule["id"]: answers.noul(f"rule_{index}_{position}")
                            for position, rule in enumerate(rules)
                        }
                        hazards = {
                            "implied_comment": answers.noul(f"implied_comment_{index}"),
                            "tone_clash": answers.noul(f"tone_clash_{index}"),
                            "audience_mismatch": answers.noul(f"audience_mismatch_{index}"),
                        }
                        judged.append(
                            {
                                **placement,
                                "breaches": breaches,
                                "rule_hit": max(breaches.values()) if breaches else 0.0,
                                "hazards": hazards,
                                "suitability": answers.score(f"suitability_{index}"),
                                "context": answers.choice(f"context_{index}"),
                                "context_confidence": answers.confidence(f"context_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in judged:
        item["action"] = decide(item["rule_hit"], item["hazards"], item["suitability"], gates)
        if item["context_confidence"] < gates["confidence"]:
            item["context"] = "unclear"

    counts = Counter(item["action"] for item in judged)
    columns = st.columns(4)
    columns[0].metric("Placements", len(judged))
    columns[1].metric("Run", counts["run"])
    columns[2].metric("Blocked", counts["block"])
    columns[3].metric("To a human", counts["human_review"])

    st.subheader("🧑 Human review queue")
    st.caption("A hazard, a grey-band rule, or damage your rule set does not cover. Never auto-actioned.")
    queue = sorted([i for i in judged if i["action"] == "human_review"], key=lambda i: -i["suitability"])
    if not queue:
        st.info("Empty this run. An always-empty queue means the gates are too wide, not that the plan is safe.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['page_title']}** · {item['site']}")
            st.write(item["page_summary"][:400])
            fired = ", ".join(f"{k} {v:.2f}" for k, v in item["hazards"].items() if v >= gates["hazard"]) or "no hazard"
            st.caption(
                f"worst rule P={item['rule_hit']:.2f} · damage {item['suitability']:.2f}/3 · "
                f"{fired} · context {item['context']} ({item['context_confidence']:.2f})"
            )

    for label, title in (("block", "🔴 Blocked"), ("reduce", "🟡 Keep serving, throttled"), ("run", "🟢 Run")):
        items = [i for i in judged if i["action"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})", expanded=label == "block"):
            for item in items:
                top = sorted(item["breaches"].items(), key=lambda kv: -kv[1])[:3]
                st.markdown(f"**{item['page_title']}** · {item['site']}")
                st.caption(
                    f"damage {item['suitability']:.2f}/3 · rules: "
                    + ", ".join(f"{rid} {p:.2f}" for rid, p in top)
                )

    st.subheader("Which exclusion rules are doing the work")
    st.caption("Counted in Python from the Nouls. A rule that never fires is one to rewrite.")
    firing = Counter()
    for item in judged:
        for rid, probability in item["breaches"].items():
            if probability >= gates["rule"]:
                firing[rid] += 1
    st.bar_chart({rule["id"]: firing.get(rule["id"], 0) for rule in rules})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(judged)} pairings in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(judged), 1):.8f} per placement). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
