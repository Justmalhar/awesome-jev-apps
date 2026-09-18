"""Review Authenticity -- incentivised, templated, or second-hand reviews.

Fake reviews are not detected by sentiment. A five-star rave and a paid five-star
rave read identically to a sentiment model, because the thing that separates
them is not tone -- it is whether anyone actually held the product.

What counts as inauthentic is also the operator's call, not a vendor's. Some
marketplaces allow disclosed sampling. Some ban any review written after a
refund. Some only care about template farms. So the authenticity rules live in a
text box, one Noul per rule, and the independent tells -- incentive, template,
no first-hand detail, not-about-the-product -- get their own Nouls beside them
because a review can show any combination of them at once.

Nothing here is proof. It is a queue for the people who investigate.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_reviews.json"
SAMPLE_RULES = HERE / "sample_rules.txt"
TEXT_CHARS = 1500

# How much the text reads like someone who actually used the thing. Ordered,
# and each level is something a human investigator would recognise on sight.
FIRSTHAND_LEVELS = [
    "Nothing specific at all: praise or complaint that would fit any product in any category",
    "Generic but plausible: mentions the product type and a broad impression, no detail only an owner would have",
    "Some real detail: names a feature, a setting, or a moment of use that matches this particular product",
    "Unmistakably lived: a specific situation, an unexpected quirk, or a fault discovered over time",
]

# What the review is mostly about. `unclear` is the mandatory escape hatch.
SUBJECT_KINDS = {
    "product_experience": "Using the product itself: how it performs, fits, feels, or fails.",
    "delivery_or_packaging": "Shipping speed, courier behaviour, or the state of the box on arrival.",
    "seller_service": "The seller's communication, refund handling, or customer support.",
    "price_or_value": "Almost entirely about the price, a discount, or whether it was worth the money.",
    "promotion": "Directs the reader somewhere else — another product, a code, a channel, or a shop.",
    "unclear": "Too short or too vague to tell what it is about.",
}

DEFAULT_GATES = {
    "rule": 0.65,       # P(authenticity rule broken) to count the rule as fired
    "tell": 0.6,        # P(tell present) to flag one of the independent tells
    "suppress": 2,      # number of tells flagged at or above which we suppress
    "firsthand": 1.0,   # below this, the review carries no first-hand detail
    "confidence": 0.5,  # Choice confidence below which the subject label is dropped
}


def parse_rules(text: str) -> list[dict]:
    """`A3: Disclose free samples` -> {"id": "A3", "text": "Disclose free samples"}."""
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
            rules.append({"id": f"A{len(rules) + 1}", "text": line})
    return rules


def build_questions(reviews: list[dict], rules: list[dict]) -> dict:
    """One Noul per operator rule, plus four independent tells and two summaries."""
    questions: dict[str, dict] = {}
    for index in range(len(reviews)):
        reference = {
            "product": f"`reviews[{index}].product`",
            "rating": f"`reviews[{index}].rating`",
            "title": f"`reviews[{index}].title`",
            "text": f"`reviews[{index}].text`",
            "verified_purchase": f"`reviews[{index}].verified_purchase`",
        }
        for position, rule in enumerate(rules):
            questions[f"rule_{index}_{position}"] = noul(
                {
                    "task": "Does this review break the review-authenticity rule quoted below?",
                    "rule": f"`rules[{position}].text`",
                    "review": reference,
                },
                true="The review plainly does the thing this rule forbids",
                false="It does not do that thing, or the rule governs something else",
            )
        # Four tells that can each be present independently of the others. One
        # Noul each, because a review can be incentivised without being
        # templated, and templated without being incentivised -- and the
        # operator will want a different threshold on each.
        questions[f"incentivised_{index}"] = noul(
            {"task": "Does the review indicate the writer was given something in exchange for writing it?",
             "review": reference},
            true="Mentions a free or discounted unit, a gift card, an entry into a draw, a refund, or a seller's request to review",
            false="No sign of any benefit received in exchange for the review",
        )
        questions[f"templated_{index}"] = noul(
            {"task": "Does the wording read as assembled from a template rather than written by one customer?",
             "review": reference},
            true="Marketing cadence, feature-bullet phrasing, or stock superlatives with no personal voice",
            false="Written in an individual voice, however brief or badly punctuated",
        )
        questions[f"second_hand_{index}"] = noul(
            {"task": "Does the review describe something other than the writer's own use of the product?",
             "review": reference},
            true="Relays what someone else said, quotes the listing, speculates before arrival, or reviews a product the writer has not opened",
            false="Describes the writer's own hands-on experience of the product",
        )
        questions[f"solicited_burst_{index}"] = noul(
            {"task": "Does the review's phrasing match a coordinated campaign rather than an individual customer?",
             "review": reference},
            true="Repeats a promotional phrase, a hashtag, a seller's slogan, or a talking point that reads as supplied",
            false="Nothing in the wording suggests supplied talking points",
        )
        questions[f"firsthand_{index}"] = score(
            {"task": "How much detail is here that only someone who used this product would have?",
             "review": reference},
            FIRSTHAND_LEVELS,
        )
        questions[f"subject_{index}"] = choice(
            {"task": "What is this review mostly about?", "review": reference}, SUBJECT_KINDS
        )
    return questions


def decide(rule_hit: float, tells: dict, firsthand: float, gates: dict) -> str:
    """Route one review. Pure, so gates can be retuned with no inference.

    Nothing is suppressed on tells alone unless several agree AND the text has
    no first-hand detail -- a single tell on a specific, lived review is far
    more likely to be a false positive than a review farm.
    """
    flagged = [name for name, probability in tells.items() if probability >= gates["tell"]]
    if rule_hit >= gates["rule"]:
        return "human_review"
    if len(flagged) >= gates["suppress"] and firsthand < gates["firsthand"]:
        return "suppress"
    if flagged:
        return "label"
    return "publish"


def main() -> None:
    st.set_page_config(page_title="Review Authenticity", page_icon="⭐", layout="wide")
    st.title("⭐ Review Authenticity")
    st.caption("Incentivised, templated, or second-hand — judged against your own review rules.")

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
            "rule": st.slider("P(your rule broken) to escalate", 0.0, 1.0, DEFAULT_GATES["rule"], 0.05),
            "tell": st.slider("P(tell present) to flag it", 0.0, 1.0, DEFAULT_GATES["tell"], 0.05),
            "suppress": st.slider("Tells that must agree before suppressing", 1, 4, DEFAULT_GATES["suppress"], 1),
            "firsthand": st.slider("First-hand detail below which suppression is allowed", 0.0, 3.0, DEFAULT_GATES["firsthand"], 0.1),
            "confidence": st.slider("Confidence to trust the subject label", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
        }
        batch_size = st.slider("Reviews per request", 2, 30, 10, 1)

    rules_text = st.text_area(
        "Your review-authenticity rules — edit these, the questions follow",
        value=SAMPLE_RULES.read_text(encoding="utf-8") if SAMPLE_RULES.is_file() else "",
        height=190,
    )
    rules = parse_rules(rules_text)
    if not rules:
        st.warning("Write at least one rule.")
        return

    uploaded = st.file_uploader(
        "Reviews JSON: [{product, rating, title, text, verified_purchase}]", type=["json"]
    )
    if uploaded:
        reviews = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        reviews = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(reviews)} reviews.")
    else:
        st.warning("Upload a reviews JSON.")
        return

    for review in reviews:
        review["text"] = str(review.get("text", ""))[:TEXT_CHARS]

    if not st.button(f"Check {len(reviews)} reviews", type="primary"):
        return

    groups = [reviews[i : i + batch_size] for i in range(0, len(reviews), batch_size)]
    checked: list[dict] = []
    cost = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Applying {len(rules)} rules in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, rules)
                    answers = client.ask({"rules": rules, "reviews": group}, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, review in enumerate(group):
                        broken = {
                            rule["id"]: answers.noul(f"rule_{index}_{position}")
                            for position, rule in enumerate(rules)
                        }
                        tells = {
                            "incentivised": answers.noul(f"incentivised_{index}"),
                            "templated": answers.noul(f"templated_{index}"),
                            "second_hand": answers.noul(f"second_hand_{index}"),
                            "campaign_phrasing": answers.noul(f"solicited_burst_{index}"),
                        }
                        checked.append(
                            {
                                **review,
                                "broken": broken,
                                "rule_hit": max(broken.values()) if broken else 0.0,
                                "tells": tells,
                                "firsthand": answers.score(f"firsthand_{index}"),
                                "subject": answers.choice(f"subject_{index}"),
                                "subject_confidence": answers.confidence(f"subject_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in checked:
        item["action"] = decide(item["rule_hit"], item["tells"], item["firsthand"], gates)
        if item["subject_confidence"] < gates["confidence"]:
            item["subject"] = "unclear"

    counts = Counter(item["action"] for item in checked)
    columns = st.columns(4)
    columns[0].metric("Reviews", len(checked))
    columns[1].metric("Published", counts["publish"])
    columns[2].metric("Labelled", counts["label"])
    columns[3].metric("To a human", counts["human_review"] + counts["suppress"])

    st.subheader("🧑 Human review queue")
    st.caption("Your own rules fired here. Suppressed items are listed below and also need a person before anything is removed.")
    queue = sorted([i for i in checked if i["action"] == "human_review"], key=lambda i: -i["rule_hit"])
    if not queue:
        st.info("Empty this run. An always-empty queue means the gates are too wide.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['title']}** — {item['rating']}★ on _{item['product']}_")
            st.write(item["text"])
            fired = ", ".join(f"{rid} {p:.2f}" for rid, p in sorted(item["broken"].items(), key=lambda kv: -kv[1])[:3])
            st.caption(
                f"rules: {fired} · first-hand detail {item['firsthand']:.2f}/3 · "
                f"about: {item['subject']} ({item['subject_confidence']:.2f})"
            )

    for label, title in (("suppress", "🔴 Proposed suppressions"), ("label", "🟡 Publish with a disclosure label"), ("publish", "🟢 Published")):
        items = [i for i in checked if i["action"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})", expanded=label == "suppress"):
            for item in items:
                shown = ", ".join(f"{k} {v:.2f}" for k, v in item["tells"].items() if v >= gates["tell"]) or "no tell"
                st.markdown(f"**{item['title']}** — {item['rating']}★ · {item['text'][:150]}")
                st.caption(f"{shown} · first-hand {item['firsthand']:.2f}/3")

    st.subheader("Which tells are firing")
    st.caption("Counted in Python from the Nouls — never asked of the model.")
    firing = Counter()
    for item in checked:
        for name, probability in item["tells"].items():
            if probability >= gates["tell"]:
                firing[name] += 1
    st.bar_chart({name: firing.get(name, 0) for name in ("incentivised", "templated", "second_hand", "campaign_phrasing")})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(checked)} reviews in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(checked), 1):.8f} per review). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
