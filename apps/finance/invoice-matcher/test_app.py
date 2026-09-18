"""Offline checks for invoice-matcher. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    sets = json.loads((HERE / "sample_documents.json").read_text(encoding="utf-8"))
    assert len(sets) >= 6
    by_po = {s["po_number"]: s for s in sets}

    # -- every comparison is Python's, and must be exact --
    clean = app.reconcile(by_po["PO-7741"])
    assert clean["value_difference"] == 0.0 and clean["sku_codes_agree"]
    assert clean["qty_invoiced_minus_delivered"] == 0

    partial = app.reconcile(by_po["PO-7748"])
    assert partial["qty_delivered_minus_ordered"] == -100
    assert partial["qty_invoiced_minus_delivered"] == 0, "invoiced for what arrived"

    price = app.reconcile(by_po["PO-7752"])
    assert price["value_difference"] == 270.0, price["value_difference"]
    assert round(price["value_difference_pct"], 1) == 16.5

    substitute = app.reconcile(by_po["PO-7760"])
    assert not substitute["sku_codes_agree"], "a substituted part must show as a code mismatch"
    assert substitute["value_difference"] == 0.0, "the substitution was at the agreed price"

    over = app.reconcile(by_po["PO-7764"])
    assert over["qty_delivered_minus_ordered"] == 12

    damaged = app.reconcile(by_po["PO-7783"])
    assert damaged["qty_invoiced_minus_delivered"] == 6, "billed for more than was received"

    assert app.line_total([{"qty": 3, "unit_price": 2.5}, {"qty": 1, "unit_price": 0.5}]) == 8.0

    # -- question design --
    enriched = [{**s, "reconciliation": app.reconcile(s)} for s in sets]
    questions = app.build_questions(enriched)
    assert len(questions) == 4 * len(sets)
    assert app.NO_EXPLANATION in questions["reason_0"]["criteria"], "a Choice needs a no-match option"
    assert all(len(text) > 15 for text in app.REASONS.values())
    assert [questions[f"{p}_0"]["type"] for p in ("same_goods", "delivery_belongs", "explained")] == ["noul"] * 3
    assert questions["reason_0"]["type"] == "choice"

    # -- payment policy, at its boundaries --
    gates = dict(goods_gate=0.6, delivery_gate=0.6, explained_gate=0.6, value_tolerance=0.02)
    assert app.decide(clean, 0.95, 0.95, 0.05, **gates) == "pay"
    assert app.decide(clean, 0.95, 0.10, 0.90, **gates) == "escalate_no_valid_receipt"
    assert app.decide(clean, 0.10, 0.95, 0.90, **gates) == "escalate_wrong_goods", "wrong goods beats a good receipt"
    assert app.decide(damaged, 0.95, 0.20, 0.90, **gates) == "escalate_no_valid_receipt"
    assert app.decide(damaged, 0.95, 0.95, 0.90, **gates) == "hold_invoiced_above_receipt", (
        "billing above what was received is never paid, however well explained"
    )
    assert app.decide(price, 0.95, 0.95, 0.05, **gates) == "hold_unexplained_price_variance"
    assert app.decide(price, 0.95, 0.95, 0.90, **gates) == "pay_with_documented_variance"
    assert app.decide(over, 0.95, 0.95, 0.05, **gates) == "hold_unexplained_over_delivery"
    assert app.decide(substitute, 0.90, 0.90, 0.90, **gates) == "pay", "an approved substitute at price is payable"
    # the gates are inclusive, and each moves on its own
    assert app.decide(clean, 0.6, 0.6, 0.0, **gates) == "pay"
    assert app.decide(clean, 0.59, 0.6, 0.0, **gates) == "escalate_wrong_goods"


if __name__ == "__main__":
    check()
    print("ok")
