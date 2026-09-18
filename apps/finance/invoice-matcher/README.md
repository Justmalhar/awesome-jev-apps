# 📦 Invoice Matcher

Three-way match: purchase order, invoice, delivery note. Pay only what you
ordered, at the price you agreed, for goods you actually received.

## The problem

Three-way match is the oldest control in accounts payable and it is still done
by hand, because the automated version only works when the three documents agree
perfectly — and when they agree perfectly there was nothing to check.

The whole job is the disagreements:

- The invoice says `NE-LED-40P / Philips CoreLine`, the order says `NE-LED-40 /
  Osram`. Approved substitution, or supplying whatever was in the warehouse?
- The invoice is for 5 units at £144, the order for 30 at £24. Wrong goods, or
  the same goods billed by the case?
- The delivery note number matches an order from three weeks ago and the goods on
  it are hex bolts, while the invoice is for washers.

ERP matching rules handle none of these. They flag all four as exceptions and a
human reads the PDFs, which is the work you were trying to avoid.

## Why this needs Jev

**The numbers were never the hard part.** Quantities, unit prices, totals,
value variance as a percentage of the order — pandas, exact, free, and
`jev-1.13` could not do any of it. So it does none of it. `reconcile()` computes
every figure before a single question is asked, and hands the results in as
facts:

```python
"qty_invoiced_minus_delivered": round(invoiced_qty - delivered_qty, 3),
"value_difference": round(invoiced_value - ordered_value, 2),
"sku_codes_agree": po_skus == invoice_skus == delivery_skus,
```

**The hard part is that identical mismatches mean opposite things.** `sku_codes_agree
== False` is true for the approved Philips substitution and true for the delivery
note stapled to the wrong order. One is payable, one is a control failure. The
codes cannot tell you which; reading "Osram line discontinued; Philips equivalent
supplied at the agreed Osram price, approved by J. Whitaker on 2026-01-17" can.

**Three independent failures need three independent gates.** "Wrong goods" is a
buying problem. "No valid receipt" is a goods-in problem. "Unexplained variance"
is a supplier problem. Different owners, different tolerances, so three Nouls:

```python
questions[f"same_goods_{index}"]       = noul(...)   # invoice vs order
questions[f"delivery_belongs_{index}"] = noul(...)   # note vs order
questions[f"explained_{index}"]        = noul(...)   # does the paperwork say why
questions[f"reason_{index}"]           = choice(..., REASONS)
```

plus one Choice for the *stated* reason — which is a claim made by the documents,
not a conclusion, and is labelled that way throughout.

All four, for every document set, in one request.

## How it works

The decision is pure Python and ordered worst-first, so the veto beats the
excuse:

```python
if delivery_belongs < delivery_gate:  return "escalate_no_valid_receipt"
if same_goods < goods_gate:           return "escalate_wrong_goods"
if over_invoiced:                     return "hold_invoiced_above_receipt"
```

Note the third line: billing for more than arrived is never payable no matter how
convincingly it is explained. That is policy, it belongs in code, and it is
tested at its boundary.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 8 document sets — one clean, plus a part shipment with a backorder note,
an unannounced 16.5% price rise, an approved substitution, an over-delivery
against an explicit no-over-delivery term, a duplicate invoice number reusing an
old delivery note, a case-versus-unit measurement clash, and a delivery left
unsigned at the wrong site with six racks crushed.

## Design notes

- **Nouls, not a Score.** "Same goods" is not a degree; it is a yes/no with
  uncertainty, and the uncertainty *is* the probability. A rubric here would
  invent an ordering between "wrong goods" and "wrong delivery note" that does
  not exist.
- **`no_explanation_in_the_paperwork` is the escape hatch.** Most document sets
  offer no reason for anything. Without that option the Choice must nominate one,
  and "backorder" on a clean set reads as a finding.
- **The Choice reports a claim, not a truth.** `stated_reason` is what the
  paperwork asserts. Believing the supplier's note is a separate decision and this
  app does not make it.
- **`explained` gates payment, but never overrides the quantity veto.** An
  explanation converts an unexplained variance into a documented one. It cannot
  convert over-billing into payment.

## Limits

- **Line-level matching is aggregated.** Quantities and values are summed across
  lines per document. A set where two lines are wrong in opposite directions nets
  to clean. Real AP needs per-line matching with a line-to-line assignment step —
  that is the receipt-triage app's pairwise pattern applied here, and it is not
  built.
- **`jev-1.13` cannot compare dates**, so nothing in this app reasons about
  whether the delivery beat the deadline, whether the invoice predates the
  delivery (a classic fraud signal), or whether the note is stale. Those are
  pandas comparisons and are not wired in.
- **Duplicate-invoice detection is only as good as the note.** PO-7771 is caught
  because the supplier wrote "Second submission". A silent duplicate needs a
  group-by on invoice number across the whole ledger, which is Python, and is
  upstream of this app.
- **Approvals are taken at face value.** "approved by J. Whitaker" is read as an
  approval claim. Whether J. Whitaker exists and had authority is a lookup against
  your own systems, not a judgment.
- **One delivery note per order.** Multi-drop deliveries against one PO break the
  model.

## Privacy

Purchase orders, invoices, delivery notes, supplier names, prices and the names
of staff who signed for goods are all sent to whichever provider
`providers.toml` selects. This is commercially sensitive financial text —
supplier pricing especially — leaving your machine. Check the provider's
retention policy before running this against live AP data.
