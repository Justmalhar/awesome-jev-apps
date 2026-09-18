"""Translation QA -- flag the translations that changed MEANING, not wording.

Every segment is two states held side by side: what the source says, and what a
reader of the target would take away. A good translation is routinely a bad
string match -- "pan comido" shares no substring with "a piece of cake" and is
the correct rendering -- so string distance and back-translation similarity both
punish exactly the segments a linguist would approve.

The hazards here are genuinely independent. A segment can omit a clause without
flipping a polarity; it can invert an obligation while omitting nothing. So each
is its own Noul with its own threshold, and a fifth Noul -- "the difference is
stylistic only" -- is what lets idiomatic renderings be suppressed rather than
reported. Severity is a Score, because it is a degree.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_segments.json"
CHARS_PER_TOKEN = 4  # rough, only used to warn before the provider rejects the call

# Hazards, in the order a reviewer would want them reported. Each is separately
# thresholded, which is the whole reason they are separate Nouls.
HAZARDS = ("polarity_flip", "omission", "addition", "meaning_changed")

HAZARD_LABEL = {
    "polarity_flip": "polarity / modality flipped",
    "omission": "source content missing",
    "addition": "unsupported claim added",
    "meaning_changed": "meaning changed",
}

DEFAULT_THRESHOLDS = {
    "polarity_flip": 0.55,
    "omission": 0.65,
    "addition": 0.65,
    "meaning_changed": 0.70,
    "stylistic_only": 0.75,
}

SEVERITY_LEVELS = [
    "A reader of the translation takes away exactly what the source says; only phrasing differs",
    "A nuance is lost or added -- emphasis, register, or a hedge -- while the instruction and the facts survive",
    "A reader would act differently: a duty, permission, deadline, quantity, or named party does not match the source",
]


def load_segments(text: str) -> list[dict]:
    """Parse the pasted JSON into source/target pairs, rejecting junk loudly."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of {id, source, target} objects")
    segments = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or "source" not in item or "target" not in item:
            raise ValueError(f"segment {index} needs both 'source' and 'target'")
        segments.append(
            {
                "id": str(item.get("id", index)),
                "source": str(item["source"]),
                "target": str(item["target"]),
            }
        )
    return segments


def build_questions(segments: list[dict]) -> dict:
    """Five independent Nouls plus one severity Score per segment, one request."""
    questions: dict[str, dict] = {}
    for index in range(len(segments)):
        pair = {
            "source_text": f"`segments[{index}].source`",
            "translated_text": f"`segments[{index}].target`",
        }
        questions[f"meaning_changed_{index}"] = noul(
            {
                "task": "Would a reader of the translated text come away with a different "
                        "understanding than a reader of the source text?",
                "note": "A factual detail that has been altered -- a named party, a quantity, "
                        "a period, a place -- is a different understanding. A different word "
                        "choice that leaves the understanding intact is not.",
                "segment": pair,
            },
            true="The two readers would disagree about what is being stated, required, or promised",
            false="The two readers would describe what was said in the same terms, "
                  "even if the wording differs",
        )
        questions[f"omission_{index}"] = noul(
            {
                "task": "Is there information stated in the source text that is absent from "
                        "the translated text?",
                "note": "An exception, a condition, a qualifier, or a whole clause that the "
                        "translation simply does not carry.",
                "segment": pair,
            },
            true="Something the source asserts cannot be recovered from the translation",
            false="Everything the source asserts is present in the translation in some form",
        )
        questions[f"addition_{index}"] = noul(
            {
                "task": "Does the translated text assert something that the source text does "
                        "not assert?",
                "note": "A guarantee, a deadline, a capability, or a condition that has no "
                        "basis in the source. Natural-sounding connective words are not additions.",
                "segment": pair,
            },
            true="The translation commits to something the source never commits to",
            false="The translation asserts nothing beyond what the source asserts",
        )
        questions[f"polarity_flip_{index}"] = noul(
            {
                "task": "Has a negation, permission, or obligation been inverted between the "
                        "source text and the translated text?",
                "note": "A prohibition rendered as an allowance, an allowance rendered as a "
                        "requirement, a requirement softened to an option, or a negation dropped.",
                "segment": pair,
            },
            true="What the source forbids the translation permits, or what the source requires "
                 "the translation merely allows, or the reverse",
            false="Negation, permission, and obligation carry across unchanged",
        )
        questions[f"stylistic_only_{index}"] = noul(
            {
                "task": "Is the difference between the two texts purely one of style, idiom, "
                        "or register, with the meaning identical?",
                "note": "An idiom rendered by a different idiom with the same sense, a "
                        "restructured sentence, or a politer register all qualify.",
                "segment": pair,
            },
            true="A fluent bilingual reader would call this a good non-literal rendering",
            false="Something beyond style differs, or the translation is simply literal",
        )
        questions[f"severity_{index}"] = score(
            {
                "task": "Rate the consequence for someone acting on the translated text alone.",
                "segment": pair,
            },
            SEVERITY_LEVELS,
        )
    return questions


def decide(
    probabilities: dict[str, float], severity: float, thresholds: dict[str, float], severity_flag: float
) -> tuple[str, str]:
    """Policy in Python: a good idiom suppresses, any hazard over its own line fails.

    The suppression comes first deliberately. `stylistic_only` is the guard that
    keeps "pan comido" out of the defect pile, and it is only trusted while
    `meaning_changed` is still below its own threshold -- so a confident
    "it's just idiom" cannot override a confident "the meaning moved".
    """
    stylistic = probabilities.get("stylistic_only", 0.0)
    if stylistic >= thresholds["stylistic_only"] and probabilities["meaning_changed"] < thresholds["meaning_changed"]:
        return "pass", f"non-literal but equivalent (P={stylistic:.2f})"

    fired = [h for h in HAZARDS if probabilities[h] >= thresholds[h]]
    if fired:
        detail = ", ".join(f"{HAZARD_LABEL[h]} P={probabilities[h]:.2f}" for h in fired)
        return "defect", detail
    if severity >= severity_flag:
        return "review", f"severity {severity:.2f} with no single hazard over its threshold"
    return "pass", "no hazard fired"


VERDICT_ORDER = {"defect": 0, "review": 1, "pass": 2}
VERDICT_ICON = {"defect": "🔴", "review": "🟠", "pass": "🟢"}


def main() -> None:
    st.set_page_config(page_title="Translation QA", page_icon="🌐", layout="wide")
    st.title("🌐 Translation QA")
    st.caption(
        "Two states per segment: what the source says, what the translation says. "
        "Flags the ones where the **meaning** moved, not the wording."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    thresholds = dict(DEFAULT_THRESHOLDS)
    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Hazard thresholds")
        st.caption("Each hazard is judged separately, so each gets its own line.")
        for hazard in HAZARDS:
            thresholds[hazard] = st.slider(
                HAZARD_LABEL[hazard], 0.1, 0.99, DEFAULT_THRESHOLDS[hazard], 0.01
            )
        thresholds["stylistic_only"] = st.slider(
            "P(stylistic only) that suppresses a flag", 0.1, 0.99,
            DEFAULT_THRESHOLDS["stylistic_only"], 0.01,
        )
        severity_flag = st.slider(
            "Severity that escalates an unflagged segment", 0.0, 2.0, 1.2, 0.05
        )

    default_text = SAMPLE.read_text(encoding="utf-8") if SAMPLE.is_file() else "[]"
    uploaded = st.file_uploader("Upload aligned segments (.json)", type=["json"])
    raw = uploaded.read().decode("utf-8", errors="replace") if uploaded else default_text
    raw = st.text_area("Segments — a JSON list of {id, source, target}", value=raw, height=260)

    if not st.button("Review translation", type="primary"):
        st.info("Twelve bundled EN→ES segments are loaded. Press the button.")
        return

    try:
        segments = load_segments(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        st.error(f"Could not read the segments: {exc}")
        return
    if not segments:
        st.warning("No segments to review.")
        return

    estimated_tokens = len(raw) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Review a shorter run of segments, or switch provider."
        )
        return

    questions = build_questions(segments)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"segments": segments}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, segment in enumerate(segments):
        probabilities = {
            name: answers.noul(f"{name}_{index}")
            for name in (*HAZARDS, "stylistic_only")
        }
        severity = answers.score(f"severity_{index}")
        verdict, reason = decide(probabilities, severity, thresholds, severity_flag)
        rows.append((verdict, reason, segment, probabilities, severity))

    rows.sort(key=lambda r: (VERDICT_ORDER[r[0]], -r[4]))

    counts = {v: sum(1 for r in rows if r[0] == v) for v in VERDICT_ORDER}
    columns = st.columns(3)
    for column, verdict in zip(columns, ("defect", "review", "pass")):
        column.metric(f"{VERDICT_ICON[verdict]} {verdict}", counts[verdict])

    table = pd.DataFrame(
        [
            {
                "segment": segment["id"],
                "verdict": verdict,
                "severity": round(severity, 2),
                **{HAZARD_LABEL[h]: round(probabilities[h], 2) for h in HAZARDS},
                "stylistic only": round(probabilities["stylistic_only"], 2),
                "why": reason,
            }
            for verdict, reason, segment, probabilities, severity in rows
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)

    for verdict, reason, segment, probabilities, severity in rows:
        if verdict == "pass":
            continue
        with st.container(border=True):
            st.markdown(f"{VERDICT_ICON[verdict]} **{segment['id']}** · {reason}")
            source, target = st.columns(2)
            source.markdown("*Source*")
            source.write(segment["source"])
            target.markdown("*Translation*")
            target.write(segment["target"])
            st.caption(f"severity {severity:.2f} / {len(SEVERITY_LEVELS) - 1}")

    st.divider()
    st.caption(
        f"{len(segments)} segments × 6 judgments = {len(questions)} questions, "
        f"1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
