"""Competitor Monitor -- which competitor updates actually matter to YOUR roadmap.

A competitive-intelligence feed is a firehose of changelogs, funding news,
partnership announcements and blog posts. Almost none of it should change what
you build. The job is not summarising it -- it is throwing nearly all of it away
with a reason.

So this is a relevance filter, and relevance is relative to a roadmap the user
writes as prose. The same item is a five-alarm fire for one team and noise for
another, which is exactly why a generic "importance" label is useless and why
the roadmap goes into the state.

The app reports its suppression ratio, because a filter that keeps 80% of the
firehose has not done anything.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_updates.json"

DEFAULT_ROADMAP = (
    "We sell a data-quality platform to analytics teams. The next two quarters are: "
    "(1) column-level lineage across dbt and Airflow, (2) an anomaly-detection engine "
    "that learns per-column baselines without configuration, (3) native Snowflake and "
    "BigQuery push-down so checks run in the warehouse rather than pulling data out, "
    "and (4) a free tier aimed at individual analytics engineers. We are not building "
    "a catalog, we are not building BI, and we have deliberately decided against "
    "on-prem deployment. Our pricing is per-warehouse-credit and we are nervous about "
    "anyone moving to seat-based or free-forever models."
)

RELEVANCE_LEVELS = [
    "The update has nothing to do with anything the roadmap describes",
    "The update is in the same market but touches none of the named roadmap items",
    "The update relates to an area the roadmap names but is not the same capability",
    "The update directly addresses a capability the roadmap says is being built",
    "The update directly addresses a named roadmap capability and appears to ship it first",
]

THREAT_LEVELS = [
    "Nothing about this changes what a buyer would ask us",
    "A buyer might mention this in passing during an evaluation",
    "A buyer in an active evaluation would ask us to respond to this",
    "This would become a standing objection in every deal until we answer it",
]

SIGNALS = {
    "shipped": (
        "Does this update describe something that is available to customers now, rather than announced, previewed, or planned?",
        "It is described as generally available, released, or in customers' hands",
        "It is a preview, beta, waitlist, roadmap item, or announcement of future work",
    ),
    "pricing_move": (
        "Does this update change what the competitor charges or how they charge for it?",
        "A price, a packaging change, a free tier, or a new charging model is described",
        "No change to price or packaging is described",
    ),
    "our_stated_gap": (
        "Does this update address something the roadmap explicitly says we are NOT doing?",
        "It covers an area the roadmap names as deliberately out of scope",
        "It does not touch anything the roadmap rules out",
    ),
    "customer_visible": (
        "Would an existing customer of ours notice this on their own?",
        "It would appear in their workflow, their bill, or their vendor's product in a visible way",
        "Only someone tracking this market closely would notice",
    ),
    "substance": (
        "Does this update describe a concrete product or business change, as opposed to marketing content?",
        "A specific capability, price, deal, or organisational change is described",
        "It is a blog post, opinion piece, webinar, award, or general positioning",
    ),
}

UPDATE_TYPES = {
    "product_release": "A new capability or product is described as available",
    "product_announcement": "A capability is announced, previewed, or promised for later",
    "pricing_change": "Pricing, packaging, or plan structure changes",
    "funding_or_mna": "A funding round, acquisition, or merger",
    "partnership": "A partnership, integration, or reseller arrangement",
    "personnel": "A hire, departure, or reorganisation",
    "marketing_content": "A blog post, report, webinar, award, or event",
    "other": "None of the above describes this update",
}


def build_questions(updates: list[dict], roadmap: str) -> dict:
    """Two Scores, five Nouls and one Choice per update, in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(updates)):
        reference = {
            "competitor": f"`updates[{index}].competitor`",
            "headline": f"`updates[{index}].headline`",
            "detail": f"`updates[{index}].detail`",
        }
        questions[f"relevance__{index}"] = score(
            {
                "task": "How directly does this competitor update bear on the roadmap below?",
                "our_roadmap": roadmap,
                "update": reference,
            },
            RELEVANCE_LEVELS,
        )
        questions[f"threat__{index}"] = score(
            {
                "task": "How much would this update change what a prospect asks us in a deal?",
                "our_roadmap": roadmap,
                "update": reference,
            },
            THREAT_LEVELS,
        )
        for key, (task, yes, no) in SIGNALS.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "our_roadmap": roadmap, "update": reference}, true=yes, false=no
            )
        questions[f"type__{index}"] = choice(
            {"task": "What kind of update is this?", "update": reference}, UPDATE_TYPES
        )
    return questions


def triage(
    judgment: dict, relevance_at: float, threat_at: float, substance_at: float
) -> tuple[str, str]:
    """Policy in Python. 'Noise' is the expected outcome for most of a firehose."""
    if judgment["substance"] < substance_at:
        return "noise", "no concrete product or business change described"
    if judgment["relevance"] >= relevance_at and judgment["shipped"] >= 0.5:
        return "act", "directly on a roadmap capability, and shipped"
    if judgment["relevance"] >= relevance_at:
        return "watch", "on a roadmap capability but not yet in customers' hands"
    if judgment["threat"] >= threat_at or judgment["pricing_move"] >= 0.7:
        return "watch", "would change the conversation in a live deal"
    return "noise", "not on the roadmap and not deal-changing"


def brief(frame: pd.DataFrame) -> pd.DataFrame:
    """The weekly output: what to act on, grouped by competitor. pandas only."""
    acting = frame[frame["verdict"] != "noise"]
    if acting.empty:
        return pd.DataFrame(columns=["competitor", "items", "top relevance"])
    table = acting.groupby("competitor").agg(
        items=("headline", "size"),
        top_relevance=("relevance", "max"),
    )
    return table.sort_values("top_relevance", ascending=False).round(2)


def main() -> None:
    st.set_page_config(page_title="Competitor Monitor", page_icon="🔭", layout="wide")
    st.title("🔭 Competitor Monitor")
    st.caption("A relevance filter over the firehose. Relevance is relative to your roadmap.")

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
        st.caption("Re-filtering from stored judgments makes no API calls.")
        relevance_at = st.slider("On the roadmap at relevance ≥", 0.0, 4.0, 2.6, 0.1)
        threat_at = st.slider("Deal-changing at threat ≥", 0.0, 3.0, 2.0, 0.1)
        substance_at = st.slider("Discard below P(substance)", 0.0, 0.9, 0.40, 0.05)

    roadmap = st.text_area("Your roadmap and your fears, in prose", value=DEFAULT_ROADMAP, height=170)
    upload = st.file_uploader(
        "Updates (JSON list of {competitor, headline, detail, source})", type=["json"]
    )
    updates = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Filter {len(updates)} updates", type="primary"):
        questions = build_questions(updates, roadmap.strip())
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"updates": updates, "our_roadmap": roadmap}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["filtered"] = {
            "updates": updates,
            "judgments": [
                {
                    "relevance": answers.score(f"relevance__{i}"),
                    "threat": answers.score(f"threat__{i}"),
                    **{k: answers.noul(f"{k}__{i}") for k in SIGNALS},
                    "type": answers.choice(f"type__{i}"),
                }
                for i in range(len(updates))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    filtered = st.session_state.get("filtered")
    if not filtered:
        st.info(f"{len(updates)} sample updates bundled — mostly noise, by design.")
        return

    rows = []
    for update, judgment in zip(filtered["updates"], filtered["judgments"]):
        verdict, reason = triage(judgment, relevance_at, threat_at, substance_at)
        rows.append(
            {
                "verdict": verdict,
                "competitor": update["competitor"],
                "headline": update["headline"],
                "type": judgment["type"],
                "relevance": round(judgment["relevance"], 2),
                "threat": round(judgment["threat"], 2),
                "why": reason,
                **{k: round(judgment[k], 2) for k in SIGNALS},
                "source": update.get("source", ""),
            }
        )
    frame = pd.DataFrame(rows).sort_values("relevance", ascending=False)

    kept = int((frame["verdict"] != "noise").sum())
    columns = st.columns(4)
    columns[0].metric("Act", int((frame["verdict"] == "act").sum()))
    columns[1].metric("Watch", int((frame["verdict"] == "watch").sum()))
    columns[2].metric("Noise", int((frame["verdict"] == "noise").sum()))
    columns[3].metric("Suppressed", f"{(1 - kept / len(frame)) * 100:.0f}%")

    st.subheader("This week's brief")
    st.dataframe(brief(frame), width="stretch")

    for verdict, title in (
        ("act", "🔴 Act — on a roadmap capability and shipped"),
        ("watch", "🟡 Watch"),
        ("noise", "⚪ Noise — suppressed"),
    ):
        subset = frame[frame["verdict"] == verdict]
        st.subheader(f"{title} ({len(subset)})")
        if subset.empty:
            st.caption("nothing here")
        else:
            st.dataframe(subset.drop(columns=["verdict"]), width="stretch", hide_index=True)

    st.download_button(
        "Download competitor_brief.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="competitor_brief.csv",
        mime="text/csv",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", filtered["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{filtered['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${filtered['cost']:.6f}")
    st.caption(
        f"{filtered['tokens']:,} input tokens for {len(frame)} updates × "
        f"{len(SIGNALS) + 3} judgments. Editing the roadmap re-runs inference; "
        "moving the sliders does not."
    )


if __name__ == "__main__":
    main()
