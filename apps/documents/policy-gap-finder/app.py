"""Policy Gap Finder -- which controls in a standard your policy does not address.

Readiness assessment for ISO 27001, SOC 2 or a customer security questionnaire
is the same grim afternoon every time: the control catalogue in one window, the
policy PDF in the other, Ctrl-F, and a spreadsheet cell that says "covered?".

The thing that makes the spreadsheet useless is that "covered" is three
different properties wearing one hat:

    covered   -- the policy states a rule that would satisfy the control
    specific  -- it names a mechanism, a frequency, or a concrete artefact
    owned     -- somebody is accountable for it by role

An auditor fails you for the second and third just as readily as the first, and
they fail INDEPENDENTLY: a policy can be specific and unowned, or owned and
aspirational. So they are three Nouls with three thresholds, not one rubric --
collapsing them destroys exactly the tuning the compliance lead needs.

A fourth question, a Choice, names the SHAPE of the gap, because that decides
who fixes it: an absent control needs a policy author, an unowned one needs a
conversation with a department head.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_CONTROLS = HERE / "sample_controls.json"
SAMPLE_POLICY = HERE / "sample_policy.txt"

CHARS_PER_TOKEN = 4
QUESTIONS_PER_CONTROL = 4

# Worst first. Used for sorting the remediation list and nothing else.
SEVERITY = {"open": 3, "vague": 2, "unowned": 1, "met": 0}

GAP_TYPES = {
    "absent": "The policy document says nothing that bears on this control at all.",
    "aspirational": (
        "The policy states an intention, commitment or principle about this control "
        "but names no mechanism, frequency or artefact that would implement it."
    ),
    "partial": (
        "The policy implements part of what the control requires and leaves another "
        "part of its scope unaddressed."
    ),
    "unowned": (
        "The policy states a rule that would satisfy the control but assigns it to "
        "no named role, team or job title."
    ),
    "none": "The control is properly addressed: a rule, a mechanism, and an accountable role.",
}


def build_questions(controls: list[dict]) -> dict:
    """Three independent Nouls plus one Choice per control, all in one batch.

    Every question points at the same ingested state by path, so the policy is
    read once no matter how large the control catalogue is.
    """
    questions: dict[str, dict] = {}
    for index in range(len(controls)):
        reference = {
            "control_title": f"`controls[{index}].title`",
            "control_requirement": f"`controls[{index}].requirement`",
            "policy": "`policy`",
        }
        questions[f"covered_{index}"] = noul(
            {
                "task": "Does the policy document state a rule that would satisfy this control?",
                **reference,
            },
            true="The policy contains a rule, prohibition or obligation that meets what the control requires",
            false="The policy is silent on this subject, or only mentions it without imposing any rule",
        )
        questions[f"specific_{index}"] = noul(
            {
                "task": (
                    "Where the policy addresses this control, does it name a concrete "
                    "mechanism, review frequency, tool or artefact?"
                ),
                **reference,
            },
            true="The policy names a specific mechanism, cadence, system or record that implements the rule",
            false="The policy expresses an intention, aspiration or principle with nothing concrete behind it",
        )
        questions[f"owned_{index}"] = noul(
            {
                "task": (
                    "Does the policy make a named role, team or job title accountable "
                    "for this control?"
                ),
                **reference,
            },
            true="A specific role, job title or named team is made responsible for this control",
            false="No role is named for it, or responsibility is left to the organisation generally",
        )
        questions[f"gap_{index}"] = choice(
            {
                "task": "Select the option that best describes the policy's treatment of this control.",
                **reference,
            },
            GAP_TYPES,
        )
    return questions


def assess(
    covered: float, specific: float, owned: float, accept: float, reject: float
) -> tuple[str, str]:
    """Per-control verdict. Jev supplied evidence; this is the policy.

    Ordered, not averaged. An absent rule cannot be rescued by a named owner,
    so `covered` is checked first and a failure there short-circuits.
    """
    if covered <= reject:
        return "open", f"the policy states no rule for this control (P(covered)={covered:.2f})"
    if specific <= reject:
        return "vague", f"stated as intent, no mechanism named (P(specific)={specific:.2f})"
    if owned <= reject:
        return "unowned", f"rule exists but no role is accountable (P(owned)={owned:.2f})"
    if covered >= accept and specific >= accept and owned >= accept:
        return "met", "rule, mechanism and accountable role all present"
    weakest = min(covered, specific, owned)
    return "vague", f"borderline — weakest signal P={weakest:.2f}, a human should read it"


def disagrees(status: str, gap_type: str) -> bool:
    """True when the thresholded Nouls and the gap-shape Choice tell different stories.

    Not an error: it means the control sits on a boundary and is worth a human
    eye. Surfacing it beats silently trusting whichever question you sorted by.
    """
    return (status == "met") != (gap_type == "none")


def summarise_domains(rows: list[dict]) -> list[dict]:
    """Per-domain rollup. Arithmetic belongs here, never in a question."""
    domains: dict[str, list[dict]] = {}
    for row in rows:
        domains.setdefault(row["domain"], []).append(row)

    summary = []
    for domain, members in domains.items():
        met = sum(1 for row in members if row["status"] == "met")
        summary.append(
            {
                "domain": domain,
                "controls": len(members),
                "met": met,
                "gaps": len(members) - met,
                "coverage": met / len(members),
                "worst": max(SEVERITY[row["status"]] for row in members),
            }
        )
    summary.sort(key=lambda entry: (-entry["worst"], entry["coverage"]))
    return summary


def main() -> None:
    st.set_page_config(page_title="Policy Gap Finder", page_icon="🛡️", layout="wide")
    st.title("🛡️ Policy Gap Finder")
    st.caption(
        "Which controls in a standard your policy document leaves open, vague or "
        "unowned — every control judged in **one** request."
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
        st.caption("Tune against controls your auditor has already ruled on.")
        accept = st.slider("Treat P(yes) at or above this as YES", 0.5, 0.99, 0.75, 0.01)
        reject = st.slider("Treat P(yes) at or below this as NO", 0.01, 0.5, 0.25, 0.01)
        st.divider()
        show_met = st.checkbox("Show controls that are met", value=False)

    left, right = st.columns(2)
    with left:
        uploaded_controls = st.file_uploader("Control catalogue (.json)", type=["json"])
        raw_controls = (
            uploaded_controls.read().decode("utf-8", errors="replace")
            if uploaded_controls
            else SAMPLE_CONTROLS.read_text(encoding="utf-8")
        )
        raw_controls = st.text_area("Controls", value=raw_controls, height=240)
    with right:
        uploaded_policy = st.file_uploader("Policy document (.txt)", type=["txt"])
        policy = (
            uploaded_policy.read().decode("utf-8", errors="replace")
            if uploaded_policy
            else SAMPLE_POLICY.read_text(encoding="utf-8")
        )
        policy = st.text_area("Policy", value=policy, height=240)

    if not st.button("Find gaps", type="primary"):
        st.info("A sample control catalogue and InfoSec policy are loaded. Hit the button.")
        return

    try:
        controls = json.loads(raw_controls)
    except json.JSONDecodeError as exc:
        st.error(f"Controls are not valid JSON: {exc}")
        return
    if not isinstance(controls, list) or not controls:
        st.error("Controls must be a non-empty JSON list.")
        return
    if not policy.strip():
        st.error("The policy document is empty.")
        return

    state = {"controls": controls, "policy": policy}
    estimated_tokens = len(json.dumps(state)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Split the catalogue or switch provider."
        )
        return

    questions = build_questions(controls)

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, control in enumerate(controls):
        covered = answers.noul(f"covered_{index}")
        specific = answers.noul(f"specific_{index}")
        owned = answers.noul(f"owned_{index}")
        gap_type = answers.choice(f"gap_{index}")
        status, reason = assess(covered, specific, owned, accept, reject)
        rows.append(
            {
                "id": control.get("id", f"#{index + 1}"),
                "domain": control.get("domain", "Uncategorised"),
                "title": control.get("title", ""),
                "requirement": control.get("requirement", ""),
                "covered": covered,
                "specific": specific,
                "owned": owned,
                "gap_type": gap_type,
                "status": status,
                "reason": reason,
                "disputed": disagrees(status, gap_type),
            }
        )

    badge = {"met": "✅", "unowned": "👤", "vague": "🌫️", "open": "❌"}
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in SEVERITY}
    columns = st.columns(5)
    columns[0].metric("Controls", len(rows))
    columns[1].metric("Met", counts["met"])
    columns[2].metric("Unowned", counts["unowned"])
    columns[3].metric("Vague", counts["vague"])
    columns[4].metric("Open", counts["open"])

    st.subheader("Coverage by domain")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Domain": entry["domain"],
                    "Controls": entry["controls"],
                    "Met": entry["met"],
                    "Gaps": entry["gaps"],
                    "Coverage": f"{entry['coverage'] * 100:.0f}%",
                }
                for entry in summarise_domains(rows)
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Remediation list")
    st.caption("Worst first. Each line names the question that produced the verdict.")
    for entry in summarise_domains(rows):
        members = [row for row in rows if row["domain"] == entry["domain"]]
        members.sort(key=lambda row: -SEVERITY[row["status"]])
        visible = members if show_met else [row for row in members if row["status"] != "met"]
        if not visible:
            continue
        with st.expander(
            f"{entry['domain']} — {entry['gaps']} gap(s) of {entry['controls']}", expanded=True
        ):
            for row in visible:
                with st.container(border=True):
                    st.markdown(
                        f"{badge[row['status']]} **{row['id']} {row['title']}** — "
                        f"`{row['status']}` · shape: `{row['gap_type']}`"
                    )
                    st.caption(row["reason"])
                    if row["disputed"]:
                        st.warning(
                            "The thresholded checks and the gap-shape question disagree — "
                            "this control is on a boundary, read it yourself."
                        )
                    inner = st.columns(3)
                    for column, (label, value) in zip(
                        inner,
                        (("covered", row["covered"]), ("specific", row["specific"]), ("owned", row["owned"])),
                    ):
                        column.progress(min(value, 1.0), text=f"{label} {value:.2f}")
                    st.caption(row["requirement"])

    st.divider()
    st.caption(
        f"{len(rows)} controls × {QUESTIONS_PER_CONTROL} judgments = {len(questions)} answers, "
        f"1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
