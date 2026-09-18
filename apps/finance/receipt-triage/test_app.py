"""Offline checks for receipt-triage. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    transactions = pd.read_csv(HERE / "sample_transactions.csv").to_dict("records")
    receipts = json.loads((HERE / "sample_receipts.json").read_text(encoding="utf-8"))

    # -- blocking is pure Python and must actually block --
    pairs = app.candidate_pairs(transactions, receipts, window_days=4, tolerance=0.25)
    grid = len(transactions) * len(receipts)
    assert 0 < len(pairs) < grid / 2, f"blocking kept {len(pairs)} of {grid}"

    # the EUR receipt has no GBP-comparable total, so it must survive on currency alone
    eur_index = next(i for i, r in enumerate(receipts) if r["currency"] == "EUR")
    paris_index = next(i for i, t in enumerate(transactions) if "ZINC" in t["merchant"])
    assert (paris_index, eur_index) in pairs, "a foreign-currency receipt must not be amount-filtered out"

    # the two identical Pret receipts must both reach the curator for both Pret charges
    pret_txns = [i for i, t in enumerate(transactions) if "PRET" in t["merchant"]]
    pret_rcps = [i for i, r in enumerate(receipts) if "Pret" in r["vendor"]]
    assert len(pret_txns) == 2 and len(pret_rcps) == 2
    for t in pret_txns:
        for r in pret_rcps:
            assert (t, r) in pairs, "the ambiguity the curator exists to resolve was blocked away"

    # a zero-day window must be stricter than a wide one
    assert len(app.candidate_pairs(transactions, receipts, 0, 0.25)) < len(pairs)

    # -- measurements are taken in Python, never asked for --
    facts = app.pair_facts(transactions[1], receipts[1])
    assert facts["amount_difference"] == 16.40, facts
    assert facts["calendar_days_apart"] == 0

    # -- question design: pairwise Nouls + one curator Choice with an escape hatch --
    pair_state = [{"transaction": transactions[t], "receipt": receipts[r],
                   "facts": app.pair_facts(transactions[t], receipts[r])} for t, r in pairs]
    curations = [{
        "transaction_index": 0,
        "transaction": transactions[0],
        "candidates": [receipts[0]],
        "options": {"RCP-A": "Bluebird Coffee"},
    }]
    questions = app.build_questions(pair_state, curations)
    assert len(questions) == len(pair_state) + len(curations)
    assert questions["pair_0"]["type"] == "noul"
    assert questions["curate_0"]["type"] == "choice"
    assert app.NO_MATCH in questions["curate_0"]["criteria"], "a Choice needs a no-match option"

    # -- fusion and assignment --
    assert app.combine(0.6, True, 0.8, 0.25) == 0.8
    assert app.combine(0.6, False, 0.8, 0.25) == 0.6
    assert app.combine(0.95, True, 1.0, 0.25) == 1.0, "fusion must stay in [0,1]"

    # one receipt cannot satisfy two charges, however good both pairs look
    scores = {(0, 0): 0.99, (1, 0): 0.98, (1, 1): 0.40}
    assigned = app.assign(scores, accept_gate=0.75)
    assert assigned == {0: 0}, assigned
    assert app.assign(scores, accept_gate=0.995) == {}

    assert app.triage(0.9, 0.75, 0.35) == "matched"
    assert app.triage(0.75, 0.75, 0.35) == "matched", "gate is inclusive"
    assert app.triage(0.74, 0.75, 0.35) == "review"
    assert app.triage(0.35, 0.75, 0.35) == "review"
    assert app.triage(0.34, 0.75, 0.35) == "unmatched"


if __name__ == "__main__":
    check()
    print("ok")
