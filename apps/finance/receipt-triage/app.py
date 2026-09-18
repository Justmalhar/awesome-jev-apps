"""Receipt Triage -- match receipts to card transactions, flag what is unmatched.

The shape here is pairwise-plus-curator, in ONE request.

  Python   blocks the n x m grid down to plausible pairs using a date window and
           an amount tolerance -- exactly the comparisons jev-1.13 cannot make.
  Jev      answers, per surviving pair, "is this the same purchase?", AND, per
           transaction, "which of these receipts is the one?".
  Python   resolves the two answers into a one-to-one assignment, because a
           receipt can only belong to one charge and that is bookkeeping, not
           judgment.

The pairwise tier is myopic: two identical Pret receipts on the same day for the
same amount both score high against both Pret charges, and nothing about either
pair, viewed alone, breaks the tie. The curator tier sees all the candidates for
one transaction at once and picks. Both tiers are questions over the same state,
so they cost one request between them.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE_TXNS = HERE / "sample_transactions.csv"
SAMPLE_RECEIPTS = HERE / "sample_receipts.json"

NO_MATCH = "none"


def candidate_pairs(
    transactions: list[dict],
    receipts: list[dict],
    window_days: int,
    tolerance: float,
    account_currency: str = "GBP",
) -> list[tuple[int, int]]:
    """Blocking, in Python. Date arithmetic and amount comparison both live here.

    A foreign-currency receipt is kept whatever its total, because the card was
    charged in the account currency after conversion and the two numbers are
    supposed to disagree. That is the one case where the amount filter must not
    be trusted.
    """
    pairs: list[tuple[int, int]] = []
    txn_dates = pd.to_datetime([t["date"] for t in transactions], errors="coerce")
    rcp_dates = pd.to_datetime([r["date"] for r in receipts], errors="coerce")
    for t_index, transaction in enumerate(transactions):
        for r_index, receipt in enumerate(receipts):
            gap = abs((txn_dates[t_index] - rcp_dates[r_index]).days)
            if gap > window_days:
                continue
            foreign = str(receipt.get("currency", account_currency)).upper() != account_currency.upper()
            charged = abs(float(transaction["amount"]))
            claimed = abs(float(receipt["total"]))
            within = abs(charged - claimed) <= max(tolerance * charged, 0.01)
            if foreign or within:
                pairs.append((t_index, r_index))
    return pairs


def pair_facts(transaction: dict, receipt: dict) -> dict:
    """Measurements handed to the model as facts. It never derives them."""
    charged = abs(float(transaction["amount"]))
    claimed = abs(float(receipt["total"]))
    gap = abs((pd.to_datetime(transaction["date"]) - pd.to_datetime(receipt["date"])).days)
    return {
        "amount_charged": round(charged, 2),
        "receipt_total": round(claimed, 2),
        "receipt_currency": receipt.get("currency", "GBP"),
        "amount_difference": round(charged - claimed, 2),
        "calendar_days_apart": int(gap),
    }


def build_questions(pairs: list[dict], curations: list[dict]) -> dict:
    """Tier one: a Noul per candidate pair. Tier two: a Choice per transaction.

    Both tiers see the same state and cannot see each other's answers -- which is
    fine, because Python combines them afterwards.
    """
    questions: dict[str, dict] = {}
    for index in range(len(pairs)):
        questions[f"pair_{index}"] = noul(
            {
                "task": "Do this card charge and this receipt document the same single purchase?",
                "card_charge": f"`pairs[{index}].transaction`",
                "receipt": f"`pairs[{index}].receipt`",
                "measurements": f"`pairs[{index}].facts`",
                "note": "A tip, a service charge, or a currency conversion can make the two figures differ for the same purchase. A different shop on the same day for the same figure is a different purchase.",
            },
            true="The receipt itemises the goods or service that this card charge paid for.",
            false="They are separate purchases that merely look alike, or the receipt belongs to a different merchant, trip, or day.",
        )
    for index in range(len(curations)):
        questions[f"curate_{index}"] = choice(
            {
                "task": "Which one of these receipts is the receipt for this card charge?",
                "card_charge": f"`curations[{index}].transaction`",
                "candidate_receipts": f"`curations[{index}].candidates`",
            },
            {**curations[index]["options"], NO_MATCH: "None of these receipts is for this charge."},
        )
    return questions


def combine(pair_probability: float, curated: bool, curator_confidence: float, curator_weight: float) -> float:
    """Fuse the two tiers. Weight is a slider, so fusion is tunable without inference."""
    bonus = curator_weight * curator_confidence if curated else 0.0
    return min(1.0, pair_probability + bonus)


def assign(scores: dict[tuple[int, int], float], accept_gate: float) -> dict[int, int]:
    """Greedy one-to-one assignment, highest score first.

    A receipt matched to two charges is a double claim, so the constraint is not
    optional -- and it is a constraint, not a judgment, so it lives here.
    """
    taken_transactions: set[int] = set()
    taken_receipts: set[int] = set()
    matched: dict[int, int] = {}
    for (t_index, r_index), value in sorted(scores.items(), key=lambda kv: -kv[1]):
        if value < accept_gate:
            break
        if t_index in taken_transactions or r_index in taken_receipts:
            continue
        matched[t_index] = r_index
        taken_transactions.add(t_index)
        taken_receipts.add(r_index)
    return matched


def triage(best_score: float, accept_gate: float, review_gate: float) -> str:
    if best_score >= accept_gate:
        return "matched"
    if best_score >= review_gate:
        return "review"
    return "unmatched"


def main() -> None:
    st.set_page_config(page_title="Receipt Triage", page_icon="🧾", layout="wide")
    st.title("🧾 Receipt Triage")
    st.caption("Match receipts to card charges. Flag the charges with nothing behind them.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Blocking (free, pure Python)")
        window_days = st.slider("Date window, days either side", 0, 14, 4, 1)
        tolerance = st.slider("Amount tolerance", 0.0, 0.5, 0.25, 0.01,
                              help="Wide enough to survive tips and service charges.")
        st.divider()
        st.subheader("Fusion and gates")
        curator_weight = st.slider("Curator bonus", 0.0, 0.6, 0.25, 0.05)
        accept_gate = st.slider("Auto-match above", 0.0, 1.0, 0.75, 0.05)
        review_gate = st.slider("Send to a human above", 0.0, 1.0, 0.35, 0.05)

    uploaded_txns = st.file_uploader("Card transactions CSV", type=["csv"])
    uploaded_receipts = st.file_uploader("Receipts JSON", type=["json"])

    transactions = pd.read_csv(io.BytesIO(uploaded_txns.read())) if uploaded_txns else pd.read_csv(SAMPLE_TXNS)
    receipts = (
        json.loads(uploaded_receipts.read().decode("utf-8"))
        if uploaded_receipts
        else json.loads(SAMPLE_RECEIPTS.read_text(encoding="utf-8"))
    )

    txn_records = transactions.to_dict("records")
    if uploaded_txns is None and uploaded_receipts is None:
        st.caption(f"Bundled sample: {len(txn_records)} card charges, {len(receipts)} receipts.")

    left, right = st.columns(2)
    left.dataframe(transactions.head(6), width="stretch", hide_index=True)
    right.dataframe(pd.DataFrame(receipts).head(6), width="stretch", hide_index=True)

    pairs = candidate_pairs(txn_records, receipts, window_days, tolerance)
    full_grid = len(txn_records) * len(receipts)
    st.caption(
        f"Blocking kept {len(pairs)} of {full_grid} possible pairs "
        f"({len(pairs) / max(full_grid, 1) * 100:.0f}%) — and cost nothing."
    )
    if not pairs:
        st.info("No candidate pairs survived blocking. Widen the window or the tolerance.")
        return

    if not st.button("Triage", type="primary"):
        return

    pair_state = [
        {
            "transaction": txn_records[t],
            "receipt": receipts[r],
            "facts": pair_facts(txn_records[t], receipts[r]),
        }
        for t, r in pairs
    ]

    by_transaction: dict[int, list[int]] = {}
    for pair_index, (t_index, r_index) in enumerate(pairs):
        by_transaction.setdefault(t_index, []).append(r_index)

    curations = [
        {
            "transaction_index": t_index,
            "transaction": txn_records[t_index],
            "candidates": [receipts[r] for r in candidate_receipts],
            "options": {
                str(receipts[r]["receipt_id"]): f"{receipts[r]['vendor']} — {receipts[r]['items']}"
                for r in candidate_receipts
            },
        }
        for t_index, candidate_receipts in sorted(by_transaction.items())
    ]

    questions = build_questions(pair_state, curations)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"pairs": pair_state, "curations": curations}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    curator_pick: dict[int, tuple[str, float]] = {}
    for index, curation in enumerate(curations):
        curator_pick[curation["transaction_index"]] = (
            answers.choice(f"curate_{index}"),
            answers.confidence(f"curate_{index}"),
        )

    scores: dict[tuple[int, int], float] = {}
    rows: list[dict] = []
    for pair_index, (t_index, r_index) in enumerate(pairs):
        pair_probability = answers.noul(f"pair_{pair_index}")
        picked, picked_confidence = curator_pick.get(t_index, (NO_MATCH, 0.0))
        curated = picked == str(receipts[r_index]["receipt_id"])
        fused = combine(pair_probability, curated, picked_confidence, curator_weight)
        scores[(t_index, r_index)] = fused
        rows.append(
            {
                "txn_id": txn_records[t_index]["txn_id"],
                "merchant": txn_records[t_index]["merchant"],
                "amount": float(txn_records[t_index]["amount"]),
                "receipt_id": receipts[r_index]["receipt_id"],
                "vendor": receipts[r_index]["vendor"],
                "receipt_total": float(receipts[r_index]["total"]),
                "pair_p": pair_probability,
                "curator_picked": curated,
                "score": fused,
            }
        )

    matched = assign(scores, accept_gate)
    pair_frame = pd.DataFrame(rows)

    summary = []
    for t_index, transaction in enumerate(txn_records):
        best = max((v for (t, _), v in scores.items() if t == t_index), default=0.0)
        r_index = matched.get(t_index)
        summary.append(
            {
                "txn_id": transaction["txn_id"],
                "date": transaction["date"],
                "merchant": transaction["merchant"],
                "amount": float(transaction["amount"]),
                "status": triage(best, accept_gate, review_gate),
                "receipt_id": receipts[r_index]["receipt_id"] if r_index is not None else "",
                "best_score": round(best, 3),
            }
        )
    summary_frame = pd.DataFrame(summary)
    summary_frame.loc[summary_frame["receipt_id"] == "", "status"] = summary_frame.loc[
        summary_frame["receipt_id"] == "", "status"
    ].replace({"matched": "review"})

    used = {receipts[r]["receipt_id"] for r in matched.values()}
    orphans = [r for r in receipts if r["receipt_id"] not in used]

    columns = st.columns(4)
    columns[0].metric("Charges", len(summary_frame))
    columns[1].metric("Matched", int((summary_frame["status"] == "matched").sum()))
    columns[2].metric("Unmatched value", f"{summary_frame[summary_frame['status'] == 'unmatched']['amount'].sum():,.2f}")
    columns[3].metric("Orphan receipts", len(orphans))

    st.subheader("Charges")
    st.dataframe(summary_frame.sort_values(["status", "best_score"]), width="stretch", hide_index=True)

    st.subheader("Where the curator earned its keep")
    st.caption(
        "Pairs the pairwise tier rated highly that the curator did NOT pick. Viewed alone, "
        "two identical receipts from two branches of the same chain on the same day are both "
        "excellent matches for both charges. Only a question that sees all the candidates at "
        "once can break that tie."
    )
    contested = pair_frame[(pair_frame["pair_p"] >= 0.5) & (~pair_frame["curator_picked"])]
    st.dataframe(contested.sort_values("pair_p", ascending=False), width="stretch", hide_index=True)

    st.subheader(f"🧾 Receipts with no charge ({len(orphans)})")
    st.caption("Duplicate forwards, personal cards, and receipts for charges that have not landed yet.")
    st.dataframe(pd.DataFrame(orphans), width="stretch", hide_index=True)

    st.download_button(
        "Download match report CSV",
        summary_frame.to_csv(index=False).encode("utf-8"),
        file_name="receipt_matches.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {len(questions)} questions ({len(pair_state)} pairwise + {len(curations)} curator), "
        f"{answers.input_tokens:,} input tokens, ${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s."
    )


if __name__ == "__main__":
    main()
