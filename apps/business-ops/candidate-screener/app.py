"""Candidate Screener -- criterion-by-criterion screening ASSISTANCE for humans.

READ THIS BEFORE YOU RUN IT.

This app does not reject anyone. It cannot. There is no "reject" outcome in the
code: every application lands in `advance` or in one of two human-review piles,
and the two review piles differ only in what a person is asked to look at first.
A human reads every application either way.

The design follows from that constraint:

  * Every criterion is a separate Noul with its own evidence, so a human can see
    which requirement drove which flag, criterion by criterion.
  * The criteria are supplied by the user and are checked against a list of
    protected characteristics before anything is sent.
  * Nothing is composed into a single "candidate score", because a single number
    is exactly what makes a screening tool impossible to audit or contest.

Hiring is a high-stakes decision about people. The useful automation here is
ordering a queue and showing evidence, not making the call.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_applications.json"

DEFAULT_ROLE = {
    "title": "Senior Backend Engineer (Payments)",
    "required": [
        "The application shows professional experience building and operating backend services in production.",
        "The application shows experience with payments, billing, ledgers, or financial transaction systems.",
        "The application shows experience with a statically typed backend language such as Go, Java, Kotlin, Rust, or C#.",
        "The application shows experience owning a service on call, including incident response.",
    ],
    "preferred": [
        "The application shows experience with event-driven architectures or message queues.",
        "The application shows experience working under a regulatory or audit regime such as PCI DSS or SOX.",
        "The application shows experience mentoring or technically leading other engineers.",
    ],
}

# Anything on this list is not a job-related criterion. Checked in Python, before
# a single token is sent. This is a trust boundary, not a style preference.
PROTECTED_TERMS = (
    "age", "young", "old", "birth", "graduat", "race", "ethnic", "national origin",
    "nationality", "citizen", "visa", "religio", "gender", "male", "female", "sex",
    "pregnan", "marital", "married", "children", "family", "disab", "health",
    "sexual orientation", "photo", "appearance", "accent", "native speaker",
    "culture fit", "culture-fit", "political", "union", "salary history",
    "current salary", "postcode", "zip code", "neighbourhood", "neighborhood",
)


def screen_criteria(criteria: list[str]) -> list[tuple[str, str]]:
    """Flag criteria that are not job-related. Runs before any API call."""
    problems = []
    for criterion in criteria:
        lowered = criterion.lower()
        for term in PROTECTED_TERMS:
            if re.search(rf"\b{re.escape(term)}", lowered):
                problems.append((criterion, term))
                break
    return problems


def build_questions(applications: list[dict], role: dict) -> dict:
    """One Noul per criterion per application. Nothing is ever composed."""
    questions: dict[str, dict] = {}
    for index in range(len(applications)):
        reference = {
            "resume": f"`applications[{index}].resume`",
            "cover_note": f"`applications[{index}].cover_note`",
        }
        for kind in ("required", "preferred"):
            for position, criterion in enumerate(role.get(kind, [])):
                questions[f"{kind}_{index}_{position}"] = noul(
                    {
                        "task": "Does the application below provide evidence for this job requirement?",
                        "requirement": criterion,
                        "role": role["title"],
                        "application": reference,
                    },
                    true="The application states experience that clearly satisfies this requirement",
                    false="The application does not state experience satisfying this requirement",
                )
        questions[f"evidence_{index}"] = noul(
            {
                "task": "Does the application describe the candidate's own contribution in enough detail to be assessed?",
                "application": reference,
            },
            true="Specific systems, decisions, or outcomes the candidate worked on are described",
            false="Only job titles, tool lists, or generic statements are given",
        )
    return questions


def triage(required: list[float], met_at: float, review_at: float) -> tuple[str, str]:
    """Three outcomes, NONE of which is a rejection.

    `advance` means a recruiter reads it first. `review` and `review_thin` both
    mean a human reads it. The only difference is what they are told to check.
    Adding a fourth outcome that skips the human would break the app's contract.
    """
    if not required:
        return "review", "no required criteria configured"
    unmet = [i for i, p in enumerate(required) if p <= review_at]
    if all(p >= met_at for p in required):
        return "advance", "evidence found for every required criterion"
    if unmet:
        listed = ", ".join(f"#{i + 1}" for i in unmet)
        return "review_thin", f"no evidence found for required criterion {listed} — verify by hand"
    return "review", "some requirements are borderline in the application text"


def main() -> None:
    st.set_page_config(page_title="Candidate Screener", page_icon="🧑‍💼", layout="wide")
    st.title("🧑‍💼 Candidate Screener")
    st.caption("Assists human screening. It does not reject anyone, and it never can.")

    st.warning(
        "**This tool assists a human screener. It does not make hiring decisions.** "
        "Every application ends up in front of a person; the piles below only change "
        "the reading order and what to check. There is no automatic rejection, and "
        "the code has no reject outcome to enable. Criteria must be job-related — "
        "anything touching a protected characteristic is blocked before a request "
        "is sent.",
        icon="⚠️",
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Reading-order thresholds")
        st.caption("These change what a human reads first. They never remove anyone.")
        met_at = st.slider("Treat evidence as found at P(yes) ≥", 0.5, 0.99, 0.75, 0.01)
        review_at = st.slider("Flag as 'no evidence found' at P(yes) ≤", 0.01, 0.5, 0.25, 0.01)

    role_text = st.text_area(
        "Role criteria (JSON). Job-related requirements only.",
        value=json.dumps(DEFAULT_ROLE, indent=2),
        height=280,
    )
    try:
        role = json.loads(role_text)
        assert role.get("title") and role.get("required")
    except (json.JSONDecodeError, AssertionError) as exc:
        st.error(f"Role must be JSON with a 'title' and a non-empty 'required' list: {exc}")
        return

    all_criteria = list(role.get("required", [])) + list(role.get("preferred", []))
    problems = screen_criteria(all_criteria)
    if problems:
        st.error("These criteria reference a protected or non-job-related characteristic. Rewrite them.")
        for criterion, term in problems:
            st.markdown(f"- `{term}` → {criterion}")
        return

    upload = st.file_uploader(
        "Applications (JSON list of {id, resume, cover_note})", type=["json"]
    )
    applications = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Screen {len(applications)} applications", type="primary"):
        questions = build_questions(applications, role)
        try:
            with st.spinner(f"{len(questions)} criterion judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"applications": applications}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["screened"] = {
            "applications": applications,
            "role": role,
            "judgments": [
                {
                    "required": [
                        answers.noul(f"required_{i}_{p}") for p in range(len(role["required"]))
                    ],
                    "preferred": [
                        answers.noul(f"preferred_{i}_{p}")
                        for p in range(len(role.get("preferred", [])))
                    ],
                    "evidence": answers.noul(f"evidence_{i}"),
                }
                for i in range(len(applications))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    screened = st.session_state.get("screened")
    if not screened:
        st.info(f"{len(applications)} anonymised sample applications bundled. Hit the button.")
        return

    role = screened["role"]
    piles: dict[str, list] = {"advance": [], "review": [], "review_thin": []}
    for application, judgment in zip(screened["applications"], screened["judgments"]):
        pile, reason = triage(judgment["required"], met_at, review_at)
        piles[pile].append((application, judgment, reason))

    columns = st.columns(4)
    columns[0].metric("Read first", len(piles["advance"]))
    columns[1].metric("Read next", len(piles["review"]))
    columns[2].metric("Read with care", len(piles["review_thin"]))
    columns[3].metric("Auto-rejected", 0, help="This tool has no reject outcome.")

    st.caption(
        f"All {len(screened['applications'])} applications are still in the queue. "
        "The piles are a reading order, not a filter."
    )

    tabs = st.tabs(
        [
            f"Read first ({len(piles['advance'])})",
            f"Read next ({len(piles['review'])})",
            f"Read with care ({len(piles['review_thin'])})",
        ]
    )
    for tab, key in zip(tabs, ("advance", "review", "review_thin")):
        with tab:
            for application, judgment, reason in piles[key]:
                with st.container(border=True):
                    st.markdown(f"**{application['id']}** — {reason}")
                    if judgment["evidence"] <= review_at:
                        st.caption(
                            "⚠️ The application gives little detail about this person's own "
                            "contribution. Thin applications are not weak candidates — read it yourself."
                        )
                    left, right = st.columns(2)
                    left.markdown("*Required*")
                    for probability, criterion in zip(judgment["required"], role["required"]):
                        mark = "✅" if probability >= met_at else ("⬜" if probability <= review_at else "🟡")
                        left.caption(f"{mark} {probability:.2f} — {criterion[:80]}")
                    right.markdown("*Preferred*")
                    for probability, criterion in zip(
                        judgment["preferred"], role.get("preferred", [])
                    ):
                        mark = "✅" if probability >= met_at else ("⬜" if probability <= review_at else "🟡")
                        right.caption(f"{mark} {probability:.2f} — {criterion[:80]}")
                    with st.expander("Application text"):
                        st.write(application["resume"])
                        st.write(application.get("cover_note", ""))

    audit = pd.DataFrame(
        [
            {
                "id": application["id"],
                "pile": triage(judgment["required"], met_at, review_at)[0],
                "evidence detail": round(judgment["evidence"], 2),
                **{
                    f"req {p + 1}": round(value, 2)
                    for p, value in enumerate(judgment["required"])
                },
                **{
                    f"pref {p + 1}": round(value, 2)
                    for p, value in enumerate(judgment["preferred"])
                },
            }
            for application, judgment in zip(screened["applications"], screened["judgments"])
        ]
    )
    st.subheader("Audit trail")
    st.caption("Export this. If a decision is ever challenged, this is the record.")
    st.dataframe(audit, width="stretch", hide_index=True)
    st.download_button(
        "Download screening_audit.csv",
        audit.to_csv(index=False).encode("utf-8"),
        file_name="screening_audit.csv",
        mime="text/csv",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", screened["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{screened['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${screened['cost']:.6f}")
    st.caption(
        f"{screened['tokens']:,} input tokens for {len(screened['applications'])} applications "
        f"× {len(role['required']) + len(role.get('preferred', [])) + 1} criteria. "
        "Changing the thresholds above re-ordered the queue for $0.00."
    )


if __name__ == "__main__":
    main()
