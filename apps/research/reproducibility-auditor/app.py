"""Reproducibility Auditor -- could someone else actually rebuild this result?

Reproducibility checklists (NeurIPS, ACM badging, the ML Reproducibility
Checklist) are answered by authors about their own work, which is the same
conflict of interest that makes reporting gaps so common. Reviewers who try to
apply them independently do it by hand, per paper, and quietly stop.

Three primitives, each doing the job it is actually for:

  * Noul per criterion  -- data available? code available? seeds fixed? These
    co-occur freely and are separately actionable, so they are independent
    conditions, never levels on one scale.
  * ONE Score          -- environment specificity genuinely IS ordered, from
    "no software named" to "a pinned container image". A Score is the right
    primitive precisely because these are degrees of the same thing.
  * ONE Choice         -- how the artifacts are obtained is mutually exclusive
    (open download, registered access, on request, not available), so it is a
    Choice, with an explicit no-match option.

The tier is decided in Python, and the middle tier is the point: an audit that
returns only "reproducible" or "not" is not one anybody trusts.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_papers.json"

# Independent conditions. `mandatory` is policy and lives in the sidebar.
CRITERIA = [
    {
        "key": "data",
        "label": "Underlying data obtainable",
        "question": "Does the paper state where the data used for the reported results can be obtained, or state the specific restriction preventing it?",
        "true": "A repository, accession, download location or a named access procedure is given",
        "false": "The data source is described only in general terms with no way to obtain it",
        "mandatory": True,
    },
    {
        "key": "code",
        "label": "Analysis or model code released",
        "question": "Does the paper state that the code producing the reported results is available, and where to get it?",
        "true": "A repository link, archive, supplementary code bundle or named release is stated",
        "false": "The method is described in prose only, with no released implementation",
        "mandatory": True,
    },
    {
        "key": "preprocessing",
        "label": "Preprocessing and data preparation described",
        "question": "Does the paper describe how the raw data were filtered, cleaned, transformed or split before the reported analysis?",
        "true": "The steps taken between the raw source and the analysed dataset are described concretely",
        "false": "The analysed dataset simply appears, with nothing said about how it was prepared",
        "mandatory": True,
    },
    {
        "key": "settings",
        "label": "Parameter and hyperparameter settings given",
        "question": "Does the paper state the parameter or hyperparameter values used to produce the reported results?",
        "true": "Concrete settings are stated in the text, a table or a linked configuration",
        "false": "Settings are described as tuned or selected without the chosen values being stated",
        "mandatory": True,
    },
    {
        "key": "stochastic",
        "label": "Stochastic variation controlled or reported",
        "question": "Does the paper state how randomness was handled -- fixed seeds, repeated runs, or reported variability across runs?",
        "true": "Seeding, repeated runs, or run-to-run variability of the results is addressed",
        "false": "A stochastic procedure is used and nothing is said about seeds or repetition",
        "mandatory": False,
    },
    {
        "key": "hardware",
        "label": "Compute environment described",
        "question": "Does the paper describe the hardware the reported results were produced on, or the compute budget they required?",
        "true": "Processors, accelerators, memory or runtime are described concretely enough to plan a rerun",
        "false": "Nothing is said about what the work was run on",
        "mandatory": False,
    },
    {
        "key": "evaluation",
        "label": "Evaluation protocol fully specified",
        "question": "Does the paper specify the evaluation protocol -- which split or subset the reported numbers come from, and how the metric was computed?",
        "true": "The evaluated subset and the metric definition are both stated unambiguously",
        "false": "A metric is reported without saying what it was computed over or exactly how",
        "mandatory": True,
    },
    {
        "key": "licence",
        "label": "Reuse terms stated for the artifacts",
        "question": "Does the paper state the terms under which its released data or code may be reused?",
        "true": "A licence, terms of use or an explicit reuse restriction is named for the artifacts",
        "false": "Artifacts are offered with nothing said about what may be done with them",
        "mandatory": False,
    },
]

MANDATORY = [c["key"] for c in CRITERIA if c["mandatory"]]

# Ordered degrees of the SAME property -- which is what makes this a Score and
# not four Nouls. Each level is a situation someone rebuilding could recognise.
ENVIRONMENT_LEVELS = [
    "No software is named beyond the method itself; the whole stack would have to be guessed.",
    "The language or main framework is named, but no versions are given, so a rebuild may silently differ.",
    "The main libraries are named with versions, leaving only incidental dependencies to be inferred.",
    "A pinned environment file, lockfile or container image is referenced that covers the entire stack.",
]

ACCESS_OPTIONS = {
    "open": "The artifacts can be downloaded by anyone with no registration or request step",
    "registered": "The artifacts require an account, credential approval or a signed use agreement",
    "on_request": "The artifacts are obtained by contacting the authors or a named custodian",
    "unavailable": "The paper states the artifacts cannot be shared, or offers no route to them at all",
    "unclear": "The paper does not say enough about artifact access for any of the above to fit",
}


def build_questions(papers: list[dict], criteria: list[dict] = CRITERIA) -> dict:
    """Per paper: one Noul per criterion, one Score, one Choice. All in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(papers)):
        reference = {
            "title": f"`papers[{index}].title`",
            "text": f"`papers[{index}].text`",
        }
        for criterion in criteria:
            questions[f"crit_{index}_{criterion['key']}"] = noul(
                {"task": criterion["question"], "paper": reference},
                true=criterion["true"],
                false=criterion["false"],
            )
        questions[f"env_{index}"] = score(
            {
                "task": "How completely does this paper specify the software environment its results were produced in?",
                "paper": reference,
            },
            ENVIRONMENT_LEVELS,
        )
        questions[f"access_{index}"] = choice(
            {
                "task": "By what route can a reader obtain the data or code this paper reports on?",
                "paper": reference,
            },
            ACCESS_OPTIONS,
        )
    return questions


def classify(probability: float, met_at: float, unmet_at: float) -> str:
    if probability >= met_at:
        return "met"
    if probability <= unmet_at:
        return "unmet"
    return "unclear"


def tier(
    statuses: dict[str, str],
    environment: float,
    access: str,
    mandatory: list[str] = MANDATORY,
    environment_floor: float = 2.0,
) -> tuple[str, str]:
    """Conjunctive with a veto, plus an explicit human tier in the middle.

    Any mandatory criterion clearly unmet blocks outright -- released code does
    not compensate for unobtainable data. Anything less than clear-cut goes to a
    person, because "probably reproducible" is a claim nobody should publish.
    """
    unmet = [key for key in mandatory if statuses.get(key) == "unmet"]
    if unmet:
        return "blocked", f"{unmet[0]} is not met, and it is mandatory"
    if access == "unavailable":
        return "blocked", "the paper offers no route to its artifacts"

    ambiguous = [key for key in mandatory if statuses.get(key) != "met"]
    if ambiguous:
        return "human", f"{ambiguous[0]} could not be resolved from the text"
    if access == "unclear":
        return "human", "artifact access route is not stated clearly"
    if environment < environment_floor:
        return "human", f"environment specified only to level {environment:.1f}"
    return "likely", "every mandatory criterion met and the environment is pinned"


def main() -> None:
    st.set_page_config(page_title="Reproducibility Auditor", page_icon="♻️", layout="wide")
    st.title("♻️ Reproducibility Auditor")
    st.caption("Criterion by criterion, with an explicit human tier in the middle.")

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
        met_at = st.slider("Criterion met at or above", 0.5, 0.99, 0.75, 0.01)
        unmet_at = st.slider("Criterion unmet at or below", 0.01, 0.5, 0.25, 0.01)
        environment_floor = st.slider(
            "Minimum environment level to clear without a human", 0.0, 3.0, 2.0, 0.1
        )
        st.divider()
        st.subheader("Mandatory criteria")
        st.caption("Severity is policy. Changing it re-runs no inference.")
        mandatory = [
            c["key"]
            for c in CRITERIA
            if st.checkbox(c["label"], value=c["mandatory"], key=f"m_{c['key']}")
        ]

    uploaded = st.file_uploader("Papers (JSON list of {id, title, text})", type=["json"])
    if uploaded:
        papers = json.loads(uploaded.read().decode("utf-8"))
    else:
        papers = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(papers)} papers.")

    if not st.button(f"Audit {len(papers)} papers", type="primary"):
        return

    questions = build_questions(papers)
    try:
        with st.spinner(f"{len(questions)} judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"papers": papers}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    details = []
    for index, paper in enumerate(papers):
        probabilities = {
            c["key"]: answers.noul(f"crit_{index}_{c['key']}") for c in CRITERIA
        }
        statuses = {k: classify(v, met_at, unmet_at) for k, v in probabilities.items()}
        environment = answers.score(f"env_{index}")
        access = answers.choice(f"access_{index}")
        result, reason = tier(statuses, environment, access, mandatory, environment_floor)
        details.append((paper, probabilities, statuses, environment, access, result, reason))
        rows.append(
            {
                "paper": paper.get("title", paper["id"]),
                "tier": result,
                "environment": round(environment, 2),
                "artifact access": access,
                "mandatory met": sum(1 for k in mandatory if statuses.get(k) == "met"),
                "reason": reason,
            }
        )

    counts = {k: sum(1 for d in details if d[5] == k) for k in ("likely", "human", "blocked")}
    columns = st.columns(3)
    columns[0].metric("Likely reproducible", counts["likely"])
    columns[1].metric("Needs a human", counts["human"])
    columns[2].metric("Blocked", counts["blocked"])
    st.caption(
        "The middle tier is not a failure of the audit — it is the set of papers where "
        "the text genuinely does not settle the question."
    )

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    for paper, probabilities, statuses, environment, access, result, reason in details:
        icon = {"likely": "✅", "human": "⚠️", "blocked": "🛑"}[result]
        with st.expander(f"{icon} {paper.get('title', paper['id'])} — {result}: {reason}"):
            for criterion in CRITERIA:
                status = statuses[criterion["key"]]
                mark = {"met": "✅", "unmet": "🛑", "unclear": "⚠️"}[status]
                flag = " *(mandatory)*" if criterion["key"] in mandatory else ""
                st.caption(
                    f"{mark} {probabilities[criterion['key']]:.2f} — {criterion['label']}{flag}"
                )
            level = min(int(environment), len(ENVIRONMENT_LEVELS) - 1)
            st.markdown(f"**Environment {environment:.2f}** — nearest level: {ENVIRONMENT_LEVELS[level]}")
            st.markdown(f"**Artifact access** — {access}: {ACCESS_OPTIONS[access]}")
            st.write(paper["text"])

    st.divider()
    st.caption(
        f"{len(papers)} papers × ({len(CRITERIA)} criteria + 1 score + 1 choice) = "
        f"{len(questions)} judgments, 1 request, {answers.elapsed_s:.1f}s, "
        f"${answers.cost_usd:.6f} ({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
