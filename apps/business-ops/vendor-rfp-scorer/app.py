"""Vendor RFP Scorer -- weighted supplier scoring where the weights are free.

Procurement scoring models are negotiated. Finance wants cost at 30%, security
wants their section to be a gate rather than a weight, the business owner thinks
implementation risk is under-counted, and the whole matrix gets re-cut twice
before the panel meets. Each re-cut currently means somebody re-reads the
responses.

Here the reading happens once. Each criterion gets a Score (how well the
response addresses it) and a Noul (does it fail a stated mandatory requirement),
and the weighted model is rebuilt in Python on every slider move for $0.00.

Mandatory requirements are Nouls and are checked as gates, never folded into the
weighted sum -- a vendor who fails a mandatory requirement does not get to make
it up on price, and a weighted average would let them.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_responses.json"

# Generic response-quality rubric, applied per criterion. Levels describe what a
# procurement reviewer actually sees on the page.
QUALITY_LEVELS = [
    "The response does not address this requirement at all",
    "The response acknowledges the requirement but describes no capability",
    "The response claims the capability in general terms with no specifics",
    "The response describes how it meets the requirement with named specifics",
    "The response describes how it meets the requirement and cites evidence, references, or measurements",
]

DEFAULT_CRITERIA = {
    "functional_fit": {
        "requirement": "The system supports multi-entity consolidation with entity-level access control and a consolidated group view.",
        "weight": 1.0,
        "mandatory": None,
    },
    "integration": {
        "requirement": "The system integrates with SAP S/4HANA and with Workday, with documented, supported connectors.",
        "weight": 0.9,
        "mandatory": "The response states that supported connectors for both named systems exist today, rather than on a roadmap.",
    },
    "security": {
        "requirement": "The supplier maintains an independently audited security certification and supports SSO with SCIM provisioning.",
        "weight": 0.8,
        "mandatory": "The response states that a current, independently audited certification such as ISO 27001 or SOC 2 Type II is held.",
    },
    "data_residency": {
        "requirement": "All customer data is stored and processed within the EU, including backups and support access.",
        "weight": 0.7,
        "mandatory": "The response states that data, backups and support access all remain within the EU.",
    },
    "implementation": {
        "requirement": "The supplier provides a staffed implementation plan with named roles, a migration approach, and defined acceptance stages.",
        "weight": 0.8,
        "mandatory": None,
    },
    "support_model": {
        "requirement": "The supplier offers a defined support model with response commitments and a named account contact.",
        "weight": 0.6,
        "mandatory": None,
    },
    "commercial": {
        "requirement": "Pricing is transparent, with all mandatory modules, implementation fees and uplift terms stated.",
        "weight": 1.0,
        "mandatory": None,
    },
    "references": {
        "requirement": "The supplier provides comparable customer references in the same sector and of similar scale.",
        "weight": 0.5,
        "mandatory": None,
    },
}


def build_questions(responses: list[dict], criteria: dict) -> dict:
    """One quality Score per criterion, plus a Noul wherever a gate is defined."""
    questions: dict[str, dict] = {}
    for index in range(len(responses)):
        for key, spec in criteria.items():
            reference = {
                "vendor_response": f"`responses[{index}].sections.{key}`",
                "requirement": spec["requirement"],
            }
            questions[f"q_{key}__{index}"] = score(
                {"task": "How well does this vendor response address the stated requirement?", **reference},
                QUALITY_LEVELS,
            )
            if spec.get("mandatory"):
                questions[f"m_{key}__{index}"] = noul(
                    {
                        "task": "Does the vendor response satisfy this mandatory requirement?",
                        "mandatory_requirement": spec["mandatory"],
                        "vendor_response": f"`responses[{index}].sections.{key}`",
                    },
                    true="The response states plainly that this is satisfied today",
                    false="The response is silent, hedged, conditional, or places it in the future",
                )
    return questions


def weighted_score(judgment: dict, weights: dict[str, float]) -> float:
    """0..100. Rebuilt on every slider move; no inference involved."""
    weight_sum = sum(weights.values()) or 1.0
    top = len(QUALITY_LEVELS) - 1
    raw = sum(weights[key] * (judgment[key] / top) for key in weights)
    return 100.0 * raw / weight_sum


def gate_failures(judgment: dict, criteria: dict, gate_at: float) -> list[str]:
    """Mandatory requirements are gates, not weighted contributions."""
    return [
        key
        for key, spec in criteria.items()
        if spec.get("mandatory") and judgment.get(f"m_{key}", 1.0) < gate_at
    ]


def rank(
    responses: list[dict], judgments: list[dict], criteria: dict, weights: dict, gate_at: float
) -> pd.DataFrame:
    rows = []
    for response, judgment in zip(responses, judgments):
        failures = gate_failures(judgment, criteria, gate_at)
        rows.append(
            {
                "vendor": response["vendor"],
                "compliant": not failures,
                "score": round(weighted_score(judgment, weights), 1),
                "failed gates": ", ".join(failures) or "—",
                **{key: round(judgment[key], 2) for key in criteria},
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values(["compliant", "score"], ascending=[False, False])


def main() -> None:
    st.set_page_config(page_title="Vendor RFP Scorer", page_icon="📋", layout="wide")
    st.title("📋 Vendor RFP Scorer")
    st.caption("Score the responses once. Re-negotiate the weighting for free.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    criteria = DEFAULT_CRITERIA

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Scoring model")
        st.caption("This is the panel's negotiation. Every change is free.")
        weights = {
            key: st.slider(key.replace("_", " ").title(), 0.0, 2.0, spec["weight"], 0.1)
            for key, spec in criteria.items()
        }
        st.divider()
        gate_at = st.slider("Mandatory requirement passes at P(yes) ≥", 0.5, 0.99, 0.70, 0.01)

    upload = st.file_uploader("Responses (JSON list of {vendor, sections})", type=["json"])
    responses = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    with st.expander("Criteria and mandatory gates"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "criterion": key,
                        "requirement": spec["requirement"],
                        "gate": spec.get("mandatory") or "—",
                    }
                    for key, spec in criteria.items()
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    if st.button(f"Score {len(responses)} responses", type="primary"):
        questions = build_questions(responses, criteria)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"responses": responses}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["scored"] = {
            "responses": responses,
            "judgments": [
                {
                    **{key: answers.score(f"q_{key}__{i}") for key in criteria},
                    **{
                        f"m_{key}": answers.noul(f"m_{key}__{i}")
                        for key, spec in criteria.items()
                        if spec.get("mandatory")
                    },
                }
                for i in range(len(responses))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    scored = st.session_state.get("scored")
    if not scored:
        st.info(f"{len(responses)} sample RFP responses bundled. Hit the button.")
        return

    frame = rank(scored["responses"], scored["judgments"], criteria, weights, gate_at)
    compliant = frame[frame["compliant"]]

    columns = st.columns(4)
    columns[0].metric("Responses", len(frame))
    columns[1].metric("Compliant", len(compliant))
    columns[2].metric("Failed a gate", len(frame) - len(compliant))
    columns[3].metric(
        "Leader", compliant.iloc[0]["vendor"] if len(compliant) else "none compliant"
    )

    st.subheader("Ranking")
    st.caption("Vendors failing a mandatory gate are listed last regardless of score.")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download rfp_scores.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="rfp_scores.csv",
        mime="text/csv",
    )

    st.subheader("Where the responses are weakest")
    st.bar_chart(frame[list(criteria)].mean().sort_values())

    st.subheader("Score by criterion")
    st.dataframe(
        frame.set_index("vendor")[list(criteria)].T,
        width="stretch",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", scored["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{scored['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${scored['cost']:.6f}")
    st.caption(
        f"{scored['tokens']:,} input tokens for {len(frame)} vendors × {len(criteria)} "
        "criteria. Every weight change above re-ran the scoring model for $0.00."
    )


if __name__ == "__main__":
    main()
