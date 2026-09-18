"""Paper Screener -- title/abstract screening for systematic reviews.

Screening is the part of a systematic review that eats weeks: two humans read
every abstract against the protocol's inclusion and exclusion criteria, and
disagreements go to a third. Most abstracts are obvious rejects.

The design point worth stealing: this does NOT ask "should we include this
paper?" as one question. Each criterion is its own Noul, and the protocol is
applied in Python:

    include  <=>  every inclusion criterion holds  AND  no exclusion criterion fires

That is a conjunctive rule with a veto, not a weighted average. Averaging would
let three strong inclusions outvote a fatal exclusion -- which is exactly the
bug a reviewer would be fired for. Weighted scores suit compensating
preferences; "any serious violation disqualifies" needs separate conditions.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_abstracts.json"

DEFAULT_PROTOCOL = {
    "question": "Does structured exercise reduce HbA1c in adults with type 2 diabetes?",
    "include": [
        "The study enrolls adults (18+) diagnosed with type 2 diabetes.",
        "The intervention is a structured, supervised exercise programme.",
        "The study reports HbA1c as an outcome measure.",
        "The design is a randomised controlled trial.",
    ],
    "exclude": [
        "The study population is exclusively type 1 diabetes or gestational diabetes.",
        "The paper is a review, meta-analysis, protocol, editorial, or commentary rather than primary research.",
        "The study is conducted in animals rather than humans.",
    ],
}


def build_questions(papers: list[dict], protocol: dict) -> dict:
    """One Noul per criterion per paper. Independent labels stay independent."""
    questions: dict[str, dict] = {}
    for index in range(len(papers)):
        reference = {
            "title": f"`papers[{index}].title`",
            "abstract": f"`papers[{index}].abstract`",
        }
        for c_index, criterion in enumerate(protocol["include"]):
            questions[f"inc_{index}_{c_index}"] = noul(
                {
                    "task": "Does the paper below satisfy this inclusion criterion?",
                    "criterion": criterion,
                    "paper": reference,
                },
                true="The abstract states or clearly implies this is satisfied",
                false="The abstract contradicts this, or gives no indication it holds",
            )
        for c_index, criterion in enumerate(protocol["exclude"]):
            questions[f"exc_{index}_{c_index}"] = noul(
                {
                    "task": "Does the paper below trigger this exclusion criterion?",
                    "criterion": criterion,
                    "paper": reference,
                },
                true="The abstract shows this exclusion applies",
                false="This exclusion does not apply",
            )
    return questions


def decide(
    inclusions: list[float], exclusions: list[float], accept: float, reject: float
) -> tuple[str, str]:
    """Apply the protocol. Jev supplied evidence; this is the policy.

    Three outcomes, because a two-way forced choice is what makes automated
    screening untrustworthy. Anything not clear-cut goes to a human.
    """
    fired = [i for i, p in enumerate(exclusions) if p >= accept]
    if fired:
        return "exclude", f"exclusion #{fired[0] + 1} fired (P={exclusions[fired[0]]:.2f})"

    failed = [i for i, p in enumerate(inclusions) if p <= reject]
    if failed:
        return "exclude", f"inclusion #{failed[0] + 1} not met (P={inclusions[failed[0]]:.2f})"

    if all(p >= accept for p in inclusions) and all(p <= reject for p in exclusions):
        return "include", "all inclusions met, no exclusions fired"

    weakest = min(inclusions) if inclusions else 0.0
    return "review", f"borderline (weakest inclusion P={weakest:.2f})"


def main() -> None:
    st.set_page_config(page_title="Paper Screener", page_icon="📄", layout="wide")
    st.title("📄 Paper Screener")
    st.caption("Title/abstract screening against a systematic review protocol.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Decision thresholds")
        st.caption("Tune on a labelled pilot set. These are not universal.")
        accept = st.slider("Treat P(yes) at or above this as YES", 0.5, 0.99, 0.75, 0.01)
        reject = st.slider("Treat P(yes) at or below this as NO", 0.01, 0.5, 0.25, 0.01)
        st.caption("Anything between the two lands in the human pile.")

    protocol_text = st.text_area(
        "Protocol (JSON)",
        value=json.dumps(DEFAULT_PROTOCOL, indent=2),
        height=260,
    )
    try:
        protocol = json.loads(protocol_text)
        assert protocol.get("include") and protocol.get("exclude")
    except (json.JSONDecodeError, AssertionError) as exc:
        st.error(f"Protocol must be JSON with non-empty 'include' and 'exclude' lists: {exc}")
        return

    uploaded = st.file_uploader("Abstracts (JSON list of {title, abstract})", type=["json"])
    if uploaded:
        papers = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        papers = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(papers)} abstracts.")
    else:
        st.warning("Upload a JSON list of abstracts.")
        return

    if not st.button(f"Screen {len(papers)} abstracts", type="primary"):
        return

    questions = build_questions(papers, protocol)
    per_paper = len(protocol["include"]) + len(protocol["exclude"])

    try:
        with st.spinner(f"{len(questions)} criterion judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"papers": papers}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    piles: dict[str, list] = {"include": [], "exclude": [], "review": []}
    for index, paper in enumerate(papers):
        inclusions = [answers.noul(f"inc_{index}_{c}") for c in range(len(protocol["include"]))]
        exclusions = [answers.noul(f"exc_{index}_{c}") for c in range(len(protocol["exclude"]))]
        verdict, reason = decide(inclusions, exclusions, accept, reject)
        piles[verdict].append((paper, inclusions, exclusions, reason))

    columns = st.columns(4)
    columns[0].metric("Include", len(piles["include"]))
    columns[1].metric("Exclude", len(piles["exclude"]))
    columns[2].metric("Needs a human", len(piles["review"]))
    auto = len(papers) - len(piles["review"])
    columns[3].metric("Auto-resolved", f"{auto / len(papers) * 100:.0f}%")

    st.caption(
        f"A human now reads {len(piles['review'])} abstracts instead of {len(papers)}. "
        "Screening is usually done in duplicate, so that is double the saving in practice."
    )

    tabs = st.tabs([f"Include ({len(piles['include'])})",
                    f"Needs a human ({len(piles['review'])})",
                    f"Exclude ({len(piles['exclude'])})"])
    for tab, key in zip(tabs, ("include", "review", "exclude")):
        with tab:
            for paper, inclusions, exclusions, reason in piles[key]:
                with st.container(border=True):
                    st.markdown(f"**{paper['title']}**")
                    st.caption(f"→ {reason}")
                    left, right = st.columns(2)
                    for label, values, criteria, column in (
                        ("Inclusion", inclusions, protocol["include"], left),
                        ("Exclusion", exclusions, protocol["exclude"], right),
                    ):
                        column.markdown(f"*{label}*")
                        for value, criterion in zip(values, criteria):
                            mark = "✅" if value >= accept else ("❌" if value <= reject else "⚠️")
                            column.caption(f"{mark} {value:.2f} — {criterion[:70]}")
                    with st.expander("Abstract"):
                        st.write(paper["abstract"])

    st.divider()
    st.caption(
        f"{len(papers)} papers × {per_paper} criteria = {len(questions)} judgments, "
        f"1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
