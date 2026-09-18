"""Vendor Risk -- screen suppliers against risk criteria drawn from their own documents.

The structural point: SEVEN INDEPENDENT RISKS, SEVEN INDEPENDENT GATES.

A supplier can be financially shaky AND well-certified. It can be immaculate on
paper AND the only firm that can do the job. These are not points on a rubric and
they are not options in a Choice -- they are seven separate yes/no questions,
each carrying its own probability, each compared against its own threshold.

The thresholds differ on purpose. Missing an undisclosed sub-processor is a
finding; missing a sanctions exposure is a regulatory event. So the sanctions
gate sits at 0.25 and the sub-processor gate at 0.55, and neither number appears
anywhere near an instruction -- they are sliders, tuned without re-running
inference.

Anything numeric (spend at risk, how many criteria tripped, the tier) is pandas.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_vendors.json"

# Each criterion is an independent property of the supplier. Separate Noul,
# separate default gate, and `hard_stop` marks the ones no amount of other
# evidence offsets.
CRITERIA = [
    {
        "id": "offshore_subprocessing",
        "label": "Undisclosed or unmanaged sub-processing",
        "gate": 0.55,
        "hard_stop": False,
        "true": "Delivery depends on a subcontractor, offshore team or fourth party that the documents leave unnamed, unvetted, or outside the buyer's contractual reach.",
        "false": "Any subcontractors are named, contractually bound and disclosed, or the service involves none at all.",
    },
    {
        "id": "no_security_assurance",
        "label": "No independent security assurance",
        "gate": 0.55,
        "hard_stop": False,
        "true": "The supplier holds no completed independent security certification or audit report covering the service, or offers only an intention to obtain one.",
        "false": "A completed, current, independently issued certification or audit report covers the service being bought.",
    },
    {
        "id": "data_leaves_region",
        "label": "Data leaves the contracted region",
        "gate": 0.45,
        "hard_stop": False,
        "true": "Customer or personal data is stored, replicated or accessible from outside the region the contract commits to, including for support, backup or disaster recovery.",
        "false": "All storage and access stays inside the contracted region, or the supplier handles no customer or personal data at all.",
    },
    {
        "id": "financial_distress",
        "label": "Financial distress",
        "gate": 0.45,
        "hard_stop": False,
        "true": "The filings or public record show going-concern doubt, net liabilities, judgments, late filings, or dependence on director or shareholder support to keep trading.",
        "false": "The financial picture described is that of a solvent business trading normally.",
    },
    {
        "id": "single_point_of_failure",
        "label": "No viable alternative supplier",
        "gate": 0.5,
        "hard_stop": False,
        "true": "The documents indicate the buyer could not readily move this service elsewhere: a sole facility, a bespoke capability, no exit assistance, or no documented alternative.",
        "false": "The service is a commodity with alternatives, or the contract provides exit assistance and portability.",
    },
    {
        "id": "regulatory_or_legal_exposure",
        "label": "Regulatory, sanctions or legal exposure",
        "gate": 0.25,
        "hard_stop": True,
        "true": "The record describes regulatory action, sanctions exposure, opaque ownership, undisclosed beneficial owners, or litigation touching this entity or people who control it.",
        "false": "Nothing adverse beyond routine, resolved matters unconnected to how the entity is controlled.",
    },
    {
        "id": "contract_lacks_controls",
        "label": "Contract lacks basic controls",
        "gate": 0.5,
        "hard_stop": False,
        "true": "The agreement lacks breach notification, audit rights, or data protection terms appropriate to the service being bought.",
        "false": "The agreement carries breach notification, audit rights and data terms proportionate to the service.",
    },
]


def build_questions(vendors: list[dict]) -> dict:
    """One Noul per (vendor, criterion). Independent properties, one request."""
    questions: dict[str, dict] = {}
    for vendor_index in range(len(vendors)):
        reference = {
            "service_bought": f"`vendors[{vendor_index}].service`",
            "company_profile": f"`vendors[{vendor_index}].documents.company_profile`",
            "security_questionnaire": f"`vendors[{vendor_index}].documents.security_questionnaire`",
            "contract_summary": f"`vendors[{vendor_index}].documents.contract_summary`",
            "public_record": f"`vendors[{vendor_index}].documents.public_record`",
        }
        for criterion in CRITERIA:
            questions[f"{criterion['id']}_{vendor_index}"] = noul(
                {
                    "task": "Judge this one risk criterion against the supplier documents below, on what the documents actually say.",
                    "criterion": criterion["label"],
                    "supplier": reference,
                    "note": "Silence is evidence. A questionnaire that does not mention a control is not a questionnaire that confirms one.",
                },
                true=criterion["true"],
                false=criterion["false"],
            )
    return questions


def flagged(probabilities: dict[str, float], gates: dict[str, float]) -> list[str]:
    """Each criterion against its OWN gate. No averaging, ever."""
    return [key for key, value in probabilities.items() if value >= gates[key]]


def tier(flags: list[str], enhanced_at: int) -> str:
    """Blocked, enhanced, monitor, standard. Policy in Python; counting in Python."""
    if any(c["id"] in flags for c in CRITERIA if c["hard_stop"]):
        return "blocked"
    if len(flags) >= enhanced_at:
        return "enhanced_diligence"
    if flags:
        return "monitor"
    return "standard"


def main() -> None:
    st.set_page_config(page_title="Vendor Risk", page_icon="🛡️", layout="wide")
    st.title("🛡️ Vendor Risk")
    st.caption("Screen suppliers against risk criteria from their own documents. Seven risks, seven gates.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Per-criterion gates")
        st.caption("Different defaults because the consequences differ.")
        gates = {
            criterion["id"]: st.slider(
                criterion["label"], 0.0, 1.0, criterion["gate"], 0.05,
                help="hard stop" if criterion["hard_stop"] else None,
            )
            for criterion in CRITERIA
        }
        st.divider()
        enhanced_at = st.slider("Criteria tripped before enhanced diligence", 1, len(CRITERIA), 3, 1)

    uploaded = st.file_uploader("Vendors JSON", type=["json"])
    vendors = (
        json.loads(uploaded.read().decode("utf-8"))
        if uploaded
        else json.loads(SAMPLE.read_text(encoding="utf-8"))
    )
    if uploaded is None:
        st.caption(f"Bundled sample: {len(vendors)} suppliers with profile, questionnaire, contract and public record.")

    st.dataframe(
        pd.DataFrame([{"vendor": v["vendor"], "service": v["service"], "annual_spend": v["annual_spend"]} for v in vendors]),
        width="stretch", hide_index=True,
    )

    questions_count = len(vendors) * len(CRITERIA)
    if not st.button(f"Screen {len(vendors)} suppliers ({questions_count} judgments)", type="primary"):
        return

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"vendors": vendors}, build_questions(vendors))
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for vendor_index, vendor in enumerate(vendors):
        probabilities = {
            criterion["id"]: answers.noul(f"{criterion['id']}_{vendor_index}") for criterion in CRITERIA
        }
        hits = flagged(probabilities, gates)
        rows.append(
            {
                "vendor": vendor["vendor"],
                "service": vendor["service"],
                "annual_spend": float(vendor["annual_spend"]),
                "tier": tier(hits, enhanced_at),
                "criteria_tripped": len(hits),
                "tripped": ", ".join(hits),
                **probabilities,
            }
        )

    out = pd.DataFrame(rows)
    blocked = out[out["tier"] == "blocked"]
    enhanced = out[out["tier"] == "enhanced_diligence"]

    columns = st.columns(4)
    columns[0].metric("Suppliers", len(out))
    columns[1].metric("Blocked", len(blocked))
    columns[2].metric("Enhanced diligence", len(enhanced))
    columns[3].metric("Spend not in 'standard'", f"{out[out['tier'] != 'standard']['annual_spend'].sum():,.0f}")

    st.subheader("Screening result")
    st.dataframe(
        out[["vendor", "service", "annual_spend", "tier", "criteria_tripped", "tripped"]]
        .sort_values(["tier", "annual_spend"], ascending=[True, False]),
        width="stretch", hide_index=True,
    )

    st.subheader("Raw probabilities, per criterion")
    st.caption(
        "Read across a row and you get the shape of the supplier, not a score. "
        "Orrell is financially shaky and carries no data risk at all; Tessellate is "
        "spotless and irreplaceable. A single composite number would have flattened "
        "both into 'medium' and told you nothing you could act on."
    )
    criterion_ids = [c["id"] for c in CRITERIA]
    st.dataframe(
        out.set_index("vendor")[criterion_ids].style.background_gradient(cmap="Reds", vmin=0, vmax=1),
        width="stretch",
    )

    st.subheader("Exposure by criterion")
    st.caption("Annual spend sitting behind each tripped criterion — pandas, from the flags.")
    exposure = pd.Series(
        {
            criterion["id"]: float(out.loc[out[criterion["id"]] >= gates[criterion["id"]], "annual_spend"].sum())
            for criterion in CRITERIA
        }
    )
    st.bar_chart(exposure)

    st.download_button(
        "Download screening CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="vendor_risk.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {questions_count} judgments across {len(vendors)} suppliers, "
        f"{answers.input_tokens:,} input tokens, ${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s "
        f"— ${answers.cost_usd / len(vendors):.6f} per supplier."
    )


if __name__ == "__main__":
    main()
