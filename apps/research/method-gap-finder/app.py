"""Method Gap Finder -- the reporting checklist, applied before a reviewer does it.

Desk rejection and major revision are mostly the same handful of omissions:
no control condition, no rationale for the sample, no ethics statement, no
preregistration. CONSORT, ARRIVE, STROBE and the rest exist precisely because
these are predictable -- and they are still missed, because the author who wrote
the methods is the worst person to notice what is not in them.

Every checklist item is a separate Noul. They are independent conditions: a
paper can have exemplary ethics reporting and no control group. Collapsing them
into one "methodological quality" rubric would let the strong items compensate
for a fatal one, which is exactly backwards -- a missing control condition is a
veto, not a deduction.

So the policy is conjunctive with severity tiers, in Python:

    blocking     <=>  any CRITICAL item is clearly absent
    needs review <=>  any CRITICAL item is ambiguous
    ready        <=>  every CRITICAL item is clearly present

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_manuscripts.json"

# Each item is an independent condition. `critical` items veto; `advisory` items
# are reported but never block, because severity is policy and policy is ours.
CHECKLIST = [
    {
        "key": "control",
        "label": "Control or comparison condition",
        "question": "Does the methods section describe a control condition, comparison group, or comparison arm against which the main condition is assessed?",
        "true": "A control group, placebo arm, waitlist, usual-care arm, baseline-only comparison or explicitly compared alternative condition is described",
        "false": "The design is single-arm or purely descriptive with nothing described as a comparison",
        "critical": True,
    },
    {
        "key": "sample_rationale",
        "label": "Sample size justification",
        "question": "Does the methods section give a stated rationale for how large the sample is -- a power analysis, a precision target, a simulation, or an explicit feasibility argument?",
        "true": "The text gives a reason for the sample size, such as a power analysis, an effect size assumption, a precision target or a stated feasibility constraint",
        "false": "The sample size is simply reported, or is not discussed as a design decision at all",
        "critical": True,
    },
    {
        "key": "ethics",
        "label": "Ethics approval statement",
        "question": "Does the methods section state that the study received ethical approval, or that a named ethics body reviewed or waived it?",
        "true": "An ethics committee, IRB, REC or equivalent is named as having approved the work or granted a waiver",
        "false": "No ethical review body is mentioned anywhere in the methods",
        "critical": True,
    },
    {
        "key": "preregistration",
        "label": "Preregistration or protocol registration",
        "question": "Does the methods section state that the study or its analysis plan was registered before the data were collected or analysed?",
        "true": "A registry, registration identifier, or a lodged preregistered analysis plan is named",
        "false": "No registration or prespecified protocol is mentioned",
        "critical": True,
    },
    {
        "key": "consent",
        "label": "Informed consent",
        "question": "Does the methods section state how participant consent was obtained, or explain why consent was not required?",
        "true": "Consent is described as obtained, or a justification is given for proceeding without individual consent",
        "false": "Human participants are involved and consent is never mentioned",
        "critical": False,
    },
    {
        "key": "eligibility",
        "label": "Eligibility criteria",
        "question": "Does the methods section state the criteria that made a participant eligible or ineligible for inclusion?",
        "true": "Inclusion or exclusion criteria for participants are stated",
        "false": "Who could take part is never specified beyond a general description",
        "critical": False,
    },
    {
        "key": "blinding",
        "label": "Blinding or masking",
        "question": "Does the methods section state who was blinded to allocation, or explain that blinding was not possible?",
        "true": "Blinding of participants, deliverers or outcome assessors is described, or its absence is explicitly justified",
        "false": "Allocation awareness is never addressed in a design where it could matter",
        "critical": False,
    },
    {
        "key": "data_availability",
        "label": "Data availability",
        "question": "Does the methods section state where the underlying data can be obtained, or why they cannot be shared?",
        "true": "A repository, access procedure, or an explicit restriction on sharing is stated",
        "false": "Nothing is said about whether the data are available to anyone else",
        "critical": False,
    },
]

CRITICAL = [item["key"] for item in CHECKLIST if item["critical"]]


def build_questions(manuscripts: list[dict], checklist: list[dict] = CHECKLIST) -> dict:
    """One Noul per checklist item per manuscript. Independent conditions stay independent."""
    questions: dict[str, dict] = {}
    for index in range(len(manuscripts)):
        for item in checklist:
            questions[f"chk_{index}_{item['key']}"] = noul(
                {
                    "task": item["question"],
                    "methods_section": f"`manuscripts[{index}].methods`",
                },
                true=item["true"],
                false=item["false"],
            )
    return questions


def classify(probability: float, present_at: float, absent_at: float) -> str:
    """One item's status. The middle band is the honest answer, not a rounding error."""
    if probability >= present_at:
        return "present"
    if probability <= absent_at:
        return "absent"
    return "unclear"


def verdict(statuses: dict[str, str], critical: list[str] = CRITICAL) -> tuple[str, list[str]]:
    """Conjunctive with a veto. A missing control is not offset by good ethics reporting.

    Returns the verdict and the critical keys that drove it -- never a score,
    because "methodological quality: 6.4" is not something an author can act on.
    """
    missing = [key for key in critical if statuses.get(key) == "absent"]
    if missing:
        return "blocking", missing
    ambiguous = [key for key in critical if statuses.get(key) != "present"]
    if ambiguous:
        return "needs_review", ambiguous
    return "ready", []


def main() -> None:
    st.set_page_config(page_title="Method Gap Finder", page_icon="🧪", layout="wide")
    st.title("🧪 Method Gap Finder")
    st.caption("Reporting-checklist gaps in a methods section, before a reviewer finds them.")

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
        present_at = st.slider("Call an item present at or above", 0.5, 0.99, 0.75, 0.01)
        absent_at = st.slider("Call an item absent at or below", 0.01, 0.5, 0.25, 0.01)
        st.caption("Between the two, the item is 'unclear' and a human decides.")
        st.divider()
        st.subheader("Which items block")
        st.caption("Severity is policy. Move an item here and no inference re-runs.")
        critical = [
            item["key"]
            for item in CHECKLIST
            if st.checkbox(item["label"], value=item["critical"], key=f"crit_{item['key']}")
        ]

    uploaded = st.file_uploader("Manuscripts (JSON list of {id, title, methods})", type=["json"])
    if uploaded:
        manuscripts = json.loads(uploaded.read().decode("utf-8"))
    else:
        manuscripts = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(manuscripts)} methods sections.")

    if not st.button(f"Check {len(manuscripts)} manuscripts", type="primary"):
        return

    questions = build_questions(manuscripts)
    try:
        with st.spinner(f"{len(questions)} checklist judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"manuscripts": manuscripts}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    details: list[tuple[dict, dict, dict, str, list[str]]] = []
    for index, manuscript in enumerate(manuscripts):
        probabilities = {
            item["key"]: answers.noul(f"chk_{index}_{item['key']}") for item in CHECKLIST
        }
        statuses = {
            key: classify(value, present_at, absent_at) for key, value in probabilities.items()
        }
        result, drivers = verdict(statuses, critical)
        details.append((manuscript, probabilities, statuses, result, drivers))
        row = {"manuscript": manuscript.get("title", manuscript["id"]), "verdict": result}
        row.update({item["label"]: statuses[item["key"]] for item in CHECKLIST})
        rows.append(row)

    counts = {key: sum(1 for d in details if d[3] == key) for key in ("blocking", "needs_review", "ready")}
    columns = st.columns(3)
    columns[0].metric("Blocking gaps", counts["blocking"])
    columns[1].metric("Needs a human", counts["needs_review"])
    columns[2].metric("Checklist clear", counts["ready"])

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    labels = {item["key"]: item["label"] for item in CHECKLIST}
    for manuscript, probabilities, statuses, result, drivers in details:
        icon = {"blocking": "🛑", "needs_review": "⚠️", "ready": "✅"}[result]
        with st.expander(f"{icon} {manuscript.get('title', manuscript['id'])} — {result}"):
            if drivers:
                st.markdown("**Driven by:** " + ", ".join(labels[k] for k in drivers))
            for item in CHECKLIST:
                status = statuses[item["key"]]
                mark = {"present": "✅", "absent": "🛑", "unclear": "⚠️"}[status]
                flag = " *(blocking)*" if item["key"] in critical else ""
                st.caption(
                    f"{mark} {probabilities[item['key']]:.2f} — {item['label']}{flag}: {status}"
                )
            st.write(manuscript["methods"])

    st.divider()
    st.caption(
        f"{len(manuscripts)} manuscripts × {len(CHECKLIST)} checklist items = {len(questions)} "
        f"judgments, 1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
