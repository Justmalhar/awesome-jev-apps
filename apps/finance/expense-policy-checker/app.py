"""Expense Policy Checker -- which claims breach a written expense policy.

Two things this app is built to demonstrate.

1. VETO SEMANTICS. A claim is not "72% compliant". One breach of one rule
   disqualifies it, however clean the other nine look. So the rules are NOT
   averaged, NOT weighted, and NOT collapsed into a compliance score -- each
   rule is its own Noul with its own gate, and the decision is a max over
   breaches, not a mean. The app prints what an averaged score would have said
   next to the real verdict, because the failure mode is worth seeing once.

2. THE POLICY SPLITS IN TWO. "Meals up to 45.00" is a comparison; Python does
   it exactly and for free. "Alcohol is not reimbursable" requires reading a
   restaurant line and knowing that a bottle of red is alcohol. Rules declare
   which kind they are, and only the judgment ones reach the model.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_CLAIMS = HERE / "sample_claims.csv"
SAMPLE_POLICY = HERE / "sample_policy.json"

JUDGMENT = "judgment"
DETERMINISTIC = {"amount_cap", "requires_approval_above", "requires_receipt_above"}


def split_rules(rules: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rules Python can settle exactly, and rules that need reading comprehension."""
    judged = [r for r in rules if r.get("check") == JUDGMENT]
    computed = [r for r in rules if r.get("check") in DETERMINISTIC]
    return judged, computed


def deterministic_breaches(claim: dict, computed: list[dict]) -> dict[str, str]:
    """Every comparison in this app happens here. The model never sees a cap."""
    hits: dict[str, str] = {}
    amount = float(claim.get("amount", 0.0))
    for rule in computed:
        cap = float(rule["cap"])
        kind = rule["check"]
        if kind == "amount_cap":
            if str(claim.get("category", "")).lower() == rule.get("category") and amount > cap:
                hits[rule["id"]] = f"{amount:.2f} exceeds the {cap:.2f} cap"
        elif kind == "requires_approval_above":
            if amount > cap and not str(claim.get("approval_ref", "") or "").strip():
                hits[rule["id"]] = f"{amount:.2f} is above {cap:.2f} with no pre-approval reference"
        elif kind == "requires_receipt_above":
            if amount > cap and str(claim.get("receipt", "")).strip().lower() not in {"yes", "y", "true", "1"}:
                hits[rule["id"]] = f"{amount:.2f} is above {cap:.2f} with no receipt"
    return hits


def build_questions(claims: list[dict], judged: list[dict]) -> dict:
    """One Noul per (claim, rule). Independent properties, independent gates.

    Rule text is pointed at by reference rather than pasted in, so the policy
    file stays the single source of truth and editing it changes the question.
    """
    questions: dict[str, dict] = {}
    for claim_index in range(len(claims)):
        claim_ref = {
            "vendor": f"`claims[{claim_index}].vendor`",
            "category_claimed": f"`claims[{claim_index}].category`",
            "description": f"`claims[{claim_index}].description`",
            "amount": f"`claims[{claim_index}].amount`",
        }
        for rule_index, rule in enumerate(judged):
            questions[f"b_{claim_index}_{rule_index}"] = noul(
                {
                    "task": "Does this expense claim breach the policy rule quoted below?",
                    "rule": f"`rules[{rule_index}].text`",
                    "claim": claim_ref,
                },
                true=rule["breach"],
                false=rule["clean"],
            )
    return questions


def verdict(breaches: dict[str, float], reject_gate: float, review_gate: float) -> str:
    """VETO, not average. One rule clearing the gate is enough to sink the claim.

    `breaches` maps rule id -> P(breach). A clean claim is one where the worst
    rule is quiet; there is no amount of compliance elsewhere that offsets a
    breach here, which is exactly why this is a max and not a mean.
    """
    if not breaches:
        return "approve"
    worst = max(breaches.values())
    if worst >= reject_gate:
        return "reject"
    if worst >= review_gate:
        return "review"
    return "approve"


def load_claims(uploaded) -> pd.DataFrame | None:
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()))
    return pd.read_csv(SAMPLE_CLAIMS) if SAMPLE_CLAIMS.is_file() else None


def batch(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    st.set_page_config(page_title="Expense Policy Checker", page_icon="📋", layout="wide")
    st.title("📋 Expense Policy Checker")
    st.caption("Which claims breach the written policy. One breach is a breach — nothing is averaged.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    policy = json.loads(SAMPLE_POLICY.read_text(encoding="utf-8"))
    judged, computed = split_rules(policy["rules"])

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Gates")
        reject_gate = st.slider("Reject when a rule breaches above P=", 0.0, 1.0, 0.7, 0.05)
        review_gate = st.slider("Send to a human above P=", 0.0, 1.0, 0.35, 0.05)
        claims_per_request = st.slider("Claims per request", 2, 20, 8, 1)
        st.divider()
        st.caption(f"{len(judged)} judgment rules · {len(computed)} rules settled in Python")

    st.subheader(policy["policy_name"])
    with st.expander("The policy", expanded=False):
        st.dataframe(pd.DataFrame(policy["rules"])[["id", "title", "check", "text"]], width="stretch", hide_index=True)

    uploaded = st.file_uploader("Claims CSV", type=["csv"])
    frame = load_claims(uploaded)
    if frame is None:
        st.warning("Upload a CSV, or keep sample_claims.csv next to this app.")
        return

    required = {"claim_id", "amount", "description", "vendor", "category"}
    missing = required - set(frame.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
        return

    if uploaded is None:
        st.caption(f"Bundled sample: {len(frame)} claims against {len(policy['rules'])} rules.")
    st.dataframe(frame.head(6), width="stretch", hide_index=True)

    question_count = len(frame) * len(judged)
    if not st.button(f"Check {len(frame)} claims ({question_count} judgments)", type="primary"):
        return

    records = frame.to_dict("records")
    groups = batch(records, claims_per_request)
    rule_state = [{"id": r["id"], "text": r["text"]} for r in judged]

    rows: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    progress = st.progress(0.0, text="Checking...")

    try:
        with JevClient(provider=provider) as client:
            for group_index, group in enumerate(groups):
                answers = client.ask(
                    {"policy": policy["policy_name"], "rules": rule_state, "claims": group},
                    build_questions(group, judged),
                )
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                for claim_index, claim in enumerate(group):
                    judged_probs = {
                        rule["id"]: answers.noul(f"b_{claim_index}_{rule_index}")
                        for rule_index, rule in enumerate(judged)
                    }
                    hard = deterministic_breaches(claim, computed)
                    # A Python-detected breach is certain, so it enters at P=1.0.
                    probs = {**judged_probs, **{rule_id: 1.0 for rule_id in hard}}
                    rows.append(
                        {
                            "claim_id": claim["claim_id"],
                            "employee": claim.get("employee", ""),
                            "vendor": claim["vendor"],
                            "amount": float(claim["amount"]),
                            "verdict": verdict(probs, reject_gate, review_gate),
                            "worst_rule": max(probs, key=probs.get),
                            "worst_p": max(probs.values()),
                            "mean_p": sum(probs.values()) / len(probs),
                            "computed_breaches": "; ".join(f"{k}: {v}" for k, v in hard.items()),
                            **{f"p_{rule_id}": p for rule_id, p in judged_probs.items()},
                        }
                    )
                progress.progress((group_index + 1) / len(groups), text="Checking...")
    except JevError as exc:
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(rows)
    rejected = out[out["verdict"] == "reject"]
    review = out[out["verdict"] == "review"]

    columns = st.columns(4)
    columns[0].metric("Claims", len(out))
    columns[1].metric("Rejected", len(rejected))
    columns[2].metric("To review", len(review))
    columns[3].metric("Value rejected", f"{rejected['amount'].sum():,.2f}")

    st.subheader("Verdicts")
    st.dataframe(
        out[["claim_id", "employee", "vendor", "amount", "verdict", "worst_rule", "worst_p", "computed_breaches"]]
        .sort_values("worst_p", ascending=False),
        width="stretch", hide_index=True,
    )

    st.subheader("Why this is a veto and not a score")
    averaged = out.assign(
        averaged_verdict=lambda d: pd.cut(
            d["mean_p"], [-0.01, review_gate, reject_gate, 1.01],
            labels=["approve", "review", "reject"],
        )
    )
    disagreement = averaged[averaged["verdict"] != averaged["averaged_verdict"].astype(str)]
    st.caption(
        "The same probabilities, averaged into one compliance score, instead of vetoed. "
        "Every row below is a claim a weighted average would have waved through because "
        "nine rules were clean and one was not. Compliance does not work that way."
    )
    st.dataframe(
        disagreement[["claim_id", "vendor", "amount", "worst_rule", "worst_p", "mean_p", "verdict", "averaged_verdict"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Breach rate by rule")
    rule_columns = [c for c in out.columns if c.startswith("p_")]
    st.bar_chart((out[rule_columns] >= reject_gate).sum().rename(lambda c: c[2:]))

    st.download_button(
        "Download verdicts CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="policy_verdicts.csv",
        mime="text/csv",
    )
    st.caption(
        f"{len(groups)} request(s), {question_count} judgments, {total_tokens:,} input tokens, "
        f"${total_cost:.6f} total — ${total_cost / max(len(out), 1):.8f} per claim."
    )


if __name__ == "__main__":
    main()
