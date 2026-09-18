"""Redaction Checker -- find PII and confidential detail before a document ships.

The design point: hazard classes are NOT levels on one rubric and NOT options in
one Choice. A single paragraph can carry a home address AND a salary AND an
unreleased codename at the same time, so they are independent properties and get
one Noul each -- which is what makes them independently thresholdable.

That independence is the whole product. Legal privilege at 0.35 is worth a human
look. A customer name at 0.35 is noise. One number cannot express both, and a
rubric that averaged them would hide the privilege behind the noise.

A separate Score rates how much releasing the paragraph would actually cost,
which is a different question from which hazards are present.

This finds CANDIDATES for a human. It is not a redaction guarantee.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_document.txt"

# Rough chars-per-token; only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4

# (key, label, question, true criteria, false criteria). One Noul each, one
# slider each. The false criteria carry the boundary cases on purpose: the model
# reads literally, so "a public job title is not a hazard" has to be written down.
HAZARDS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "personal_identity",
        "Named individual",
        "Does this paragraph identify a specific individual person by name?",
        "A particular person is named, or is described specifically enough that a "
        "reader could work out who they are from the description alone",
        "No individual is identified, or the only people referred to are teams, "
        "roles, or job titles with nobody named",
    ),
    (
        "contact_details",
        "Contact details",
        "Does this paragraph contain a way to contact or locate a specific person "
        "directly, outside of work channels?",
        "A personal email address, a personal or direct phone number, a home or "
        "residential address, or a personal messaging handle appears here",
        "No personal contact route appears, or the only contact route is a general "
        "company address, a shared team inbox, or a published support line",
    ),
    (
        "financial_personal",
        "Individual pay or finances",
        "Does this paragraph disclose an individual's pay, compensation, or "
        "personal financial position?",
        "A salary, bonus, equity grant, offer figure, severance amount, or an "
        "individual's personal financial circumstance appears here",
        "No individual's finances appear. Company-level revenue, budgets, costs, "
        "contract values, or service credits are not an individual's finances",
    ),
    (
        "health",
        "Health or medical detail",
        "Does this paragraph disclose health, medical, disability, or personal "
        "leave information about an identifiable person?",
        "A medical condition, injury, diagnosis, treatment, disability, mental "
        "health matter, or medical leave is attributed to a specific person",
        "No health information about a person appears. System health, service "
        "health, and organisational health are not medical information",
    ),
    (
        "customer_confidential",
        "Customer named under obligation",
        "Does this paragraph name a customer or partner organisation whose "
        "relationship with us is described as unannounced, confidential, or "
        "restricted from disclosure?",
        "A customer or partner is named and the text indicates the relationship is "
        "not public, is under an obligation of confidence, or must not be disclosed",
        "No customer is named, or the named organisation is a publicly known "
        "customer, a vendor we openly use, or is mentioned with no restriction",
    ),
    (
        "unreleased_product",
        "Unreleased product or roadmap",
        "Does this paragraph reveal a product, feature, project, or codename that "
        "the text indicates has not been publicly announced?",
        "An internal codename, an unannounced product, or a forward roadmap "
        "commitment appears, and the text signals it is not yet public",
        "Only shipped and publicly known products, generic engineering work, or "
        "internal incident identifiers appear",
    ),
    (
        "legal_privilege",
        "Legal privilege",
        "Does this paragraph reproduce or describe legal advice, or a legal "
        "strategy position, that would ordinarily be privileged?",
        "Advice from counsel, a privileged memorandum, litigation strategy, or a "
        "reasoned position about our exposure in a dispute is described here",
        "No legal advice or strategy appears. Statements of plain fact, "
        "contractual terms, and regulatory obligations are not privileged advice",
    ),
    (
        "security_sensitive",
        "Security-sensitive detail",
        "Does this paragraph expose an internal system detail that would be useful "
        "to someone attacking us?",
        "An internal hostname, an internal endpoint or port, a service account or "
        "credential name, a key, or an unremediated specific vulnerability appears",
        "Only general architectural description, named public services, or already "
        "remediated issues described without exploitable specifics appear",
    ),
)

# Ordered rubric: each level stands alone and describes a concrete situation.
SEVERITY_LEVELS = [
    "Nothing in this paragraph would embarrass anyone if the entire document were "
    "posted on a public website tomorrow",
    "A person or organisation is identifiable here, but only through a role or fact "
    "the company already publicises about itself",
    "Something here is internal and unpublished, so releasing it would be awkward "
    "or commercially unhelpful without breaching any obligation",
    "Releasing this paragraph would breach a confidentiality obligation, waive legal "
    "privilege, expose an individual's private life, or hand an attacker something "
    "they could use",
]


def split_paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs, whitespace normalised.

    ponytail: no length floor here, unlike a clause splitter. A two-word line can
    be a phone number, and dropping it is the one failure this app cannot afford.
    """
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def build_questions(paragraphs: list[str]) -> dict:
    """Per paragraph: one Noul per hazard class, plus one severity Score.

    Every question points at the paragraph by path rather than restating it, so
    the document is ingested once and the model is not re-reading copies of it.
    """
    questions: dict[str, dict] = {}
    for index in range(len(paragraphs)):
        reference = f"`paragraphs[{index}].text`"
        for key, _label, question, true_criteria, false_criteria in HAZARDS:
            questions[f"{key}_{index}"] = noul(
                {"task": question, "paragraph": reference},
                true=true_criteria,
                false=false_criteria,
            )
        questions[f"severity_{index}"] = score(
            {
                "task": (
                    "Rate the harm of releasing this paragraph to an external party "
                    "outside the company."
                ),
                "paragraph": reference,
            },
            SEVERITY_LEVELS,
        )
    return questions


def fired_classes(probabilities: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    """Which hazard classes cleared their OWN threshold. Separate dials, by design."""
    return [key for key, _l, _q, _t, _f in HAZARDS if probabilities.get(key, 0.0) >= thresholds[key]]


def needs_review(fired: list[str], severity: float, severity_threshold: float) -> bool:
    """Flag on either signal. A hazard the Score underrates still gets a human,
    and a paragraph that reads badly still gets one even if no class fired."""
    return bool(fired) or severity >= severity_threshold


def class_counts(rows: list[dict]) -> dict[str, int]:
    """Per-class totals. Counting is arithmetic, so Python does it, never the model."""
    return {
        key: sum(1 for row in rows if key in row["fired"])
        for key, _l, _q, _t, _f in HAZARDS
    }


def main() -> None:
    st.set_page_config(page_title="Redaction Checker", page_icon="🖍️", layout="wide")
    st.title("🖍️ Redaction Checker")
    st.caption(
        "Find PII and confidential detail before a document goes out. "
        "**Candidates for a human reviewer — not a redaction guarantee.**"
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(
            f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok",
            language=None,
        )
        st.caption("Change `provider` in providers.toml, or set `JEV_PROVIDER`.")
        st.divider()
        st.subheader("Per-class thresholds")
        st.caption(
            "One dial per hazard class. Privilege and health should sit low — you "
            "want the false positives. Set them from your own release policy."
        )
        # Defaults: classes where a miss is unrecoverable get a lower bar.
        defaults = {
            "personal_identity": 0.60,
            "contact_details": 0.40,
            "financial_personal": 0.40,
            "health": 0.30,
            "customer_confidential": 0.45,
            "unreleased_product": 0.50,
            "legal_privilege": 0.30,
            "security_sensitive": 0.40,
        }
        thresholds = {
            key: st.slider(label, 0.0, 1.0, defaults[key], 0.05, key=f"th_{key}")
            for key, label, _q, _t, _f in HAZARDS
        }
        st.divider()
        severity_threshold = st.slider(
            "Severity at or above which we flag regardless of class",
            0.0,
            float(len(SEVERITY_LEVELS) - 1),
            2.0,
            0.1,
        )

    text = st.text_area(
        "Document about to go out",
        value=SAMPLE.read_text(encoding="utf-8") if SAMPLE.is_file() else "",
        height=300,
    )

    if not st.button("Scan for release", type="primary"):
        st.info("The bundled sample document is loaded. Hit the button to scan it.")
        return

    paragraphs = split_paragraphs(text)
    if not paragraphs:
        st.warning("No paragraphs found. Separate paragraphs with a blank line.")
        return

    estimated_tokens = len(text) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Scan the document in sections."
        )
        return

    questions = build_questions(paragraphs)
    state = {"paragraphs": [{"text": paragraph} for paragraph in paragraphs]}

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    labels = {key: label for key, label, _q, _t, _f in HAZARDS}
    rows = []
    for index, paragraph in enumerate(paragraphs):
        probabilities = {
            key: answers.noul(f"{key}_{index}") for key, _l, _q, _t, _f in HAZARDS
        }
        severity = answers.score(f"severity_{index}")
        fired = fired_classes(probabilities, thresholds)
        rows.append(
            {
                "index": index,
                "text": paragraph,
                "probabilities": probabilities,
                "severity": severity,
                "fired": fired,
                "flagged": needs_review(fired, severity, severity_threshold),
            }
        )

    worklist = sorted(
        (row for row in rows if row["flagged"]), key=lambda r: -r["severity"]
    )

    left, right = st.columns([2, 1])
    with left:
        st.subheader(f"Redaction worklist ({len(worklist)} of {len(paragraphs)} paragraphs)")
        if not worklist:
            st.success(
                "No paragraph cleared any threshold. Lower the dials before you "
                "conclude the document is clean."
            )
        for row in worklist:
            with st.container(border=True):
                st.markdown(
                    f"**¶{row['index'] + 1}** · severity {row['severity']:.2f} / "
                    f"{len(SEVERITY_LEVELS) - 1} · "
                    + (", ".join(labels[key] for key in row["fired"]) or "severity only")
                )
                st.write(row["text"])
                st.caption(
                    " · ".join(
                        f"{labels[key]} {row['probabilities'][key]:.2f}"
                        for key in row["fired"]
                    )
                    or "No class cleared its own threshold; flagged on severity alone."
                )

    with right:
        st.subheader("Hazards by class")
        counts = class_counts(rows)
        st.dataframe(
            pd.DataFrame(
                [
                    {"Hazard class": labels[key], "Paragraphs": counts[key],
                     "Threshold": thresholds[key]}
                    for key, _l, _q, _t, _f in HAZARDS
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Each row has its own dial in the sidebar. That is the point of one "
            "Noul per class — move privilege without moving customer names."
        )

    with st.expander("Every paragraph, with all class probabilities"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "¶": row["index"] + 1,
                        "Severity": round(row["severity"], 2),
                        "Flagged": row["flagged"],
                        **{labels[key]: round(row["probabilities"][key], 2)
                           for key, _l, _q, _t, _f in HAZARDS},
                    }
                    for row in rows
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    st.warning(
        "This is a **candidate list for a human reviewer**, not a redaction "
        "guarantee. Nothing here proves the paragraphs it did not flag are safe."
    )

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Questions", len(questions))
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{answers.elapsed_s:.1f} s")
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{len(paragraphs)} paragraphs × {len(HAZARDS) + 1} questions = "
        f"{len(questions)} judgments in 1 request, "
        f"{answers.input_tokens:,} input tokens."
    )


if __name__ == "__main__":
    main()
