"""Listing Policy -- your marketplace's own prohibited-items list, applied.

Marketplace policy is never generic. It is a page of clauses your legal and
trust teams wrote, specific to the categories you actually sell in, amended
every time a regulator or a payment processor asks. "Is this listing bad?" is
not a question anyone in that building has. "Does this listing breach clause 7?"
is.

So clause 7 lives in a text box. One Noul per policy clause, independent hazard
Nouls beside them for the signals that cut across every clause (counterfeit
tells, off-platform payment, condition that does not match the photos), and the
enforcement decision in Python.

This assists human reviewers. Delisting a seller's inventory is a decision that
affects someone's income -- it needs a person.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_listings.json"
SAMPLE_POLICY = HERE / "sample_policy.txt"
DESC_CHARS = 1500

# What a buyer stands to lose if the listing is exactly what it looks like.
# Ordered, and each level is something a reviewer could point at.
EXPOSURE_LEVELS = [
    "Nothing at stake: an ordinary item, honestly described, that arrives as pictured",
    "Disappointment only: the item is real but oversold, so the buyer wants a refund and gets one",
    "Money gone: the buyer pays outside the platform's protection, or receives a fake they cannot return",
    "Physical or legal harm: a recalled, unsafe, restricted, or stolen item reaching a household",
]

# The restricted family this listing sits in, if any. `none_apply` is
# mandatory -- a Choice always returns something, and most listings are fine.
RESTRICTED_FAMILIES = {
    "recalled_unsafe": "Subject to a safety recall, or an item type known to be unsafe to ship or resell.",
    "regulated_goods": "Requires a licence, age check, or prescription to sell lawfully in most markets.",
    "replica_branded": "Presented as, or strongly implied to be, a branded item the seller cannot source legitimately.",
    "digital_account": "An account, licence key, subscription, or in-game asset rather than a physical good.",
    "recovered_stolen": "The description suggests the seller does not have clean title to the item.",
    "none_apply": "An ordinary item that belongs in none of these restricted families.",
}

DEFAULT_GATES = {
    "breach": 0.65,     # P(clause breached) at or above which the clause counts
    "uncertain": 0.35,  # below this the clause is clean
    "remove": 2.4,      # exposure at or above which enforcement is removal
    "hazard": 0.6,      # P(hazard) at or above which the hazard is flagged
    "confidence": 0.5,  # Choice confidence below which a family label is not trusted
}


def parse_policy(text: str) -> list[dict]:
    """`P7: No recalled goods` -> {"id": "P7", "text": "No recalled goods"}."""
    clauses: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, body = line.partition(":")
        head, body = head.strip(), body.strip()
        # An id must carry a digit ("R4", "3.1", "P7"). Otherwise a line like
        # "Warning: be civil" would have "Warning" stolen as its id.
        if sep and body and " " not in head and len(head) <= 12 and any(c.isdigit() for c in head):
            clauses.append({"id": head, "text": body})
        else:
            clauses.append({"id": f"P{len(clauses) + 1}", "text": line})
    return clauses


def build_questions(listings: list[dict], clauses: list[dict]) -> dict:
    """One Noul per clause per listing, plus three hazards that cut across them."""
    questions: dict[str, dict] = {}
    for index in range(len(listings)):
        reference = {
            "title": f"`listings[{index}].title`",
            "category": f"`listings[{index}].category`",
            "price": f"`listings[{index}].price`",
            "description": f"`listings[{index}].description`",
            "seller": f"`listings[{index}].seller`",
        }
        for position, clause in enumerate(clauses):
            questions[f"clause_{index}_{position}"] = noul(
                {
                    "task": "Does this listing breach the marketplace policy clause quoted below?",
                    "clause": f"`policy[{position}].text`",
                    "listing": reference,
                },
                true="The listing does the thing this clause forbids, on the face of what is written",
                false="The listing does not do that thing, or the clause governs something else",
            )
        # Three hazards that can each be true independently of every clause and
        # of each other. Separate Nouls so each gets its own gate -- an
        # off-platform payment request and a counterfeit tell need different
        # enforcement, and a rubric would force them onto one dial.
        questions[f"counterfeit_{index}"] = noul(
            {"task": "Does this listing show the tells of a counterfeit of a branded item?",
             "listing": reference},
            true="Branded item at an implausible price, hedging words like 'inspired by' or 'unbranded original', or no box, tags, or serial where a genuine one would have them",
            false="Either not a branded item at all, or presented in a way consistent with a genuine one",
        )
        questions[f"off_platform_{index}"] = noul(
            {"task": "Does this listing push the buyer to pay or communicate outside the marketplace?",
             "listing": reference},
            true="Asks for direct bank transfer, gift cards, a messaging app, or contact details to complete the sale elsewhere",
            false="The sale is expected to complete through the marketplace's own checkout and messaging",
        )
        questions[f"mismatch_{index}"] = noul(
            {"task": "Does the stated condition or specification contradict something else in the listing?",
             "listing": reference},
            true="The description, title, category, or price tell inconsistent stories about what is actually being sold",
            false="Title, category, condition, and description all describe the same item",
        )
        questions[f"exposure_{index}"] = score(
            {"task": "If this listing is exactly what it appears to be, what does the buyer stand to lose?",
             "listing": reference},
            EXPOSURE_LEVELS,
        )
        questions[f"family_{index}"] = choice(
            {"task": "Which restricted family does this item belong to, if any?", "listing": reference},
            RESTRICTED_FAMILIES,
        )
    return questions


def decide(breach: float, hazards: dict, exposure: float, gates: dict) -> str:
    """Route one listing. Pure -- retune the gates without spending a token."""
    flagged = [name for name, probability in hazards.items() if probability >= gates["hazard"]]
    if breach >= gates["breach"]:
        return "remove" if exposure >= gates["remove"] else "human_review"
    if flagged:
        # A hazard with no clause behind it is a reviewer's call, not a removal:
        # the policy is the authority, the hazards are only evidence.
        return "human_review"
    if breach >= gates["uncertain"]:
        return "human_review"
    return "approve"


def main() -> None:
    st.set_page_config(page_title="Listing Policy", page_icon="🏷️", layout="wide")
    st.title("🏷️ Listing Policy")
    st.caption("Your prohibited-items policy, clause by clause, over a listing queue.")

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
            "breach": st.slider("P(clause breached) to act", 0.0, 1.0, DEFAULT_GATES["breach"], 0.05),
            "uncertain": st.slider("P(clause breached) below which it is clean", 0.0, 1.0, DEFAULT_GATES["uncertain"], 0.05),
            "remove": st.slider("Buyer exposure to remove rather than escalate", 0.0, 3.0, DEFAULT_GATES["remove"], 0.1),
            "hazard": st.slider("P(hazard) to flag", 0.0, 1.0, DEFAULT_GATES["hazard"], 0.05),
            "confidence": st.slider("Confidence to trust the restricted-family label", 0.0, 1.0, DEFAULT_GATES["confidence"], 0.05),
        }
        batch_size = st.slider("Listings per request", 2, 25, 8, 1)

    policy_text = st.text_area(
        "Your marketplace policy — edit these clauses, the questions follow",
        value=SAMPLE_POLICY.read_text(encoding="utf-8") if SAMPLE_POLICY.is_file() else "",
        height=220,
    )
    clauses = parse_policy(policy_text)
    if not clauses:
        st.warning("Write at least one policy clause.")
        return

    uploaded = st.file_uploader("Listings JSON: [{title, category, price, description, seller}]", type=["json"])
    if uploaded:
        listings = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        listings = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(listings)} listings.")
    else:
        st.warning("Upload a listings JSON.")
        return

    for listing in listings:
        listing["description"] = str(listing.get("description", ""))[:DESC_CHARS]

    st.caption(f"{len(clauses)} clauses × {len(listings)} listings, plus 5 judgments each.")
    if not st.button(f"Check {len(listings)} listings", type="primary"):
        return

    groups = [listings[i : i + batch_size] for i in range(0, len(listings), batch_size)]
    checked: list[dict] = []
    cost = tokens = elapsed = asked = 0.0

    try:
        with st.spinner(f"Applying {len(clauses)} clauses in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group, clauses)
                    answers = client.ask({"policy": clauses, "listings": group}, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, listing in enumerate(group):
                        breaches = {
                            clause["id"]: answers.noul(f"clause_{index}_{position}")
                            for position, clause in enumerate(clauses)
                        }
                        hazards = {
                            "counterfeit": answers.noul(f"counterfeit_{index}"),
                            "off_platform_payment": answers.noul(f"off_platform_{index}"),
                            "condition_mismatch": answers.noul(f"mismatch_{index}"),
                        }
                        checked.append(
                            {
                                **listing,
                                "breaches": breaches,
                                "breach": max(breaches.values()) if breaches else 0.0,
                                "hazards": hazards,
                                "exposure": answers.score(f"exposure_{index}"),
                                "family": answers.choice(f"family_{index}"),
                                "family_confidence": answers.confidence(f"family_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    for item in checked:
        item["action"] = decide(item["breach"], item["hazards"], item["exposure"], gates)
        if item["family_confidence"] < gates["confidence"]:
            item["family"] = "none_apply"

    counts = Counter(item["action"] for item in checked)
    columns = st.columns(4)
    columns[0].metric("Listings", len(checked))
    columns[1].metric("Approved", counts["approve"])
    columns[2].metric("Proposed removals", counts["remove"])
    columns[3].metric("To a human", counts["human_review"])

    st.subheader("🧑 Human review queue")
    st.caption("A hazard without a clause behind it, or a clause in the grey band. Never auto-actioned.")
    queue = sorted([i for i in checked if i["action"] == "human_review"], key=lambda i: -i["exposure"])
    if not queue:
        st.info("Empty this run. An always-empty queue means your gates are too wide, not that the queue works.")
    for item in queue:
        with st.container(border=True):
            st.markdown(f"**{item['title']}** — {item['price']} · seller `{item['seller']}`")
            st.write(item["description"][:400])
            fired = ", ".join(f"{k} {v:.2f}" for k, v in item["hazards"].items() if v >= gates["hazard"]) or "no hazard"
            st.caption(
                f"worst clause P={item['breach']:.2f} · buyer exposure {item['exposure']:.2f}/3 · "
                f"{fired} · family {item['family']} ({item['family_confidence']:.2f})"
            )

    for label, title in (("remove", "🔴 Proposed removals"), ("approve", "🟢 Approved")):
        items = [i for i in checked if i["action"] == label]
        if not items:
            continue
        with st.expander(f"{title} ({len(items)})", expanded=label == "remove"):
            for item in items:
                top = sorted(item["breaches"].items(), key=lambda kv: -kv[1])[:3]
                st.markdown(f"**{item['title']}** — {item['price']}")
                st.caption(
                    f"exposure {item['exposure']:.2f}/3 · clauses: "
                    + ", ".join(f"{cid} {p:.2f}" for cid, p in top)
                )

    st.subheader("Which clauses are carrying the load")
    st.caption("Counted in Python from the Nouls. A clause that never fires is one to rewrite or retire.")
    firing = Counter()
    for item in checked:
        for cid, probability in item["breaches"].items():
            if probability >= gates["breach"]:
                firing[cid] += 1
    st.bar_chart({clause["id"]: firing.get(clause["id"], 0) for clause in clauses})

    st.divider()
    st.caption(
        f"{int(asked)} questions over {len(checked)} listings in {len(groups)} request(s) · "
        f"{int(tokens):,} input tokens · ${cost:.6f} · {elapsed:.2f}s "
        f"(${cost / max(len(checked), 1):.8f} per listing). Measured, not estimated."
    )


if __name__ == "__main__":
    main()
