"""Offline checks for invoice-extractor. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    # Each app ships its own module named `app`; the shared runner imports them
    # into one process, so drop any predecessor before resolving ours.
    sys.modules.pop("app", None)
    sys.modules.pop("jev_provider", None)
    import app

    invoices = app.load_sample()
    assert len(invoices) >= 4, "bundled sample must cover several layouts"
    documents = app.prepare(invoices)
    by_id = {d["id"]: d for d in documents}

    # --- stage 1: regex actually finds the spans the app depends on ----------
    def spans(doc_id: str, kind: str) -> list[str]:
        return [c["text"] for c in by_id[doc_id]["candidates"] if c["kind"] == kind]

    # Two date-shaped strings on the UK invoice: issue and due.
    assert "14/03/2025" in spans("NW-2291", "date")
    assert "13/04/2025" in spans("NW-2291", "date")
    # The PO number must be offered alongside the real invoice number, or the
    # trap this sample exists to pose never reaches the model.
    assert "NW-2291/A" in spans("NW-2291", "identifier")
    assert "4410982" in spans("NW-2291", "identifier")
    # Subtotal, tax and total are all amount-shaped and adjacent.
    for amount in ("998.20", "199.64", "1,197.84"):
        assert amount in spans("NW-2291", "amount"), amount
    # European separators survive extraction verbatim.
    for amount in ("1.263,00", "239,97", "1.502,97"):
        assert amount in spans("2025-0417", "amount"), amount
    # "11 Nov 2024" must not be shredded into "Nov 2024" by the later pattern.
    assert "11 Nov 2024" in spans("BH-0092", "date")
    assert "Nov 2024" not in spans("BH-0092", "date")
    assert "November 2024" in spans("BH-0092", "date")
    # Issuer and billed party both surface; the model has to tell them apart.
    assert "=== RIVERBEND SUPPLY CO ===" in spans("RB88417", "name")
    assert "Bill To: Meridian Dental Group" in spans("RB88417", "name")

    # Candidate indices are what the option keys mean; they must be dense.
    for document in documents:
        assert [c["index"] for c in document["candidates"]] == list(
            range(len(document["candidates"]))
        )

    # --- stage 2: question design -------------------------------------------
    questions = app.build_questions(documents)
    assert len(questions) == sum(
        1
        for i, d in enumerate(documents)
        for field, (kind, _t, _n) in app.FIELDS.items()
        if any(c["kind"] == kind for c in d["candidates"])
    )
    for qid, question in questions.items():
        assert question["type"] == "choice", qid
        # Every Choice needs an escape hatch, or absent fields get a wrong span.
        assert "none" in question["criteria"], qid
        assert len(question["criteria"]) >= 2, qid
        # Option keys other than the escape hatch must be candidate indices.
        index = int(qid.split(":")[0])
        valid = {str(c["index"]) for c in documents[index]["candidates"]}
        assert set(question["criteria"]) - {"none"} <= valid, qid

    # The invoice with no due date, no tax and no separate subtotal still gets
    # those questions asked -- 'none' is the answer, not a missing question.
    cg_index = next(i for i, d in enumerate(documents) if d["id"] == "CG-3318")
    for field in ("due_date", "tax_amount", "subtotal"):
        assert f"{cg_index}:{field}" in questions

    # --- stage 3: normalisation ---------------------------------------------
    for raw, expected in (
        ("1,197.84", 1197.84),      # UK/US thousands
        ("1.502,97", 1502.97),      # European, both separators
        ("1.263,00", 1263.00),
        ("24,90", 24.90),           # European decimal comma
        ("4,500.00", 4500.00),
        ("-14.74", -14.74),         # credit line stays negative
        ("225.00", 225.00),
        ("TOTAL   1,197.84", 1197.84),
    ):
        assert app.normalize_amount(raw) == expected, (raw, app.normalize_amount(raw))
    assert app.normalize_amount("no digits here") is None

    assert app.normalize_date("2025-06-02") == "2025-06-02"
    assert app.normalize_date("14/03/2025") == "2025-03-14"
    assert app.normalize_date("03.02.2025", day_first=True) == "2025-02-03"
    assert app.normalize_date("03.02.2025", day_first=False) == "2025-03-02"
    # Unambiguous the other way round: rescue rather than throw the value away.
    assert app.normalize_date("14/03/2025", day_first=False) == "2025-03-14"
    assert app.normalize_date("11 Nov 2024") == "2024-11-11"
    # No day stated -> refuse, rather than invent the 1st.
    assert app.normalize_date("November 2024") is None
    # A sort code is date-shaped and is not a date.
    assert app.normalize_date("20-45-77") is None

    # --- the arithmetic Python does, and the model never sees ----------------
    assert app.reconcile(280.01, 20.30, 300.31) is None
    assert app.reconcile(280.01, 20.30, 300.34) is not None
    assert app.reconcile(280.01, 20.30, 300.32, tolerance=0.05) is None  # boundary
    assert app.reconcile(None, 20.30, 300.31) is None                    # can't judge
    assert app.order_check("2025-06-02", "2025-07-02") is None
    assert app.order_check("2025-07-02", "2025-06-02") is not None
    assert app.order_check("2025-06-02", None) is None

    assert app.decide(0.70, 0.70) == "extracted"   # boundary is inclusive
    assert app.decide(0.6999, 0.70) == "review"
    assert app.decide(1.0, 0.70) == "extracted"


if __name__ == "__main__":
    check()
    print("ok")
