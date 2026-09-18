"""Invoice Matcher -- three-way match of purchase order, invoice and delivery note.

Accounts payable's oldest control: pay only what you ordered, at the price you
agreed, for goods you actually received. Three documents, three ways to disagree.

The split:

  pandas   every quantity, price and value comparison, exactly, for free. Whether
           the invoice is over the PO by 6.75 a sheet is subtraction, and
           jev-1.13 cannot subtract.
  Jev      whether the documents are talking about the SAME GOODS, whether this
           delivery note belongs to this order, and what the paperwork claims the
           reason for the difference is.

That second column is the whole reason this is not a SQL join. `AF-M8-100` and
`AF-M8-100` join fine. `NE-LED-40 / Osram` versus `NE-LED-40P / Philips CoreLine`
is an approved substitution, and `AF-M8-100 hex bolts` on a delivery note against
`AF-WSH-08 washers` on the invoice is a delivery note stapled to the wrong order.
No join distinguishes those; both are code mismatches.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_documents.json"

NO_EXPLANATION = "no_explanation_in_the_paperwork"

REASONS = {
    "backorder_remainder_to_follow": "Part of the order shipped and the paperwork says the rest is coming.",
    "agreed_substitute_item": "A different product was supplied in place of the ordered one, and the paperwork says this was agreed or approved.",
    "damaged_or_rejected_on_receipt": "Goods arrived damaged, short, or were refused at goods-in.",
    "order_amended_after_issue": "The requirement changed after the order went out and the paperwork refers to that change.",
    "unit_of_measure_differs": "The documents describe the same goods but in different units, such as cases against individual items.",
    "duplicate_submission": "The paperwork indicates this invoice has already been submitted or settled once.",
    NO_EXPLANATION: "The documents give no reason at all for any difference between them.",
}


def line_total(lines: list[dict]) -> float:
    return round(sum(float(l.get("qty", 0)) * float(l.get("unit_price", 0)) for l in lines), 2)


def reconcile(document_set: dict) -> dict:
    """Every comparison in this app happens here, in Python, exactly.

    Nothing below is ever asked of the model -- it is handed the results as
    facts so it can reason about WHY they differ, not WHETHER.
    """
    po = document_set["purchase_order"]
    invoice = document_set["invoice"]
    delivery = document_set["delivery_note"]

    ordered_qty = sum(float(l.get("qty", 0)) for l in po["lines"])
    invoiced_qty = sum(float(l.get("qty", 0)) for l in invoice["lines"])
    delivered_qty = sum(float(l.get("qty", 0)) for l in delivery["lines"])
    ordered_value = line_total(po["lines"])
    invoiced_value = line_total(invoice["lines"])

    po_skus = {str(l.get("sku")) for l in po["lines"]}
    invoice_skus = {str(l.get("sku")) for l in invoice["lines"]}
    delivery_skus = {str(l.get("sku")) for l in delivery["lines"]}

    return {
        "ordered_qty": ordered_qty,
        "invoiced_qty": invoiced_qty,
        "delivered_qty": delivered_qty,
        "qty_invoiced_minus_delivered": round(invoiced_qty - delivered_qty, 3),
        "qty_delivered_minus_ordered": round(delivered_qty - ordered_qty, 3),
        "ordered_value": ordered_value,
        "invoiced_value": invoiced_value,
        "value_difference": round(invoiced_value - ordered_value, 2),
        "value_difference_pct": round((invoiced_value - ordered_value) / ordered_value * 100, 2) if ordered_value else 0.0,
        "sku_codes_agree": po_skus == invoice_skus == delivery_skus,
        "invoice_skus_not_on_order": sorted(invoice_skus - po_skus),
        "delivery_skus_not_on_order": sorted(delivery_skus - po_skus),
        "currency": po.get("currency", ""),
    }


def build_questions(sets: list[dict]) -> dict:
    """Three independent Nouls and one Choice per document set, in one request.

    Same-goods, delivery-belongs-to-this-order and difference-is-explained are
    independent: a set can fail any combination of them. Separate Nouls, separate
    gates.
    """
    questions: dict[str, dict] = {}
    for index in range(len(sets)):
        reference = {
            "purchase_order": f"`sets[{index}].purchase_order`",
            "invoice": f"`sets[{index}].invoice`",
            "delivery_note": f"`sets[{index}].delivery_note`",
            "measured_differences": f"`sets[{index}].reconciliation`",
        }
        questions[f"same_goods_{index}"] = noul(
            {
                "task": "Do the invoice lines describe the same goods the purchase order asked for?",
                "documents": reference,
                "note": "The measured_differences field records differences already established from the documents. Read them as given.",
            },
            true="The invoice bills for the goods that were ordered, allowing for wording, packaging or an equivalent product supplied in their place.",
            false="The invoice bills for materially different goods from the ones ordered, or for goods that appear nowhere on the order.",
        )
        questions[f"delivery_belongs_{index}"] = noul(
            {
                "task": "Does this delivery note record receipt of goods against this particular purchase order?",
                "documents": reference,
            },
            true="The delivery note describes goods from this order arriving at the buyer, and the receiving details are consistent with it.",
            false="The delivery note is for a different order, a different site, or is missing the receipt confirmation that would make it evidence of anything.",
        )
        questions[f"explained_{index}"] = noul(
            {
                "task": "Do the documents themselves give a reason for the way they differ from each other?",
                "documents": reference,
            },
            true="A note, term or comment on one of the documents states why the goods, quantity or price differ, or records an approval for the difference.",
            false="The documents differ and say nothing about why, or there is no difference to explain.",
        )
        questions[f"reason_{index}"] = choice(
            {
                "task": "What reason does the paperwork give for the documents not agreeing?",
                "documents": reference,
            },
            REASONS,
        )
    return questions


def decide(
    reconciliation: dict,
    same_goods: float,
    delivery_belongs: float,
    explained: float,
    goods_gate: float,
    delivery_gate: float,
    explained_gate: float,
    value_tolerance: float,
) -> str:
    """Payment policy, in Python, with veto-ish ordering: the worst finding wins.

    Every numeric test here is a comparison against a pandas-computed figure.
    Every probability here came from a separate question, so every gate moves
    independently.
    """
    if delivery_belongs < delivery_gate:
        return "escalate_no_valid_receipt"
    if same_goods < goods_gate:
        return "escalate_wrong_goods"

    over_invoiced = reconciliation["qty_invoiced_minus_delivered"] > 0
    over_delivered = reconciliation["qty_delivered_minus_ordered"] > 0
    value_gap = abs(reconciliation["value_difference"]) > max(
        value_tolerance * reconciliation["ordered_value"], 0.01
    )

    if over_invoiced:
        return "hold_invoiced_above_receipt"
    if over_delivered and explained < explained_gate:
        return "hold_unexplained_over_delivery"
    if value_gap and explained < explained_gate:
        return "hold_unexplained_price_variance"
    if (value_gap or over_delivered) and explained >= explained_gate:
        return "pay_with_documented_variance"
    return "pay"


def main() -> None:
    st.set_page_config(page_title="Invoice Matcher", page_icon="📦", layout="wide")
    st.title("📦 Invoice Matcher")
    st.caption("Three-way match: order, invoice, delivery note. Agreement judged by Jev, every figure by pandas.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Gates")
        goods_gate = st.slider("Accept goods as the same above P=", 0.0, 1.0, 0.6, 0.05)
        delivery_gate = st.slider("Accept the delivery note above P=", 0.0, 1.0, 0.6, 0.05)
        explained_gate = st.slider("Accept an explanation above P=", 0.0, 1.0, 0.6, 0.05)
        value_tolerance = st.slider("Value tolerance", 0.0, 0.2, 0.02, 0.005,
                                    help="Compared in Python against the order value.")

    uploaded = st.file_uploader("Document sets JSON", type=["json"])
    sets = (
        json.loads(uploaded.read().decode("utf-8"))
        if uploaded
        else json.loads(SAMPLE.read_text(encoding="utf-8"))
    )
    if uploaded is None:
        st.caption(f"Bundled sample: {len(sets)} three-way document sets.")

    enriched = [{**document_set, "reconciliation": reconcile(document_set)} for document_set in sets]

    st.subheader("What Python already knows")
    st.caption("Computed before a single question is asked, and never asked of the model.")
    st.dataframe(
        pd.DataFrame(
            [{"po_number": s["po_number"], "supplier": s["supplier"], **s["reconciliation"]} for s in enriched]
        ).drop(columns=["invoice_skus_not_on_order", "delivery_skus_not_on_order"]),
        width="stretch", hide_index=True,
    )

    if not st.button(f"Match {len(enriched)} document sets", type="primary"):
        return

    questions = build_questions(enriched)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"sets": enriched}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, document_set in enumerate(enriched):
        reconciliation = document_set["reconciliation"]
        same_goods = answers.noul(f"same_goods_{index}")
        delivery_belongs = answers.noul(f"delivery_belongs_{index}")
        explained = answers.noul(f"explained_{index}")
        rows.append(
            {
                "po_number": document_set["po_number"],
                "supplier": document_set["supplier"],
                "invoice_no": document_set["invoice"]["invoice_no"],
                "invoiced_value": reconciliation["invoiced_value"],
                "value_difference": reconciliation["value_difference"],
                "qty_gap_vs_receipt": reconciliation["qty_invoiced_minus_delivered"],
                "same_goods": same_goods,
                "delivery_belongs": delivery_belongs,
                "explained": explained,
                "stated_reason": answers.choice(f"reason_{index}"),
                "reason_confidence": answers.confidence(f"reason_{index}"),
                "outcome": decide(
                    reconciliation, same_goods, delivery_belongs, explained,
                    goods_gate, delivery_gate, explained_gate, value_tolerance,
                ),
            }
        )

    out = pd.DataFrame(rows)
    payable = out[out["outcome"].str.startswith("pay")]
    blocked = out[~out["outcome"].str.startswith("pay")]

    columns = st.columns(4)
    columns[0].metric("Document sets", len(out))
    columns[1].metric("Clear to pay", len(payable))
    columns[2].metric("Held or escalated", len(blocked))
    columns[3].metric("Value on hold", f"{blocked['invoiced_value'].sum():,.2f}")

    st.subheader("Match results")
    st.dataframe(out.sort_values("outcome"), width="stretch", hide_index=True)

    st.subheader("Held and escalated")
    st.caption(
        "Three probabilities, three gates. 'Wrong goods' and 'no valid receipt' are "
        "different failures with different owners — buying versus goods-in — which is "
        "why they are separate Nouls rather than one compliance score."
    )
    st.dataframe(
        blocked[["po_number", "supplier", "invoice_no", "outcome", "stated_reason",
                 "same_goods", "delivery_belongs", "explained", "value_difference"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Stated reasons")
    st.bar_chart(out["stated_reason"].value_counts())

    st.download_button(
        "Download match report CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="three_way_match.csv",
        mime="text/csv",
    )
    st.caption(
        f"1 request, {len(questions)} questions over {len(enriched)} document sets, "
        f"{answers.input_tokens:,} input tokens, ${answers.cost_usd:.6f}, {answers.elapsed_s:.2f}s."
    )


if __name__ == "__main__":
    main()
