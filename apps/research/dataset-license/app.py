"""Dataset Licence -- can we legally use this dataset the way we intend to?

NOT LEGAL ADVICE. See the README. This is a triage tool that tells a researcher
which datasets need a lawyer, not which ones are cleared.

The question is never "is this licence permissive?". It is always "does this
licence permit THIS use?" -- and the same licence gives opposite answers to
"train an internal model" and "ship the weights in a product". So intent is
first-class state, and every (dataset, intended use) pair is judged on its own.

Two Nouls per pair, deliberately, never one:

    grant   -- the licence affirmatively permits this use
    forbid  -- some clause in the licence restricts this use

These are NOT complements. A licence can be silent on a use (grant low, forbid
low) or self-contradictory across clauses (both high), and both of those are
findings that "is it allowed? 0.6" would hide. Python requires grant high AND
forbid low; anything else routes to a human. That is a veto rule, not an
average -- one non-commercial clause disqualifies a commercial use no matter how
generous the rest of the licence is.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_licences.json"

# The uses a research group actually argues about.
USES = {
    "internal_research": {
        "label": "Internal, non-commercial research analysis",
        "description": "Analysing the data inside our institution for a non-commercial research project.",
    },
    "publish_findings": {
        "label": "Publish findings and quoted examples",
        "description": "Publishing results derived from the data, including short quoted or illustrated examples, in an academic paper.",
    },
    "train_model": {
        "label": "Train a machine learning model",
        "description": "Using the data as training material for a machine learning model.",
    },
    "release_model": {
        "label": "Release the trained model publicly",
        "description": "Distributing the resulting model weights or a derived artefact to people outside our institution.",
    },
    "redistribute": {
        "label": "Redistribute the dataset itself",
        "description": "Passing copies of the data, or a modified version of it, to third parties.",
    },
    "commercial": {
        "label": "Use in a commercial product",
        "description": "Using the data, or something derived from it, in a product or service we charge money for.",
    },
}

# Independent conditions that attach to a use without forbidding it.
OBLIGATIONS = {
    "attribution": {
        "label": "Attribution required",
        "question": "Does this licence require that the original source or authors be credited when the data or a derivative is used or distributed?",
        "true": "The text requires credit, citation, attribution or preservation of author notices",
        "false": "The text imposes no crediting requirement",
    },
    "share_alike": {
        "label": "Share-alike / copyleft",
        "question": "Does this licence require that derivative works be distributed under the same or a compatible licence?",
        "true": "The text requires derivatives to carry the same or an equivalent licence",
        "false": "Derivatives may be licensed however the recipient chooses",
    },
    "notify": {
        "label": "Registration or notification required",
        "question": "Does this licence require registering, requesting permission, or notifying the provider before or during use?",
        "true": "The text requires an application, signed agreement, account, or notice to the provider",
        "false": "The data may be used without contacting anyone",
    },
    "no_reidentification": {
        "label": "Re-identification prohibited",
        "question": "Does this licence prohibit attempting to identify the individuals described in the data, or linking it to other data to do so?",
        "true": "The text forbids re-identification or linkage intended to identify individuals",
        "false": "The text says nothing about identifying individuals",
    },
    "ethics_gate": {
        "label": "Ethics approval required",
        "question": "Does this licence condition use on approval from an ethics committee or equivalent review body?",
        "true": "The text requires ethical review, IRB approval or a named governance sign-off before use",
        "false": "No ethical review is made a condition of use",
    },
}

FAMILY_OPTIONS = {
    "public_domain": "The text dedicates the work to the public domain or waives essentially all rights",
    "permissive": "The text allows broad reuse subject only to crediting the source",
    "copyleft": "The text allows reuse but requires derivatives to carry the same terms",
    "noncommercial": "The text allows reuse but carves out commercial use as not permitted",
    "research_only": "The text restricts use to academic or research purposes under a signed or approved agreement",
    "proprietary": "The text reserves rights to the provider and grants only narrow, specific permission",
    "unclear": "The text does not fit any of the above families, or is too fragmentary to place",
}


def build_questions(datasets: list[dict], uses: dict[str, dict] = USES) -> dict:
    """Per dataset: 2 Nouls per intended use, plus obligations, plus one family Choice."""
    questions: dict[str, dict] = {}
    for index in range(len(datasets)):
        licence = f"`datasets[{index}].licence_text`"
        for key, use in uses.items():
            questions[f"grant_{index}_{key}"] = noul(
                {
                    "task": "Does this licence affirmatively permit the intended use below?",
                    "intended_use": use["description"],
                    "licence_text": licence,
                },
                true="The licence text grants a permission that covers this use",
                false="The licence text grants no permission covering this use, or is silent on it",
            )
            questions[f"forbid_{index}_{key}"] = noul(
                {
                    "task": "Does any clause of this licence restrict or prohibit the intended use below?",
                    "intended_use": use["description"],
                    "licence_text": licence,
                },
                true="Some clause restricts, conditions away, or outright forbids this use",
                false="No clause in the text stands in the way of this use",
            )
        for key, obligation in OBLIGATIONS.items():
            questions[f"obl_{index}_{key}"] = noul(
                {"task": obligation["question"], "licence_text": licence},
                true=obligation["true"],
                false=obligation["false"],
            )
        questions[f"family_{index}"] = choice(
            {
                "task": "Which family does this licence text most closely belong to?",
                "licence_text": licence,
            },
            FAMILY_OPTIONS,
        )
    return questions


def judge(grant: float, forbid: float, yes_at: float, no_at: float) -> tuple[str, str]:
    """One (dataset, use) pair. Conjunctive: permission requires BOTH signals to agree.

    The four corners are all real and all different, which is why this is two
    Nouls and not one probability.
    """
    if forbid >= yes_at:
        return "prohibited", f"a clause restricts this use (P={forbid:.2f})"
    if grant <= no_at:
        return "not_granted", f"no clause permits this use (P(grant)={grant:.2f})"
    if grant >= yes_at and forbid <= no_at:
        return "permitted", f"granted (P={grant:.2f}), unrestricted (P={forbid:.2f})"
    return "unclear", f"grant {grant:.2f} / restriction {forbid:.2f} — not clear-cut"


def overall(verdicts: dict[str, str]) -> tuple[str, list[str]]:
    """Veto across the uses the researcher actually intends. Never an average.

    One prohibited use blocks the plan as stated; the fix is to narrow the
    intent, which the user does by un-ticking a use and re-deciding for free.
    """
    blocked = [k for k, v in verdicts.items() if v in ("prohibited", "not_granted")]
    if blocked:
        return "blocked", blocked
    unclear = [k for k, v in verdicts.items() if v == "unclear"]
    if unclear:
        return "seek_advice", unclear
    return "consistent", []


def main() -> None:
    st.set_page_config(page_title="Dataset Licence", page_icon="⚖️", layout="wide")
    st.title("⚖️ Dataset Licence")
    st.caption("Judge a stated intent against licence text. **Not legal advice** — see the README.")
    st.warning(
        "This is a triage aid, not legal advice. It tells you which datasets need a "
        "qualified person to look at them. It never clears one for use.",
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
        st.subheader("Thresholds")
        yes_at = st.slider("Treat a signal at or above this as yes", 0.5, 0.99, 0.75, 0.01)
        no_at = st.slider("Treat a signal at or below this as no", 0.01, 0.5, 0.25, 0.01)
        st.caption("Permission needs grant ≥ yes AND restriction ≤ no. Everything else is advice-seeking.")

    st.subheader("What do you intend to do with the data?")
    intended = [
        key
        for key, use in USES.items()
        if st.checkbox(use["label"], value=key in ("internal_research", "publish_findings", "train_model"), key=f"use_{key}")
    ]
    if not intended:
        st.info("Tick at least one intended use.")
        return

    uploaded = st.file_uploader(
        "Datasets (JSON list of {id, name, licence_name, licence_text})", type=["json"]
    )
    if uploaded:
        datasets = json.loads(uploaded.read().decode("utf-8"))
    else:
        datasets = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(datasets)} licences.")

    if not st.button(f"Assess {len(datasets)} datasets", type="primary"):
        return

    # Every use is judged, so un-ticking one in the sidebar costs no inference.
    questions = build_questions(datasets)
    try:
        with st.spinner(f"{len(questions)} judgments in one request..."):
            with JevClient(provider=provider) as client:
                answers = client.ask({"datasets": datasets}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    details = []
    for index, dataset in enumerate(datasets):
        verdicts: dict[str, str] = {}
        reasons: dict[str, str] = {}
        signals: dict[str, tuple[float, float]] = {}
        for key in USES:
            grant = answers.noul(f"grant_{index}_{key}")
            forbid = answers.noul(f"forbid_{index}_{key}")
            verdicts[key], reasons[key] = judge(grant, forbid, yes_at, no_at)
            signals[key] = (grant, forbid)
        obligations = {
            key: answers.noul(f"obl_{index}_{key}") for key in OBLIGATIONS
        }
        family = answers.choice(f"family_{index}")
        result, drivers = overall({k: verdicts[k] for k in intended})
        details.append((dataset, verdicts, reasons, signals, obligations, family, result, drivers))
        row = {
            "dataset": dataset["name"],
            "licence": dataset.get("licence_name", "—"),
            "family": family,
            "verdict": result,
        }
        row.update({USES[k]["label"]: verdicts[k] for k in intended})
        rows.append(row)

    counts = {k: sum(1 for d in details if d[6] == k) for k in ("consistent", "seek_advice", "blocked")}
    columns = st.columns(3)
    columns[0].metric("Consistent with intent", counts["consistent"])
    columns[1].metric("Ask a lawyer", counts["seek_advice"])
    columns[2].metric("Blocked as stated", counts["blocked"])

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    for dataset, verdicts, reasons, signals, obligations, family, result, drivers in details:
        icon = {"consistent": "✅", "seek_advice": "⚠️", "blocked": "🛑"}[result]
        title = f"{icon} {dataset['name']} — {dataset.get('licence_name', 'licence')} — {result}"
        with st.expander(title):
            if drivers:
                st.markdown("**Driven by:** " + ", ".join(USES[k]["label"] for k in drivers))
            st.markdown(f"**Family:** {family} — {FAMILY_OPTIONS[family]}")
            for key in USES:
                mark = {
                    "permitted": "✅",
                    "prohibited": "🛑",
                    "not_granted": "🛑",
                    "unclear": "⚠️",
                }[verdicts[key]]
                scope = "" if key in intended else " *(not intended)*"
                grant, forbid = signals[key]
                st.caption(
                    f"{mark} {USES[key]['label']}{scope}: {verdicts[key]} — "
                    f"grant {grant:.2f}, restriction {forbid:.2f}"
                )
            st.markdown("**Obligations that would attach**")
            for key, probability in obligations.items():
                mark = "📌" if probability >= yes_at else ("⚠️" if probability > no_at else "·")
                st.caption(f"{mark} {probability:.2f} — {OBLIGATIONS[key]['label']}")
            st.text(dataset["licence_text"])

    st.divider()
    st.caption(
        f"{len(datasets)} datasets × ({len(USES)}×2 uses + {len(OBLIGATIONS)} obligations + 1 family) "
        f"= {len(questions)} judgments, 1 request, {answers.elapsed_s:.1f}s, "
        f"${answers.cost_usd:.6f} ({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
