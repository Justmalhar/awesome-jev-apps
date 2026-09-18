"""flaky-test-finder -- which tests are flaky, and which are just broken?

Both look identical on a dashboard: red. The difference is what you do about
them. A flaky test gets quarantined and fixed on a Tuesday; a consistently
failing test is a regression someone is currently ignoring.

The split cannot be made from statuses alone -- a test that fails on every run
of a broken feature branch has a 100% failure rate and is not flaky, while one
that fails on 8% of runs with the same assertion every time is a real
intermittent bug, not a flake. It needs judgment about WHY the failures differ.

    uv run streamlit run app.py

Every rate, percentage and count on this page is computed by Python from the
run records. `jev-1.13` cannot count, and is never asked to.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_runs.json"
MAX_MESSAGES = 6
MESSAGE_CHARS = 400

MECHANISMS = {
    "timing_race": "The failure depends on how fast something completed: a sleep, a poll, a timeout, an await that was not awaited.",
    "order_dependence": "The test passes alone and fails beside others, or depends on state another test left behind.",
    "external_dependency": "A network service, container, registry or clock outside the test's control decided the outcome.",
    "randomized_input": "Generated or randomly ordered data occasionally produces a case the code does not handle.",
    "resource_pressure": "The runner ran out of memory, file handles, disk, or ports under parallel load.",
    "deterministic_failure": "Nothing intermittent is visible; the failures describe one consistent defect that will recur every run.",
    "unclear": "The recorded messages do not say enough to tell.",
}

FLAKE_MECHANISMS = {"timing_race", "order_dependence", "external_dependency",
                    "randomized_input", "resource_pressure"}


@dataclass
class TestHistory:
    """One test's run history. Every number here is computed in Python."""

    test: str
    runs: int = 0
    failures: int = 0
    messages: list[str] = field(default_factory=list)
    branches: set[str] = field(default_factory=set)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def distinct_messages(self) -> int:
        return len(self.messages)


def summarise(runs: list[dict]) -> list[TestHistory]:
    """Group raw run records per test. Rates, counts and dedup all in Python."""
    grouped: dict[str, TestHistory] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    for record in runs:
        name = str(record.get("test", "")).strip()
        if not name:
            continue
        history = grouped.setdefault(name, TestHistory(test=name))
        history.runs += 1
        if str(record.get("status", "")).lower() not in {"pass", "passed", "ok"}:
            history.failures += 1
            message = str(record.get("message", "")).strip()[:MESSAGE_CHARS]
            if message and message not in seen[name] and len(history.messages) < MAX_MESSAGES:
                seen[name].add(message)
                history.messages.append(message)
        branch = str(record.get("branch", "")).strip()
        if branch:
            history.branches.add(branch)
    return sorted((h for h in grouped.values() if h.failures), key=lambda h: -h.failure_rate)


def build_questions(histories: list[TestHistory]) -> dict:
    """Three judgments per test, all independent, all in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(histories)):
        reference = {
            "test": f"`tests[{index}].test`",
            "failure_messages": f"`tests[{index}].messages`",
        }
        questions[f"mechanism_{index}"] = choice(
            {"task": "What produced these failures?", "test": reference}, MECHANISMS
        )
        # Independent of the mechanism: a test can have one consistent message
        # and still be flaky (a fixed 5s timeout), or vary wildly and be a real
        # bug surfacing in several places. Separate noul, separate threshold.
        questions[f"varied_{index}"] = noul(
            {"task": "Do these failure messages describe different things going wrong rather than one repeated defect?",
             "test": reference},
            true="The recorded messages point at unrelated failure points or conditions",
            false="Every message describes the same assertion or the same error at the same place",
        )
        questions[f"self_inflicted_{index}"] = noul(
            {"task": "Would fixing this require changing the test rather than the code under test?",
             "test": reference},
            true="The test's own setup, teardown, waiting, or assumptions are what is wrong",
            false="The application code is what is wrong, or the messages do not distinguish the two",
        )
    return questions


def classify(history: TestHistory, mechanism: str, self_inflicted: float,
             *, quarantine_floor: float, broken_ceiling: float, test_fault_gate: float) -> str:
    """Pure policy over Python-computed rates and model-judged causes.

    Change these thresholds and re-run nothing: the judgments are already
    stored floats.
    """
    rate = history.failure_rate
    if rate > broken_ceiling:
        return "broken"
    if rate < quarantine_floor:
        return "watch"
    if mechanism == "unclear":
        return "needs_human"
    if mechanism == "deterministic_failure":
        # Intermittent rate, but one repeated defect -- someone's branch is red,
        # not the suite. Fix the code, do not quarantine.
        return "broken"
    if mechanism in FLAKE_MECHANISMS:
        return "quarantine" if self_inflicted >= test_fault_gate else "intermittent_bug"
    return "watch"


def main() -> None:
    st.set_page_config(page_title="Flaky Test Finder", page_icon="🎲", layout="wide")
    st.title("🎲 Flaky Test Finder")
    st.caption("Genuinely flaky, or consistently broken? Rates from Python, causes from Jev.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Policy (Python, no inference)")
        quarantine_floor = st.slider("Failure rate that counts as intermittent", 0.0, 0.5, 0.05, 0.01)
        broken_ceiling = st.slider("Failure rate above which it is just broken", 0.5, 1.0, 0.85, 0.05)
        test_fault_gate = st.slider("P(the test is at fault) to quarantine", 0.0, 1.0, 0.6, 0.05)
        batch_size = st.slider("Tests per request", 5, 60, 25, 5)

    uploaded = st.file_uploader("Run records JSON: [{test, status, message, branch}]", type=["json"])
    if uploaded:
        runs = json.loads(uploaded.read().decode("utf-8"))
    else:
        runs = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(runs)} run records.")

    histories = summarise(runs)
    if not histories:
        st.success("No failing tests in this history.")
        return

    st.metric("Tests with at least one failure", len(histories))
    if not st.button(f"Judge {len(histories)} failing tests", type="primary"):
        return

    groups = [histories[i:i + batch_size] for i in range(0, len(histories), batch_size)]
    judged: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        with st.spinner(f"Judging in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    state = {"tests": [{"test": h.test, "messages": h.messages} for h in group]}
                    questions = build_questions(group)
                    answers = client.ask(state, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, history in enumerate(group):
                        mechanism = answers.choice(f"mechanism_{index}")
                        self_inflicted = answers.noul(f"self_inflicted_{index}")
                        judged.append(
                            {
                                "test": history.test,
                                "runs": history.runs,
                                "failures": history.failures,
                                "failure_rate": round(history.failure_rate, 4),
                                "distinct_messages": history.distinct_messages,
                                "mechanism": mechanism,
                                "confidence": round(answers.confidence(f"mechanism_{index}"), 3),
                                "varied_failures": round(answers.noul(f"varied_{index}"), 3),
                                "test_at_fault": round(self_inflicted, 3),
                                "verdict": classify(
                                    history, mechanism, self_inflicted,
                                    quarantine_floor=quarantine_floor,
                                    broken_ceiling=broken_ceiling,
                                    test_fault_gate=test_fault_gate,
                                ),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in judged:
        buckets[item["verdict"]].append(item)

    columns = st.columns(4)
    columns[0].metric("Quarantine", len(buckets["quarantine"]))
    columns[1].metric("Broken, fix now", len(buckets["broken"]))
    columns[2].metric("Intermittent real bug", len(buckets["intermittent_bug"]))
    columns[3].metric("Needs a human", len(buckets["needs_human"]) + len(buckets["watch"]))

    labels = [
        ("quarantine", "🟡 Quarantine — the test is at fault", "Skip these in CI and fix them on a Tuesday."),
        ("broken", "🔴 Broken — not flaky, just failing", "Fails nearly every run with one consistent cause."),
        ("intermittent_bug", "🟠 Intermittent, but the code is at fault",
         "The rarest and most valuable bucket: a real race in the product, not the test."),
        ("watch", "⚪ Watch", "Below the intermittent floor, or a cause that is not a flake mechanism."),
        ("needs_human", "⚠️ Needs a human", "Not enough in the recorded messages to tell."),
    ]
    for key, title, blurb in labels:
        items = buckets.get(key)
        if not items:
            continue
        st.subheader(title)
        st.caption(blurb)
        st.dataframe(
            sorted(items, key=lambda i: -i["failure_rate"]),
            width="stretch",
            column_order=["test", "failure_rate", "failures", "runs", "mechanism",
                          "confidence", "test_at_fault", "varied_failures"],
        )

    st.divider()
    st.caption(
        f"{asked} question(s) over {len(judged)} test(s) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s measured. "
        "Every rate above was computed in Python from the run records."
    )


if __name__ == "__main__":
    main()
