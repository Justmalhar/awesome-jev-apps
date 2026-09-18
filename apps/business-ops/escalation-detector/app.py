"""Escalation Detector -- which threads need a manager before they blow up.

The interesting decision here is the one the app refuses to make. Anything that
is not clearly escalate-or-leave-it lands in an explicit "look at this" band and
is counted, so the operator knows exactly how much judgment they are still doing.

An escalation detector that always answers is worse than useless: it is wrong
loudly at 3am. The abstention band is the product.

Two abstention mechanisms, both policy-in-Python:

  1. A band between the escalate and ignore thresholds, on the composite.
  2. Hard triggers (legal threat, explicit manager request, public threat) that
     bypass the score entirely -- a veto, not a weighted contribution.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_threads.json"

# Hard triggers: independent properties, each with its own threshold. Any one of
# them escalates on its own, so they can never be levels on a shared rubric.
TRIGGERS = {
    "manager_requested": (
        "Does the customer explicitly ask to speak to a manager, a supervisor, or someone more senior?",
        "They ask for a manager, an escalation, an account executive, or 'someone who can actually decide'",
        "No request for a more senior person is made",
    ),
    "legal_threat": (
        "Does the customer reference lawyers, regulators, contractual breach, or formal complaint procedures?",
        "They mention legal counsel, a regulator, a breach of contract, service credits, or a formal complaint",
        "No legal, regulatory, or contractual process is referenced",
    ),
    "public_threat": (
        "Does the customer threaten to make this public?",
        "They mention posting publicly, contacting press, a review site, or telling their network",
        "No intention to go public is expressed",
    ),
    "churn_threat": (
        "Does the customer state they are considering leaving?",
        "They mention cancelling, not renewing, or moving to another provider",
        "No intention to leave is expressed",
    ),
    "unanswered": (
        "Does the thread show the customer chasing a reply they say they never received?",
        "They say they are following up, have had no response, or are repeating themselves",
        "The customer does not describe waiting on an unanswered message",
    ),
}

# Graded evidence: the things that are degrees, combined into a composite in code.
DIMENSIONS = {
    "harm": {
        "label": "Damage being done right now",
        "weight": 1.0,
        "levels": [
            "Nothing of the customer's is broken; this is a question or a preference",
            "Something is inconvenient but the customer has a working way around it",
            "The customer's own work is blocked and no workaround exists",
            "The customer's end users, revenue, or regulatory position are being harmed",
        ],
    },
    "temperature": {
        "label": "How hot the thread is",
        "weight": 0.9,
        "levels": [
            "Cordial and patient throughout",
            "Businesslike, with mild impatience",
            "Openly frustrated, using emphatic or exasperated language",
            "Hostile, or stating that their patience has run out entirely",
        ],
    },
    "stuck": {
        "label": "How stuck the conversation is",
        "weight": 0.8,
        "levels": [
            "The exchange is progressing and the next step is clear to both sides",
            "There is some repetition but the thread is still moving",
            "The same point is being restated without either side moving",
            "The customer says the process itself has failed them, not just the answer",
        ],
    },
    "authority_gap": {
        "label": "Beyond the front line's authority",
        "weight": 0.7,
        "levels": [
            "An ordinary agent can fully resolve what is being asked",
            "An agent can resolve it after checking with someone",
            "What is being asked needs a refund, credit, exception, or commitment an agent cannot give",
            "What is being asked needs a change of contract, policy, or roadmap",
        ],
    },
}


def build_questions(threads: list[dict]) -> dict:
    """Five triggers and four graded dimensions per thread, in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(threads)):
        reference = {
            "account": f"`threads[{index}].account`",
            "messages": f"`threads[{index}].messages`",
        }
        for key, (task, yes, no) in TRIGGERS.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "thread": reference}, true=yes, false=no
            )
        for key, spec in DIMENSIONS.items():
            questions[f"{key}__{index}"] = score(
                {"task": spec["label"], "thread": reference}, spec["levels"]
            )
    return questions


def composite(judgment: dict, weights: dict[str, float]) -> float:
    weight_sum = sum(weights.values()) or 1.0
    raw = sum(
        weights[key] * (judgment[key] / (len(DIMENSIONS[key]["levels"]) - 1))
        for key in DIMENSIONS
    )
    return 100.0 * raw / weight_sum


def decide(
    judgment: dict,
    weights: dict[str, float],
    trigger_gate: float,
    escalate_at: float,
    ignore_below: float,
) -> tuple[str, float, str]:
    """Three outcomes. The middle one is the point of the app.

    A hard trigger escalates on its own -- it is a veto, and vetoes must not be
    averaged away by four calm dimensions.
    """
    total = composite(judgment, weights)
    fired = [key for key in TRIGGERS if judgment[key] >= trigger_gate]
    if fired:
        return "escalate", total, f"hard trigger: {', '.join(fired)}"
    if total >= escalate_at:
        return "escalate", total, f"composite {total:.0f} at or above the escalate line"
    if total <= ignore_below:
        return "leave_to_agent", total, f"composite {total:.0f} at or below the ignore line"
    return "look_at_it", total, f"composite {total:.0f} sits in the abstention band"


def calibration_table(rows: list[dict]) -> pd.DataFrame:
    """Bucket composites so an operator can see where the abstention band lands."""
    frame = pd.DataFrame(rows)
    frame["bucket"] = pd.cut(
        frame["composite"], bins=[0, 20, 40, 60, 80, 100], right=True, include_lowest=True
    )
    table = frame.groupby("bucket", observed=False).agg(
        threads=("composite", "size"),
        escalated=("verdict", lambda s: int((s == "escalate").sum())),
        abstained=("verdict", lambda s: int((s == "look_at_it").sum())),
    )
    return table


def main() -> None:
    st.set_page_config(page_title="Escalation Detector", page_icon="🚨", layout="wide")
    st.title("🚨 Escalation Detector")
    st.caption("Which threads need a manager — and which ones it refuses to call.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Abstention band")
        escalate_at = st.slider("Escalate at composite ≥", 10, 100, 60, 1)
        ignore_below = st.slider("Leave to the agent at composite ≤", 0, 90, 30, 1)
        if ignore_below >= escalate_at:
            st.warning("No abstention band: every thread gets a forced answer.")
        trigger_gate = st.slider("Hard trigger fires at P(yes) ≥", 0.3, 0.95, 0.60, 0.05)
        st.divider()
        st.subheader("Weights")
        st.caption("Re-scoring is free — no API calls.")
        weights = {
            key: st.slider(spec["label"], 0.0, 2.0, spec["weight"], 0.1)
            for key, spec in DIMENSIONS.items()
        }

    upload = st.file_uploader("Threads (JSON list of {id, account, messages})", type=["json"])
    threads = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Scan {len(threads)} threads", type="primary"):
        questions = build_questions(threads)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"threads": threads}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["scanned"] = {
            "threads": threads,
            "judgments": [
                {
                    **{key: answers.noul(f"{key}__{i}") for key in TRIGGERS},
                    **{key: answers.score(f"{key}__{i}") for key in DIMENSIONS},
                }
                for i in range(len(threads))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    scanned = st.session_state.get("scanned")
    if not scanned:
        st.info(f"{len(threads)} sample threads bundled. Hit the button.")
        return

    rows = []
    for thread, judgment in zip(scanned["threads"], scanned["judgments"]):
        verdict, total, reason = decide(
            judgment, weights, trigger_gate, escalate_at, ignore_below
        )
        rows.append(
            {
                "id": thread["id"],
                "account": thread["account"],
                "verdict": verdict,
                "composite": round(total, 1),
                "why": reason,
                **{key: round(judgment[key], 2) for key in DIMENSIONS},
                **{key: round(judgment[key], 2) for key in TRIGGERS},
            }
        )
    frame = pd.DataFrame(rows).sort_values("composite", ascending=False)

    columns = st.columns(4)
    columns[0].metric("Escalate now", int((frame["verdict"] == "escalate").sum()))
    columns[1].metric("Look at it", int((frame["verdict"] == "look_at_it").sum()))
    columns[2].metric("Leave to agent", int((frame["verdict"] == "leave_to_agent").sum()))
    decided = int((frame["verdict"] != "look_at_it").sum())
    columns[3].metric("Auto-decided", f"{decided / len(frame) * 100:.0f}%")

    for verdict, title in (
        ("escalate", "🚨 Escalate now"),
        ("look_at_it", "🟡 Abstained — a human decides"),
        ("leave_to_agent", "🟢 Leave to the agent"),
    ):
        subset = frame[frame["verdict"] == verdict]
        st.subheader(f"{title} ({len(subset)})")
        if subset.empty:
            st.caption("nothing here")
        else:
            st.dataframe(subset.drop(columns=["verdict"]), width="stretch", hide_index=True)

    st.subheader("Where the band sits")
    st.dataframe(calibration_table(rows), width="stretch")

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", scanned["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{scanned['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${scanned['cost']:.6f}")
    st.caption(
        f"{scanned['tokens']:,} input tokens for {len(frame)} threads × "
        f"{len(TRIGGERS) + len(DIMENSIONS)} judgments. Every threshold move above "
        "re-decided the queue for $0.00."
    )


if __name__ == "__main__":
    main()
