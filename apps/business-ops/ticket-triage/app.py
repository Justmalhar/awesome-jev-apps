"""Ticket Triage -- route support tickets on four independent axes, one request.

Triage is four questions that happen to arrive together: what kind of issue is
this, which product does it belong to, how fast does it need answering, and is
this person about to leave. They are independent. A single "priority" label
collapses all four into one number and throws away the ability to route on any
of them separately.

So: two Choices (mutually exclusive within themselves), two Scores (degrees),
and two Nouls (independent flags). All of it in ONE ask() per batch of tickets,
because questions run in parallel against a state ingested once.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_tickets.json"

ISSUE_TYPES = {
    "bug": "Something that used to work, or is documented to work, is not working",
    "how_to": "They are asking how to accomplish something the product supports",
    "billing": "Invoices, charges, refunds, plan changes, or payment methods",
    "account_access": "Login, SSO, password, permissions, or being locked out",
    "feature_request": "They want something the product does not currently do",
    "outage_report": "They report the service being broadly unavailable or erroring for everyone",
    "other": "The ticket fits none of the above, or is too vague to place",
}

PRODUCT_AREAS = {
    "api": "The public API, SDKs, webhooks, or API authentication",
    "dashboard": "The web application, its views, reports, and settings screens",
    "mobile": "The iOS or Android application",
    "integrations": "A connection to a third-party system such as Slack, Salesforce, or Jira",
    "billing_system": "Subscription, invoice, or payment machinery",
    "unclear": "The ticket does not indicate a product area",
}

URGENCY_LEVELS = [
    "The customer describes something they are curious about with no work blocked",
    "They have a workaround and are asking for a better one at their convenience",
    "Their own work is blocked but nobody outside their team is affected",
    "Their customers or their revenue are being affected right now",
]

CHURN_LEVELS = [
    "The tone is neutral or warm and the relationship is not in question",
    "They express irritation about this specific issue only",
    "They reference a pattern of problems or a previous unresolved ticket",
    "They mention cancelling, not renewing, or evaluating an alternative vendor",
]

FLAGS = {
    "security": (
        "Does this ticket report a security or privacy problem, such as exposed data, a leaked credential, or someone seeing another account's information?",
        "They describe exposed data, wrong-account access, or a credential or vulnerability concern",
        "No security or privacy exposure is described",
    ),
    "data_loss": (
        "Does this ticket report customer data being lost, deleted, or corrupted?",
        "They describe records, files, or history that have gone missing or become wrong",
        "Nothing is described as lost or corrupted",
    ),
}


def build_questions(tickets: list[dict]) -> dict:
    """Four independent axes plus two flags, per ticket, in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(tickets)):
        reference = {
            "subject": f"`tickets[{index}].subject`",
            "body": f"`tickets[{index}].body`",
            "plan": f"`tickets[{index}].plan`",
        }
        questions[f"issue__{index}"] = choice(
            {"task": "What kind of issue is this ticket?", "ticket": reference}, ISSUE_TYPES
        )
        questions[f"area__{index}"] = choice(
            {"task": "Which part of the product is this ticket about?", "ticket": reference},
            PRODUCT_AREAS,
        )
        questions[f"urgency__{index}"] = score(
            {"task": "How badly is this customer blocked from working right now?", "ticket": reference},
            URGENCY_LEVELS,
        )
        questions[f"churn__{index}"] = score(
            {"task": "How much does this ticket put the relationship at risk?", "ticket": reference},
            CHURN_LEVELS,
        )
        for key, (task, yes, no) in FLAGS.items():
            questions[f"{key}__{index}"] = noul({"task": task, "ticket": reference}, true=yes, false=no)
    return questions


QUEUES = {
    "bug": "engineering",
    "outage_report": "engineering",
    "api": "engineering",
    "billing": "finance",
    "billing_system": "finance",
    "account_access": "trust-and-access",
    "feature_request": "product",
}


def route(judgment: dict, security_gate: float, churn_gate: float, urgency_gate: float) -> dict:
    """Policy in Python: every routing rule is tunable without re-running inference."""
    if judgment["security"] >= security_gate or judgment["data_loss"] >= security_gate:
        queue, reason = "security", "security or data-loss flag fired"
    elif judgment["issue"] in QUEUES:
        queue, reason = QUEUES[judgment["issue"]], f"issue type {judgment['issue']}"
    elif judgment["area"] in QUEUES:
        queue, reason = QUEUES[judgment["area"]], f"product area {judgment['area']}"
    else:
        queue, reason = "tier-1-support", "no specialist rule matched"

    priority = "P3"
    if judgment["urgency"] >= urgency_gate:
        priority = "P1"
    elif judgment["urgency"] >= urgency_gate - 1.0:
        priority = "P2"
    if judgment["churn"] >= churn_gate and priority != "P1":
        priority = "P1"
        reason += "; raised on relationship risk"
    if queue == "security":
        priority = "P1"
    return {"queue": queue, "priority": priority, "reason": reason}


def main() -> None:
    st.set_page_config(page_title="Ticket Triage", page_icon="🎫", layout="wide")
    st.title("🎫 Ticket Triage")
    st.caption("Four independent judgments per ticket, one request, routing rules in code.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Routing thresholds")
        st.caption("Re-routing uses the stored judgments — no API calls.")
        security_gate = st.slider("Security queue at P(yes) ≥", 0.3, 0.95, 0.55, 0.05)
        urgency_gate = st.slider("P1 at urgency ≥", 1.0, 3.0, 2.5, 0.1)
        churn_gate = st.slider("Escalate on relationship risk ≥", 1.0, 3.0, 2.4, 0.1)

    upload = st.file_uploader("Tickets (JSON list of {id, subject, body, plan})", type=["json"])
    tickets = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    if st.button(f"Triage {len(tickets)} tickets", type="primary"):
        questions = build_questions(tickets)
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"tickets": tickets}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["triaged"] = {
            "tickets": tickets,
            "judgments": [
                {
                    "issue": answers.choice(f"issue__{i}"),
                    "issue_conf": answers.confidence(f"issue__{i}"),
                    "area": answers.choice(f"area__{i}"),
                    "urgency": answers.score(f"urgency__{i}"),
                    "churn": answers.score(f"churn__{i}"),
                    "security": answers.noul(f"security__{i}"),
                    "data_loss": answers.noul(f"data_loss__{i}"),
                }
                for i in range(len(tickets))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    triaged = st.session_state.get("triaged")
    if not triaged:
        st.info(f"{len(tickets)} sample tickets bundled. Hit the button.")
        return

    rows = []
    for ticket, judgment in zip(triaged["tickets"], triaged["judgments"]):
        decision = route(judgment, security_gate, churn_gate, urgency_gate)
        rows.append(
            {
                "id": ticket["id"],
                "priority": decision["priority"],
                "queue": decision["queue"],
                "subject": ticket["subject"],
                "plan": ticket.get("plan", ""),
                "issue": judgment["issue"],
                "area": judgment["area"],
                "urgency": round(judgment["urgency"], 2),
                "churn risk": round(judgment["churn"], 2),
                "security P": round(judgment["security"], 2),
                "why": decision["reason"],
            }
        )
    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["priority", "urgency"], ascending=[True, False])

    columns = st.columns(4)
    for column, priority in zip(columns, ["P1", "P2", "P3"]):
        column.metric(priority, int((frame["priority"] == priority).sum()))
    columns[3].metric("Queues used", frame["queue"].nunique())

    st.dataframe(frame, width="stretch", hide_index=True)

    st.subheader("Load per queue")
    st.dataframe(
        frame.groupby(["queue", "priority"]).size().unstack(fill_value=0),
        width="stretch",
    )

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", triaged["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{triaged['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${triaged['cost']:.6f}")
    st.caption(
        f"{triaged['tokens']:,} input tokens for {len(triaged['tickets'])} tickets "
        f"× 6 judgments. Moving the thresholds above re-routed everything for $0.00."
    )


if __name__ == "__main__":
    main()
