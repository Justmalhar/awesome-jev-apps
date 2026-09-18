"""Peer Review Router -- match submissions to reviewers at conference scale.

Bidding solves this for reviewers who bid. The remainder -- typically most of
the programme committee and most of the submissions -- lands on a chair who
assigns by eyeballing titles at midnight, which is how a paper on federated
optimisation reaches three reviewers who work on HCI.

Two things are being decided per (submission, reviewer) pair and they are NOT
the same kind of thing:

  * expertise fit -- ordered, a ranking key, so a Score.
  * conflict of interest -- binary, independent, and a VETO. A separate Noul.

Fusing them is the tempting mistake. A conflicted expert has the highest
possible fit and must not review the paper: that is not "high fit, slightly
discounted", it is disqualified. Weighted averages model compensating
preferences; this is not one.

Hard conflicts (shared affiliation, co-authorship) are settled in PYTHON by
string comparison before any question is built -- it is exact, free, and it
removes those pairs from the request entirely. The Noul is asked only about the
soft conflicts that need reading: a described recent collaboration, a
supervisory relationship, a shared project.

Pairs are batched against `provider.context_tokens` -- batching for SIZE, not a
dependency. Every question stays independent.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE_SUBMISSIONS = HERE / "sample_submissions.json"
SAMPLE_REVIEWERS = HERE / "sample_reviewers.json"

CHARS_PER_TOKEN = 4

FIT_LEVELS = [
    "The submission's topic lies outside every area this reviewer lists, and nothing in their recent work touches it.",
    "The submission shares only a broad field with the reviewer, matching none of their listed methods or application areas.",
    "The submission sits in an application area the reviewer lists, but its core methods are outside their stated recent work.",
    "The submission's core methods and its application area both appear directly in this reviewer's listed expertise or recent work.",
]


def normalise(value: str) -> str:
    return " ".join(value.lower().replace(".", " ").split())


def hard_conflict(submission: dict, reviewer: dict) -> str | None:
    """Exact conflicts, settled in Python. Free, deterministic, and audit-proof.

    A chair has to be able to say WHY a reviewer was excluded, and "the model
    thought so" is not an answer when the excluded reviewer asks. String
    identity is; so these never reach the model at all.
    """
    reviewer_affiliation = normalise(reviewer.get("affiliation", ""))
    if reviewer_affiliation and reviewer_affiliation in {
        normalise(a) for a in submission.get("author_affiliations", [])
    }:
        return f"same institution ({reviewer['affiliation']})"

    reviewer_name = normalise(reviewer.get("name", ""))
    if reviewer_name and reviewer_name in {normalise(a) for a in submission.get("authors", [])}:
        return "reviewer is an author"

    coauthors = {normalise(a) for a in reviewer.get("recent_coauthors", [])}
    overlap = coauthors & {normalise(a) for a in submission.get("authors", [])}
    if overlap:
        return "declared recent co-author of an author"
    return None


def plan_batches(sizes: list[int], budget: int) -> list[list[int]]:
    """Greedily pack submission indices into requests that fit the context window."""
    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, size in enumerate(sizes):
        if current and used + size > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += size
    if current:
        batches.append(current)
    return batches


def build_questions(
    submissions: list[dict], reviewers: list[dict], pairs: list[tuple[int, int]]
) -> dict:
    """One Score and one Noul per surviving pair. Indices are LOCAL to the batch."""
    questions: dict[str, dict] = {}
    for s_index, r_index in pairs:
        submission_ref = {
            "title": f"`submissions[{s_index}].title`",
            "abstract": f"`submissions[{s_index}].abstract`",
            "topics": f"`submissions[{s_index}].topics`",
        }
        reviewer_ref = {
            "expertise": f"`reviewers[{r_index}].expertise`",
            "recent_work": f"`reviewers[{r_index}].recent_work`",
        }
        questions[f"fit_{s_index}_{r_index}"] = score(
            {
                "task": "How well does this reviewer's stated expertise cover this submission?",
                "submission": submission_ref,
                "reviewer": reviewer_ref,
            },
            FIT_LEVELS,
        )
        questions[f"coi_{s_index}_{r_index}"] = noul(
            {
                "task": "Is there a conflict of interest between this reviewer and this submission that would stop them reviewing it impartially?",
                "submission": {
                    **submission_ref,
                    "authors": f"`submissions[{s_index}].authors`",
                    "author_affiliations": f"`submissions[{s_index}].author_affiliations`",
                },
                "reviewer": {
                    "name": f"`reviewers[{r_index}].name`",
                    "affiliation": f"`reviewers[{r_index}].affiliation`",
                    "recent_work": f"`reviewers[{r_index}].recent_work`",
                    "declared_relationships": f"`reviewers[{r_index}].declared_relationships`",
                },
            },
            true="The reviewer's own text describes a collaboration, supervision, shared project or funding tie with an author of this submission, or the submission is plainly their own work",
            false="Nothing in either text connects this reviewer personally to the authors of this submission",
        )
    return questions


def assign(
    pairs: list[dict],
    reviews_per_paper: int,
    max_load: int,
    coi_at: float,
    fit_floor: float,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Greedy matching under a veto. Returns (assignments, blocked, shortfalls).

    Greedy on fit is deliberate rather than optimal: a chair has to explain the
    allocation, and "the highest-fit unconflicted reviewer with capacity" is an
    explanation. A min-cost flow would be a better matching and an unauditable
    one.

    ponytail: greedy, O(n log n) on pairs. Swap for min-cost flow only if
    measured allocation quality actually justifies the loss of explainability.
    """
    def vetoed(pair: dict) -> bool:
        return bool(pair.get("hard_conflict")) or pair["coi"] >= coi_at

    blocked = [p for p in pairs if vetoed(p)]
    usable = [p for p in pairs if not vetoed(p) and p["fit"] >= fit_floor]
    usable.sort(key=lambda p: (-p["fit"], p["submission"], p["reviewer"]))

    load: dict[str, int] = {}
    per_paper: dict[str, int] = {}
    assignments: list[dict] = []
    for pair in usable:
        if per_paper.get(pair["submission"], 0) >= reviews_per_paper:
            continue
        if load.get(pair["reviewer"], 0) >= max_load:
            continue
        assignments.append(pair)
        per_paper[pair["submission"]] = per_paper.get(pair["submission"], 0) + 1
        load[pair["reviewer"]] = load.get(pair["reviewer"], 0) + 1

    submissions = sorted({p["submission"] for p in pairs})
    shortfalls = [
        {"submission": s, "assigned": per_paper.get(s, 0), "needed": reviews_per_paper}
        for s in submissions
        if per_paper.get(s, 0) < reviews_per_paper
    ]
    return assignments, blocked, shortfalls


def main() -> None:
    st.set_page_config(page_title="Peer Review Router", page_icon="🧑‍⚖️", layout="wide")
    st.title("🧑‍⚖️ Peer Review Router")
    st.caption("Pairwise expertise matching at conference scale, with conflicts as a separate veto.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Allocation policy")
        reviews_per_paper = st.slider("Reviews per submission", 1, 5, 3, 1)
        max_load = st.slider("Maximum papers per reviewer", 1, 12, 4, 1)
        coi_at = st.slider("Veto at or above this P(conflict)", 0.2, 0.9, 0.45, 0.01)
        fit_floor = st.slider("Minimum expertise fit to assign", 0.0, 3.0, 1.0, 0.1)
        st.caption("All Python-side. Re-allocating re-runs no inference.")
        st.divider()
        st.caption(f"Context window: {provider.context_tokens:,} tokens.")

    left, right = st.columns(2)
    with left:
        submissions_file = st.file_uploader("Submissions (JSON)", type=["json"], key="subs")
    with right:
        reviewers_file = st.file_uploader("Reviewers (JSON)", type=["json"], key="revs")

    submissions = (
        json.loads(submissions_file.read().decode("utf-8"))
        if submissions_file
        else json.loads(SAMPLE_SUBMISSIONS.read_text(encoding="utf-8"))
    )
    reviewers = (
        json.loads(reviewers_file.read().decode("utf-8"))
        if reviewers_file
        else json.loads(SAMPLE_REVIEWERS.read_text(encoding="utf-8"))
    )

    # Exact conflicts first: they are free and they shrink the request.
    hard: dict[tuple[int, int], str] = {}
    for s_index, submission in enumerate(submissions):
        for r_index, reviewer in enumerate(reviewers):
            reason = hard_conflict(submission, reviewer)
            if reason:
                hard[(s_index, r_index)] = reason

    total_pairs = len(submissions) * len(reviewers)
    st.caption(
        f"{len(submissions)} submissions × {len(reviewers)} reviewers = {total_pairs} pairs. "
        f"{len(hard)} removed in Python on an exact conflict before any inference."
    )

    reviewer_tokens = sum(len(json.dumps(r)) for r in reviewers) // CHARS_PER_TOKEN
    sizes = [
        len(json.dumps(s)) // CHARS_PER_TOKEN + len(reviewers) * 120 for s in submissions
    ]
    budget = max(int(provider.context_tokens * 0.4) - reviewer_tokens, 1000)
    batches = plan_batches(sizes, budget)
    asked = total_pairs - len(hard)
    st.caption(f"{asked * 2} judgments over {len(batches)} request(s).")

    if not st.button("Route the submissions", type="primary"):
        return

    pairs: list[dict] = []
    elapsed = 0.0
    try:
        with JevClient(provider=provider) as client:
            for number, indices in enumerate(batches, start=1):
                batch_submissions = [submissions[i] for i in indices]
                local_pairs = [
                    (local, r_index)
                    for local, s_index in enumerate(indices)
                    for r_index in range(len(reviewers))
                    if (s_index, r_index) not in hard
                ]
                if not local_pairs:
                    continue
                questions = build_questions(batch_submissions, reviewers, local_pairs)
                with st.spinner(f"Request {number} of {len(batches)}..."):
                    answers = client.ask(
                        {"submissions": batch_submissions, "reviewers": reviewers}, questions
                    )
                for local, r_index in local_pairs:
                    s_index = indices[local]
                    pairs.append(
                        {
                            "submission": submissions[s_index]["id"],
                            "reviewer": reviewers[r_index]["id"],
                            "title": submissions[s_index]["title"],
                            "reviewer_name": reviewers[r_index]["name"],
                            "fit": answers.score(f"fit_{local}_{r_index}"),
                            "coi": answers.noul(f"coi_{local}_{r_index}"),
                            "hard_conflict": None,
                        }
                    )
                elapsed = answers.elapsed_s
            totals = (client.total_cost_usd, client.total_input_tokens, client.calls)
    except JevError as exc:
        st.error(str(exc))
        return

    for (s_index, r_index), reason in hard.items():
        pairs.append(
            {
                "submission": submissions[s_index]["id"],
                "reviewer": reviewers[r_index]["id"],
                "title": submissions[s_index]["title"],
                "reviewer_name": reviewers[r_index]["name"],
                "fit": 0.0,
                "coi": 1.0,
                "hard_conflict": reason,
            }
        )

    assignments, blocked, shortfalls = assign(
        pairs, reviews_per_paper, max_load, coi_at, fit_floor
    )

    columns = st.columns(4)
    columns[0].metric("Assignments", len(assignments))
    columns[1].metric("Pairs blocked", len(blocked))
    columns[2].metric("Submissions short", len(shortfalls))
    loads = {p["reviewer"]: 0 for p in pairs}
    for pair in assignments:
        loads[pair["reviewer"]] += 1
    columns[3].metric("Busiest reviewer", max(loads.values(), default=0))

    st.subheader("Allocation")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "submission": a["submission"],
                    "title": a["title"],
                    "reviewer": a["reviewer_name"],
                    "fit": round(a["fit"], 2),
                    "P(conflict)": round(a["coi"], 2),
                }
                for a in sorted(assignments, key=lambda a: (a["submission"], -a["fit"]))
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    if shortfalls:
        st.subheader("Under-reviewed submissions")
        st.warning(
            "These could not be filled without breaking a conflict rule or a load cap. "
            "A chair handles them; the tool does not quietly relax a veto.",
            icon="⚠️",
        )
        st.dataframe(pd.DataFrame(shortfalls), width="stretch", hide_index=True)

    st.subheader("Blocked pairs")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "submission": b["submission"],
                    "reviewer": b["reviewer_name"],
                    "fit": round(b["fit"], 2),
                    "P(conflict)": round(b["coi"], 2),
                    "why": b["hard_conflict"] or "declared or described relationship",
                }
                for b in sorted(blocked, key=lambda b: -b["fit"])
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "High-fit rows here are the point: the best-qualified reviewer is often the "
        "conflicted one, and a single blended score would have assigned them."
    )

    st.subheader("Reviewer load")
    names = {r["id"]: r["name"] for r in reviewers}
    st.dataframe(
        pd.DataFrame(
            [{"reviewer": names[k], "papers": v} for k, v in sorted(loads.items(), key=lambda kv: -kv[1])]
        ),
        width="stretch",
        hide_index=True,
    )

    cost, tokens, calls = totals
    st.divider()
    st.caption(
        f"{asked} pairs × 2 judgments = {asked * 2} questions over {calls} request(s), "
        f"last request {elapsed:.1f}s, ${cost:.6f} total ({tokens:,} input tokens). "
        f"{len(hard)} pairs cost nothing — Python settled them."
    )


if __name__ == "__main__":
    main()
