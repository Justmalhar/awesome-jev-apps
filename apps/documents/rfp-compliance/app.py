"""RFP Compliance -- does this proposal actually cover the tender?

A bid team's worst day is the one where a mandatory requirement turned out to
be answered with "we can do that" instead of "we will do that". Procurement
scores the second and disqualifies the first.

Two things are being asked per requirement, and they are INDEPENDENT:

    addressed  -- the proposal responds to this requirement at all
    committed  -- the response is a binding undertaking, not a capability boast

A proposal can be fully addressed and entirely uncommitted. Collapsing those
into one "compliance score" is what lets a glossy response hide a gap, so they
are two Nouls with two thresholds, and the verdict is assembled in Python:

    submittable  <=>  every MANDATORY requirement is addressed AND committed

Conjunctive with a veto, never a weighted average. Ten excellent answers do not
compensate for one missing mandatory response -- the bid is simply
non-compliant.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_REQUIREMENTS = HERE / "sample_requirements.json"
SAMPLE_PROPOSAL = HERE / "sample_proposal.txt"

# Rough chars-per-token, only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4
QUESTIONS_PER_REQUIREMENT = 2

STATUS_ORDER = {"gap": 0, "weak": 1, "covered": 2}


def build_questions(requirements: list[dict]) -> dict:
    """Two independent Nouls per requirement, all in one batch.

    Both point at the same ingested state by path, so the proposal is read once
    however many requirements the tender has.
    """
    questions: dict[str, dict] = {}
    for index in range(len(requirements)):
        reference = {
            "requirement": f"`requirements[{index}].text`",
            "proposal": "`proposal`",
        }
        questions[f"addressed_{index}"] = noul(
            {
                "task": "Does the vendor proposal respond to this tender requirement?",
                **reference,
            },
            true=(
                "Some passage of the proposal speaks directly to the subject of this "
                "requirement, whether or not the answer is adequate"
            ),
            false=(
                "The proposal never engages with this requirement, or touches the general "
                "topic only in passing without answering what was asked"
            ),
        )
        questions[f"committed_{index}"] = noul(
            {
                "task": (
                    "Does the proposal make a binding undertaking to deliver this "
                    "requirement, as opposed to describing a capability?"
                ),
                **reference,
            },
            true=(
                "The proposal states the vendor will do it, warrants it, or offers it as a "
                "contractual term the Authority could enforce"
            ),
            false=(
                "The proposal says only that it can do it, typically does it, has done it "
                "elsewhere, plans to, or would scope it later -- or says nothing at all"
            ),
        )
    return questions


def classify(addressed: float, committed: float, accept: float, reject: float) -> tuple[str, str]:
    """Per-requirement verdict. Jev supplied the evidence; this is the policy.

    Order matters: an unanswered requirement is a gap regardless of what the
    commitment Noul says, because there is nothing to commit to.
    """
    if addressed <= reject:
        return "gap", f"no response to this requirement (P(addressed)={addressed:.2f})"
    if committed <= reject:
        return "weak", f"described but not undertaken (P(committed)={committed:.2f})"
    if addressed >= accept and committed >= accept:
        return "covered", "responded to and bindingly undertaken"
    return (
        "weak",
        f"borderline (P(addressed)={addressed:.2f}, P(committed)={committed:.2f})",
    )


def bid_verdict(rows: list[dict], block_optional: bool) -> tuple[bool, dict | None]:
    """Conjunctive with a veto: the first blocking requirement disqualifies the bid.

    `rows` carry `id`, `mandatory` and `status`. Optional requirements only
    block when the buyer scores them, which is a per-tender decision.
    """
    blockers = [
        row
        for row in rows
        if row["status"] != "covered" and (row["mandatory"] or block_optional)
    ]
    if not blockers:
        return True, None
    # Worst first, then tender order, so the banner names the real problem.
    blockers.sort(key=lambda row: (STATUS_ORDER[row["status"]], not row["mandatory"]))
    return False, blockers[0]


def main() -> None:
    st.set_page_config(page_title="RFP Compliance", page_icon="📋", layout="wide")
    st.title("📋 RFP Compliance")
    st.caption(
        "Every tender requirement checked against the proposal — *addressed* and "
        "*committed* scored separately, in **one** request."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Decision thresholds")
        st.caption("Tune these against bids you already know the outcome of.")
        accept = st.slider("Treat P(yes) at or above this as YES", 0.5, 0.99, 0.75, 0.01)
        reject = st.slider("Treat P(yes) at or below this as NO", 0.01, 0.5, 0.25, 0.01)
        block_optional = st.checkbox(
            "Uncovered optional requirements also block", value=False
        )
        st.caption("Mandatory requirements always block. Optional ones are your call.")

    left, right = st.columns(2)
    with left:
        uploaded_requirements = st.file_uploader("Requirements (.json)", type=["json"])
        raw_requirements = (
            uploaded_requirements.read().decode("utf-8", errors="replace")
            if uploaded_requirements
            else SAMPLE_REQUIREMENTS.read_text(encoding="utf-8")
        )
        raw_requirements = st.text_area("Tender requirements", value=raw_requirements, height=240)
    with right:
        uploaded_proposal = st.file_uploader("Proposal (.txt)", type=["txt"])
        proposal = (
            uploaded_proposal.read().decode("utf-8", errors="replace")
            if uploaded_proposal
            else SAMPLE_PROPOSAL.read_text(encoding="utf-8")
        )
        proposal = st.text_area("Vendor proposal", value=proposal, height=240)

    if not st.button("Check compliance", type="primary"):
        st.info("The bundled sample tender and proposal are loaded. Hit the button.")
        return

    try:
        requirements = json.loads(raw_requirements)
    except json.JSONDecodeError as exc:
        st.error(f"Requirements are not valid JSON: {exc}")
        return
    if not isinstance(requirements, list) or not requirements:
        st.error("Requirements must be a non-empty JSON list.")
        return
    if not proposal.strip():
        st.error("The proposal is empty.")
        return

    state = {"requirements": requirements, "proposal": proposal}
    estimated_tokens = len(json.dumps(state)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Trim the proposal or switch provider."
        )
        return

    questions = build_questions(requirements)

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, requirement in enumerate(requirements):
        addressed = answers.noul(f"addressed_{index}")
        committed = answers.noul(f"committed_{index}")
        status, reason = classify(addressed, committed, accept, reject)
        rows.append(
            {
                "id": requirement.get("id", f"#{index + 1}"),
                "mandatory": bool(requirement.get("mandatory", False)),
                "requirement": requirement.get("text", ""),
                "addressed": addressed,
                "committed": committed,
                "status": status,
                "reason": reason,
            }
        )

    submittable, blocker = bid_verdict(rows, block_optional)
    if submittable:
        st.success("Compliant — every blocking requirement is addressed and committed.")
    else:
        scope = "Mandatory" if blocker["mandatory"] else "Optional"
        st.error(
            f"**Not submittable.** {scope} requirement `{blocker['id']}` is "
            f"**{blocker['status']}** — {blocker['reason']}"
        )

    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUS_ORDER}
    columns = st.columns(4)
    columns[0].metric("Requirements", len(rows))
    columns[1].metric("Covered", counts["covered"])
    columns[2].metric("Weak", counts["weak"])
    columns[3].metric("Gaps", counts["gap"])

    badge = {"covered": "✅", "weak": "⚠️", "gap": "❌"}
    frame = pd.DataFrame(
        [
            {
                "ID": row["id"],
                "M": "★" if row["mandatory"] else "",
                "Status": f"{badge[row['status']]} {row['status']}",
                "P(addressed)": round(row["addressed"], 3),
                "P(committed)": round(row["committed"], 3),
                "Why": row["reason"],
                "Requirement": row["requirement"],
            }
            for row in rows
        ]
    )
    st.dataframe(frame, width="stretch", hide_index=True)

    problems = [row for row in rows if row["status"] != "covered"]
    if problems:
        st.subheader("What the bid team has to fix")
        for row in sorted(problems, key=lambda r: (STATUS_ORDER[r["status"]], not r["mandatory"])):
            with st.container(border=True):
                st.markdown(
                    f"{badge[row['status']]} **{row['id']}** "
                    f"{'(mandatory)' if row['mandatory'] else '(optional)'} — {row['reason']}"
                )
                st.caption(row["requirement"])

    st.divider()
    st.caption(
        f"{len(rows)} requirements × {QUESTIONS_PER_REQUIREMENT} judgments = "
        f"{len(questions)} answers, 1 request, {answers.elapsed_s:.1f}s, "
        f"${answers.cost_usd:.6f} ({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
