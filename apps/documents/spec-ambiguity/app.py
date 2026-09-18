"""Spec Ambiguity -- find the sentences two engineers would implement differently.

The five ambiguity properties are INDEPENDENT: a sentence can lack an actor and
also lean on an undefined term, and "unquantified" is not a milder form of
"missing actor". So each is its own Noul with its own slider, rather than levels
on one rubric or options in one Choice. Collapsing them would destroy exactly
the thing a spec reviewer wants to tune: "I don't care about passive voice on
this project, I care about unhandled failure paths."

A Choice picks the dominant family (for grouping the report), and a Score rates
how expensive the ambiguity is to discover late. Everything for every sentence
goes in ONE request.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_spec.txt"
# Rough chars-per-token, only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4

# Independent properties -> one Noul each, one slider each.
HAZARDS: dict[str, dict[str, str]] = {
    "two_readings": {
        "label": "Two defensible readings",
        "task": "Could two competent engineers implement this sentence differently and each defend their reading as correct?",
        "true": "The wording admits at least two implementations that differ in observable behaviour, and the sentence does not say which is meant",
        "false": "Every competent reading of this sentence produces the same observable behaviour",
    },
    "missing_actor": {
        "label": "No actor named",
        "task": "Does this sentence describe an action without naming who or what performs it?",
        "true": "An action is specified but the performing component, service, or role is never named in the sentence",
        "false": "The sentence names the actor performing the action, or describes a state rather than an action",
    },
    "unquantified": {
        "label": "Unquantified quality bar",
        "task": "Does this sentence assert a quality bar without a measurable threshold an engineer could test against?",
        "true": "A quality is asserted using words like quickly, performant, large, reasonable, or appropriately, with no testable threshold given",
        "false": "Either no quality bar is asserted, or the threshold given is concrete enough to write a test against",
    },
    "undefined_term": {
        "label": "Undefined term",
        "task": "Does this sentence rely on a term of art that the rest of this specification never defines?",
        "true": "The sentence uses a term whose meaning drives behaviour, and no other sentence in the specification defines it",
        "false": "Every behaviour-driving term in the sentence is either ordinary English or defined elsewhere in the specification",
    },
    "unhandled_failure": {
        "label": "Failure path unstated",
        "task": "Does this sentence specify only the successful path, leaving the failure path unstated?",
        "true": "The sentence describes an operation that can fail, and neither it nor the rest of the specification says what happens when it does",
        "false": "Either the operation cannot fail, or the failure behaviour is stated here or elsewhere in the specification",
    },
}

FAMILIES: dict[str, str] = {
    "vague_quality": "A quality bar is asserted in words that cannot be turned into a test: quickly, performant, large, reasonable.",
    "dangling_scope": "A modifier or condition could attach to one item or to the whole list, changing who or what is covered.",
    "missing_actor": "An action is specified in the passive voice with no component, service, or role named as performing it.",
    "undefined_term": "A term that drives behaviour is used but never defined anywhere in the specification.",
    "and_or": "Coordination with and, or, and/or leaves it open whether the options are exclusive, inclusive, or both required.",
    "ambiguous_pronoun": "A pronoun or a bare 'it' could refer to more than one thing named earlier in the sentence.",
    "unhandled_failure": "Only the successful path is described; what happens when the operation fails is not stated.",
    "unambiguous": "This sentence admits only one reasonable implementation; there is no ambiguity to report.",
}

# Concrete situations, ordered by how expensive the ambiguity is to find late.
COST_LEVELS = [
    "Two readings differ only in wording; any implementation satisfies the intent and nobody would file a bug.",
    "Two readings differ in something cosmetic such as message text or ordering; a designer would notice in review.",
    "Two readings produce different behaviour that a code review or an integration test would catch before release.",
    "Two readings produce different data written to the database or different money moved, surfacing only in production.",
]

# Which hazard Noul each Choice family corresponds to, for cross-checking in the report.
FAMILY_TO_HAZARD = {
    "vague_quality": "unquantified",
    "missing_actor": "missing_actor",
    "undefined_term": "undefined_term",
    "unhandled_failure": "unhandled_failure",
}


def split_sentences(text: str) -> list[str]:
    """Split a spec into numbered sentences. Mechanical, so it stays in Python.

    Specs are usually one requirement per numbered line. When the text has no
    numbering, fall back to sentence-terminator splitting.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [re.sub(r"^\d+[.)]\s*", "", line) for line in lines if re.match(r"^\d+[.)]\s", line)]
    if numbered:
        return numbered
    joined = " ".join(lines)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", joined)
    return [p.strip() for p in parts if p.strip()]


def build_questions(sentences: list[str]) -> dict:
    """Five Nouls + one Choice + one Score per sentence, all in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(sentences)):
        reference = f"`sentences[{index}]`"
        for key, spec in HAZARDS.items():
            questions[f"{key}_{index}"] = noul(
                {
                    "task": spec["task"],
                    "sentence": reference,
                    "specification": "`sentences`",
                },
                true=spec["true"],
                false=spec["false"],
            )
        questions[f"family_{index}"] = choice(
            {
                "task": "Select the single ambiguity family that best describes the main problem with this sentence.",
                "sentence": reference,
                "note": "Choose 'unambiguous' when the sentence has no ambiguity worth reporting.",
            },
            FAMILIES,
        )
        questions[f"cost_{index}"] = score(
            {
                "task": "Rate what it would take to discover that this sentence was implemented the wrong way.",
                "sentence": reference,
                "specification": "`sentences`",
            },
            COST_LEVELS,
        )
    return questions


QUESTIONS_PER_SENTENCE = len(HAZARDS) + 2


def flagged_hazards(probabilities: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    """Which hazards fired, at each hazard's own threshold. Policy in Python."""
    return [key for key, value in probabilities.items() if value >= thresholds[key]]


def triage(
    probabilities: dict[str, float],
    cost: float,
    family: str,
    thresholds: dict[str, float],
    cost_cutoff: float,
) -> str:
    """Three buckets. The Choice alone is never allowed to flag a sentence.

    A sentence is 'rewrite' only when some hazard actually fired AND the cost of
    finding out late clears the cutoff. That combination is the point: the model
    supplies the evidence, this function is the policy, and moving a slider
    re-buckets everything with no further inference.
    """
    fired = flagged_hazards(probabilities, thresholds)
    if not fired:
        return "clear"
    if cost >= cost_cutoff:
        return "rewrite"
    if family == "unambiguous":
        return "clear"
    return "review"


def main() -> None:
    st.set_page_config(page_title="Spec Ambiguity", page_icon="🪤", layout="wide")
    st.title("🪤 Spec Ambiguity")
    st.caption(
        "Find the sentences in a specification that two engineers would read differently — "
        "before one of them builds the wrong thing."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.caption("Change `provider` in providers.toml, or set `JEV_PROVIDER`.")
        st.divider()
        st.subheader("Hazard thresholds")
        st.caption("One slider per hazard, because they are independent properties.")
        thresholds = {
            key: st.slider(spec["label"], 0.0, 1.0, 0.65, 0.05, key=f"th_{key}")
            for key, spec in HAZARDS.items()
        }
        st.divider()
        st.subheader("Severity")
        cost_cutoff = st.slider(
            "Rewrite when the late-discovery cost reaches",
            0.0,
            float(len(COST_LEVELS) - 1),
            2.0,
            0.1,
        )
        st.caption(f"0 = {COST_LEVELS[0][:40]}…  {len(COST_LEVELS) - 1} = {COST_LEVELS[-1][:40]}…")

    default_text = SAMPLE.read_text(encoding="utf-8") if SAMPLE.is_file() else ""
    uploaded = st.file_uploader("Upload a spec (.txt or .md)", type=["txt", "md"])
    text = uploaded.read().decode("utf-8", errors="replace") if uploaded else default_text
    text = st.text_area("Specification", value=text, height=300)

    if not st.button("Find ambiguity", type="primary") or not text.strip():
        st.info("The bundled sample spec is loaded. Hit **Find ambiguity** to run it.")
        return

    sentences = split_sentences(text)
    if not sentences:
        st.warning("No sentences found in that specification.")
        return

    estimated_tokens = len(text) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Split the spec by section, or switch provider."
        )
        return

    questions = build_questions(sentences)

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"sentences": sentences}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, sentence in enumerate(sentences):
        probabilities = {key: answers.noul(f"{key}_{index}") for key in HAZARDS}
        cost = answers.score(f"cost_{index}")
        family = answers.choice(f"family_{index}")
        rows.append(
            {
                "index": index,
                "sentence": sentence,
                "probabilities": probabilities,
                "cost": cost,
                "family": family,
                "family_confidence": answers.confidence(f"family_{index}"),
                "verdict": triage(probabilities, cost, family, thresholds, cost_cutoff),
            }
        )

    buckets = {name: [r for r in rows if r["verdict"] == name] for name in ("rewrite", "review", "clear")}
    columns = st.columns(3)
    columns[0].metric("Rewrite before build", len(buckets["rewrite"]))
    columns[1].metric("Worth a second look", len(buckets["review"]))
    columns[2].metric("Clear", len(buckets["clear"]))

    tabs = st.tabs(
        [
            f"Rewrite ({len(buckets['rewrite'])})",
            f"Review ({len(buckets['review'])})",
            f"Clear ({len(buckets['clear'])})",
        ]
    )
    for tab, name in zip(tabs, ("rewrite", "review", "clear")):
        with tab:
            ordered = sorted(buckets[name], key=lambda r: -r["cost"])
            if not ordered:
                st.caption("Nothing in this bucket at the current thresholds.")
            for row in ordered:
                with st.container(border=True):
                    st.markdown(f"**{row['index'] + 1}.** {row['sentence']}")
                    fired = flagged_hazards(row["probabilities"], thresholds)
                    st.caption(
                        f"family: `{row['family']}` (conf {row['family_confidence']:.2f}) · "
                        f"late-discovery cost {row['cost']:.2f} / {len(COST_LEVELS) - 1}"
                    )
                    if row["family"] in FAMILY_TO_HAZARD and FAMILY_TO_HAZARD[row["family"]] not in fired:
                        st.caption(
                            "⚠️ the Choice named a family whose Noul did not clear its threshold — "
                            "trust the Noul, it is the one you tuned."
                        )
                    left, right = st.columns(2)
                    for position, (key, spec) in enumerate(HAZARDS.items()):
                        value = row["probabilities"][key]
                        mark = "🔴" if value >= thresholds[key] else "·"
                        (left if position % 2 == 0 else right).caption(
                            f"{mark} {value:.2f} — {spec['label']}"
                        )

    st.divider()
    footer = st.columns(4)
    footer[0].metric("Sentences", len(sentences))
    footer[1].metric("Judgments", len(questions))
    footer[2].metric("Latency", f"{answers.elapsed_s:.1f} s")
    footer[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{len(sentences)} sentences × {QUESTIONS_PER_SENTENCE} questions = {len(questions)} judgments "
        f"in 1 request, {answers.input_tokens:,} input tokens. Moving any slider above re-buckets "
        f"these results without re-running inference."
    )


if __name__ == "__main__":
    main()
