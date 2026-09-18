"""Lead Qualifier -- score inbound leads against an ICP you write as a paragraph.

The point of this app is the sidebar. Every slider re-ranks the whole pipeline
instantly and makes ZERO API calls, because a weight change does not change the
evidence or the meaning of any question. Jev returns raw, reusable judgments;
the scoring policy lives in Python where tuning is free.

Sales teams re-tune lead scoring constantly -- after every pipeline review, every
segment change, every quarter. If the weights live in a prompt, every tune is a
full re-scoring run. Here it is a mouse drag.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_leads.json"

DEFAULT_ICP = (
    "We sell observability tooling to engineering teams at Series B to Series D "
    "SaaS companies, roughly 50 to 500 employees, who already run Kubernetes in "
    "production and have at least one on-call rotation. Our buyer is a platform "
    "or SRE lead who is drowning in alert noise and is paying too much for their "
    "current vendor. We are not a fit for agencies, single-developer projects, "
    "students, or companies that have not shipped to production yet."
)

# Each dimension is one reusable judgment. Weights are policy and live in code.
DIMENSIONS = {
    "icp_fit": {
        "label": "Company matches the ICP",
        "weight": 1.0,
        "levels": [
            "The company described falls outside every attribute the ICP paragraph names",
            "Shares the ICP's broad market but differs on size, stage, or stack",
            "Matches most attributes the ICP names, with one clear mismatch",
            "Matches the industry, size, stack and situation the ICP paragraph spells out",
        ],
    },
    "pain_match": {
        "label": "States the pain the ICP describes",
        "weight": 1.0,
        "levels": [
            "The message states no problem the product could address",
            "Mentions a general goal rather than a concrete problem",
            "Names a concrete problem that the ICP paragraph also names",
            "Describes the exact painful workflow the ICP paragraph describes, in their own words",
        ],
    },
    "authority": {
        "label": "Can actually buy",
        "weight": 0.7,
        "levels": [
            "The stated role has no described involvement in choosing tools",
            "The role evaluates or recommends tools but someone else signs the contract",
            "The person states they own the budget or the final decision",
        ],
    },
    "urgency": {
        "label": "Something is forcing a decision",
        "weight": 0.8,
        "levels": [
            "No timing, project, or triggering event is mentioned at all",
            "Describes an ongoing annoyance with no stated trigger",
            "States an active evaluation or a migration project already underway",
            "States a hard deadline, an expiring contract, or a decision being made right now",
        ],
    },
}

# Independent properties, so independent Nouls -- each is separately thresholdable.
DISQUALIFIERS = {
    "competitor": (
        "Is the sender describing themselves as working at a company that sells a competing product?",
        "They work at a vendor in the same category, or say they are doing competitive research",
        "They are a prospective user, or their employer is not in this category",
    ),
    "job_or_study": (
        "Is this message about a job, an internship, or a course assignment rather than about buying the product?",
        "They are asking about roles, careers, or writing about the product for study",
        "They are asking about using or purchasing the product",
    ),
    "agency_pitch": (
        "Is the sender pitching their own services rather than asking about the product?",
        "They offer development, marketing, SEO, staffing, or consulting services to us",
        "They are asking about our product for their own use",
    ),
}

ASK_OPTIONS = {
    "pricing": "They want prices, quotes, plan comparison, or discount information",
    "demo": "They want a call, a demo, or a walkthrough with a person",
    "technical": "They ask a specific technical question about how the product works",
    "trial_help": "They are already trying the product and need help getting it working",
    "partnership": "They propose reselling, integrating, or co-marketing",
    "unclear": "The message does not make a request that fits any of the above",
}


def build_questions(leads: list[dict], icp: str) -> dict:
    """One request: every lead x every dimension x every disqualifier."""
    questions: dict[str, dict] = {}
    for index in range(len(leads)):
        reference = {
            "company": f"`leads[{index}].company`",
            "role": f"`leads[{index}].role`",
            "message": f"`leads[{index}].message`",
        }
        for key, spec in DIMENSIONS.items():
            questions[f"{key}__{index}"] = score(
                {"task": spec["label"], "ideal_customer_profile": icp, "lead": reference},
                spec["levels"],
            )
        for key, (task, yes, no) in DISQUALIFIERS.items():
            questions[f"{key}__{index}"] = noul(
                {"task": task, "lead": reference}, true=yes, false=no
            )
        questions[f"ask__{index}"] = choice(
            {"task": "What is this sender asking us for?", "lead": reference}, ASK_OPTIONS
        )
    return questions


def composite(judgment: dict, weights: dict[str, float]) -> float:
    """Normalise each rubric to 0..1 so the weights mean what they look like."""
    weight_sum = sum(weights.values()) or 1.0
    raw = sum(
        weights[key] * (judgment[key] / (len(DIMENSIONS[key]["levels"]) - 1))
        for key in DIMENSIONS
    )
    return 100.0 * raw / weight_sum


def decide(total: float, judgment: dict, veto: float, hot: float, warm: float) -> tuple[str, str]:
    """Policy, in Python. Disqualifiers veto -- they never average away."""
    for key in DISQUALIFIERS:
        if judgment[key] >= veto:
            return "disqualified", f"{key} P={judgment[key]:.2f}"
    if total >= hot:
        return "hot", f"composite {total:.0f}"
    if total >= warm:
        return "warm", f"composite {total:.0f}"
    return "nurture", f"composite {total:.0f}"


def load_leads(upload) -> list[dict]:
    if upload is not None:
        return json.loads(upload.read().decode("utf-8"))
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def main() -> None:
    st.set_page_config(page_title="Lead Qualifier", page_icon="🎯", layout="wide")
    st.title("🎯 Lead Qualifier")
    st.caption("Describe your ICP in a paragraph. Score once. Re-weight for free.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Weights")
        st.caption("Drag freely — re-ranking makes **no** API calls.")
        weights = {
            key: st.slider(spec["label"], 0.0, 2.0, spec["weight"], 0.1)
            for key, spec in DIMENSIONS.items()
        }
        st.divider()
        st.subheader("Thresholds")
        veto = st.slider("Disqualify at P(yes) ≥", 0.5, 0.99, 0.70, 0.01)
        hot = st.slider("Hot at composite ≥", 50, 100, 75, 1)
        warm = st.slider("Warm at composite ≥", 10, 90, 50, 1)

    icp = st.text_area("Your ICP, in plain prose", value=DEFAULT_ICP, height=140)
    upload = st.file_uploader("Leads (JSON list of {name, company, role, message})", type=["json"])

    if st.button("Score inbound leads", type="primary"):
        leads = load_leads(upload)
        questions = build_questions(leads, icp.strip())
        try:
            with st.spinner(f"{len(questions)} judgments in one request..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"leads": leads, "ideal_customer_profile": icp}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["scored"] = {
            "leads": leads,
            "judgments": [
                {
                    **{k: answers.score(f"{k}__{i}") for k in DIMENSIONS},
                    **{k: answers.noul(f"{k}__{i}") for k in DISQUALIFIERS},
                    "ask": answers.choice(f"ask__{i}"),
                }
                for i in range(len(leads))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    scored = st.session_state.get("scored")
    if not scored:
        st.info(f"Bundled sample: {len(json.loads(SAMPLE.read_text(encoding='utf-8')))} inbound leads. Hit the button.")
        return

    # --- pure policy below this line: no inference happens here ---
    rows = []
    for lead, judgment in zip(scored["leads"], scored["judgments"]):
        total = composite(judgment, weights)
        tier, reason = decide(total, judgment, veto, hot, warm)
        rows.append(
            {
                "tier": tier,
                "score": round(total, 1),
                "name": lead["name"],
                "company": lead["company"],
                "role": lead["role"],
                "wants": judgment["ask"],
                "why": reason,
                **{k: round(judgment[k], 2) for k in DIMENSIONS},
                **{k: round(judgment[k], 2) for k in DISQUALIFIERS},
            }
        )
    frame = pd.DataFrame(rows).sort_values("score", ascending=False)

    order = ["hot", "warm", "nurture", "disqualified"]
    columns = st.columns(4)
    for column, tier in zip(columns, order):
        column.metric(tier.title(), int((frame["tier"] == tier).sum()))

    for tier in order:
        subset = frame[frame["tier"] == tier]
        if subset.empty:
            continue
        st.subheader(f"{tier.title()} ({len(subset)})")
        st.dataframe(subset.drop(columns=["tier"]), width="stretch", hide_index=True)

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", scored["questions"])
    metrics[1].metric("Requests", 1)
    metrics[2].metric("Latency", f"{scored['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${scored['cost']:.6f}")
    st.caption(
        f"{scored['tokens']:,} input tokens for {len(scored['leads'])} leads. "
        "Every slider move above re-scored the pipeline for $0.00."
    )


if __name__ == "__main__":
    main()
