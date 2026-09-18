"""CI Triage -- real bug, flake, or infra? Across your whole failure history.

Every team with a flaky suite ends up with the same broken instinct: re-run it
and see. That instinct is expensive, and it hides real regressions inside noise
nobody reads any more.

Classifying thousands of historical failures with a frontier model is a budget
conversation. At $0.042/Mtok it is a rounding error, which is the only reason
this app is worth writing.

Note what the model is NOT asked: how many times a test failed, what its flake
RATE is, or whether failures increased week over week. Those are counts, and
`jev-1.13` does not count. Python does all of that from the labels.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_failures.json"
LOG_CHARS = 1500

CLASSES = {
    "real_bug": "A genuine defect in the application code under test. The test is correct and caught something.",
    "flaky_test": "The test itself is unreliable: timing, ordering, shared state, randomness, or an unawaited async operation.",
    "infrastructure": "The environment failed, not the code: runner died, disk full, network unreachable, container pull failed.",
    "dependency": "An external or third-party service, package registry, or upstream API caused the failure.",
    "config": "Misconfiguration: missing environment variable, bad credentials, wrong version pin, malformed workflow file.",
    "unclear": "The log does not contain enough information to tell.",
}


def build_questions(failures: list[dict]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(failures)):
        reference = {
            "test": f"`failures[{index}].test`",
            "message": f"`failures[{index}].message`",
            "log": f"`failures[{index}].log`",
        }
        questions[f"class_{index}"] = choice(
            {"task": "What caused this CI failure?", "failure": reference}, CLASSES
        )
        # Independent of the class: an infra failure and a flaky test are both
        # retry-safe, a real bug is not. Worth its own threshold.
        questions[f"retry_{index}"] = noul(
            {"task": "Would simply re-running this job plausibly make it pass, with no code change?",
             "failure": reference},
            true="Transient cause; a retry is likely to succeed unchanged",
            false="Deterministic; it will fail again until something is fixed",
        )
        questions[f"blast_{index}"] = score(
            {"task": "If this is a real defect, how much would it affect users?",
             "failure": reference},
            [
                "Cosmetic or test-only; no user-visible effect",
                "Degrades a secondary feature or an edge case",
                "Breaks a core user-facing path",
                "Data loss, security exposure, or total outage",
            ],
        )
    return questions


def main() -> None:
    st.set_page_config(page_title="CI Triage", page_icon="🧪", layout="wide")
    st.title("🧪 CI Triage")
    st.caption("Real bug, flake, or infra — across your whole failure history.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        confidence_gate = st.slider("Confidence to trust the label", 0.0, 1.0, 0.55, 0.05)
        retry_gate = st.slider("P(retry fixes it) to call it retry-safe", 0.0, 1.0, 0.6, 0.05)
        batch_size = st.slider("Failures per request", 10, 100, 40, 10)

    uploaded = st.file_uploader("Failures JSON: [{test, message, log}]", type=["json"])
    if uploaded:
        failures = json.loads(uploaded.read().decode("utf-8"))
    elif SAMPLE.is_file():
        failures = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(failures)} failures.")
    else:
        st.warning("Upload a failures JSON.")
        return

    for failure in failures:
        failure["log"] = str(failure.get("log", ""))[:LOG_CHARS]

    if not st.button(f"Triage {len(failures)} failures", type="primary"):
        return

    batches = [failures[i : i + batch_size] for i in range(0, len(failures), batch_size)]
    triaged: list[dict] = []
    total_cost = 0.0
    total_tokens = 0

    try:
        with st.spinner(f"Classifying in {len(batches)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in batches:
                    answers = client.ask({"failures": group}, build_questions(group))
                    total_cost += answers.cost_usd
                    total_tokens += answers.input_tokens
                    for index, failure in enumerate(group):
                        triaged.append(
                            {
                                **failure,
                                "class": answers.choice(f"class_{index}"),
                                "confidence": answers.confidence(f"class_{index}"),
                                "retry_safe": answers.noul(f"retry_{index}"),
                                "blast": answers.score(f"blast_{index}"),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    # ── Every count below comes from Python, never from the model. ──
    counts = Counter(
        item["class"] if item["confidence"] >= confidence_gate else "unclear" for item in triaged
    )
    real_bugs = [i for i in triaged if i["class"] == "real_bug" and i["confidence"] >= confidence_gate]
    retry_safe = [i for i in triaged if i["retry_safe"] >= retry_gate]

    columns = st.columns(4)
    columns[0].metric("Failures", len(triaged))
    columns[1].metric("Real bugs", len(real_bugs))
    columns[2].metric("Retry-safe", f"{len(retry_safe)} ({len(retry_safe)/len(triaged)*100:.0f}%)")
    columns[3].metric("Cost", f"${total_cost:.6f}")

    st.subheader("Failure classes")
    st.bar_chart({key: value for key, value in counts.most_common()})

    st.subheader("🔴 Look at these first")
    st.caption("Classified as real bugs, ordered by how much they would hurt users.")
    priority = sorted(real_bugs, key=lambda i: -i["blast"])
    if not priority:
        st.success("No confident real-bug classifications in this batch.")
    for item in priority:
        with st.container(border=True):
            st.markdown(f"**{item['test']}** · blast radius {item['blast']:.2f}/3")
            st.caption(f"{item['message']}  ·  confidence {item['confidence']:.2f}")
            with st.expander("Log"):
                st.code(item["log"])

    st.subheader("Everything else")
    for label in ("flaky_test", "infrastructure", "dependency", "config", "unclear"):
        items = [i for i in triaged if i["class"] == label and i["confidence"] >= confidence_gate]
        if not items:
            continue
        with st.expander(f"{label} ({len(items)})"):
            for item in items:
                st.caption(
                    f"**{item['test']}** — {item['message'][:120]} "
                    f"(retry-safe P={item['retry_safe']:.2f})"
                )

    low_confidence = [i for i in triaged if i["confidence"] < confidence_gate]
    if low_confidence:
        with st.expander(f"⚠️ Low confidence, needs a human ({len(low_confidence)})"):
            for item in low_confidence:
                st.caption(f"**{item['test']}** — guessed {item['class']} at {item['confidence']:.2f}")

    st.divider()
    st.caption(
        f"{len(triaged)} failures × 3 judgments in {len(batches)} request(s) · "
        f"{total_tokens:,} tokens · ${total_cost:.6f} "
        f"(${total_cost/len(triaged):.8f} per failure)."
    )


if __name__ == "__main__":
    main()
