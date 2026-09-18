# 🧾 Invoice Extractor

Pull the invoice number, dates and totals off a messy invoice, with a hard
guarantee that every value came **verbatim out of the document**.

## The problem

Accounts payable receives invoices as whatever the supplier felt like sending:
a PDF export with the columns shifted, an OCR'd scan with `Description` spelt
`Descr iption`, a German invoice where `1.263,00` means one thousand two hundred
and sixty-three. Somebody types seven fields per invoice into the ledger.

Template extractors handle the suppliers you have already configured and break
on the next one. Regex alone gets you *candidates* — on a single invoice,
`998.20`, `199.64` and `1,197.84` are all money-shaped and sit on adjacent
lines, and `4410982` (a customer PO) looks exactly as much like an invoice
number as `NW-2291/A` does. Regex cannot tell you which is which.

An LLM can tell you which is which, and that is precisely the problem. Ask GPT
for "the grand total" and it will return a number. Sometimes that number is not
on the page. A transposed digit in an amount payable is a real loss, and nothing
in the output signals which fields were read and which were composed.

## Why this needs Jev

**Jev cannot generate. It can only select from options you supply.** That turns
a hallucination risk into a structural impossibility:

```
Python  FINDS      every date-, amount-, identifier- and name-shaped span   → find_candidates()
Jev     SELECTS    which of those spans is each field                        → one Choice per field
Python  NORMALISES the winning span to a float or an ISO date                → normalize_amount/date
```

The option **keys** are candidate indices and the option **descriptions** are
the span plus its containing line. The winning key indexes back into a list
Python sliced out of the raw text. There is no code path by which a value
absent from the document reaches the output — not a prompt instruction telling
the model to stay faithful, an absence of any mechanism for it to be otherwise.

The second structural property: **the model is never asked to compute.**
`jev-1.13` cannot do arithmetic and cannot compare dates, so parsing `1.263,00`,
checking that subtotal + tax reconciles against the total, and noticing that a
due date precedes its issue date all happen in Python — where they are exact,
free, and testable offline.

## How it works

One Choice per field, options built from the candidates of matching shape:

```python
def candidate_options(candidates: list[dict], kind: str) -> dict[str, str]:
    return {
        str(c["index"]): f'"{c["text"]}" — appearing on the line: {c["line"]}'
        for c in candidates
        if c["kind"] == kind
    }

options = candidate_options(document["candidates"], kind)
options["none"] = "No candidate span on this invoice is this field."
questions[f"{index}:{field}"] = choice(
    {"task": task, "note": note, "invoice_text": f"`invoices[{index}].text`"},
    options,
)
```

Every field of every invoice goes in **one** `ask()`. The bundled five-invoice
sample produces 35 questions in a single request; asking them one at a time
would be 35 round trips for the same answer.

Filtering candidates by shape before the model sees them matters for accuracy,
not just cost: `jev-1.13` degrades on large state full of irrelevant detail, and
there is no reason to offer eight money-shaped spans as answers to "which span
is the issue date".

Then Python does the arithmetic:

```python
def reconcile(subtotal, tax, total, tolerance=0.02):
    if subtotal is None or tax is None or total is None:
        return None
    if abs(subtotal + tax - total) <= tolerance:
        return None
    return f"{subtotal:,.2f} + {tax:,.2f} does not reconcile against {total:,.2f}"
```

A reconciliation failure is the cheapest accuracy signal you will get: it means
at least one of three selections is wrong, and it costs no inference to compute.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Five synthetic invoices ship with the app, so it runs before you upload
anything. They are built around the traps that actually break extractors:

| Sample | The trap |
|---|---|
| `NW-2291` | A customer PO (`4410982`) that is more invoice-number-shaped than the invoice number; a sort code (`20-45-77`) that is date-shaped |
| `2025-0417` | German `1.263,00` / `1.502,97`, and a delivery date sitting between the issue date and the due date |
| `RB88417` | Merchandise, credit, subtotal, tax and total on five consecutive lines — plus a **previous balance** that is not payable |
| `BH-0092` | Three date shapes (`11 Nov 2024`, `November 2024`, `02 Aug 2024`); "Due on receipt" means `due_date` should be `none` |
| `CG-3318` | No due date, no tax, no subtotal. Three `none` answers are the correct output |

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

## Design notes

**Why a Choice and not a Noul per span.** "Which span is the grand total" is
exactly one answer out of a mutually exclusive set, which is what a Choice is
for. Independent properties would be separate Nouls; these are not independent.

**Why every Choice carries `none`.** A Choice must return something. On the
`CG-3318` receipt there is no due date, no tax line and no pre-tax subtotal;
without the escape hatch the model would nominate the least-wrong amount at
entirely plausible confidence, and the ledger would silently gain a tax charge
that was never levied. Three of the correct answers on that invoice are `none`.

**Why the boundary cases live in `criteria` and not in the task text.**
`jev-1.13` reads literally. "A balance brought forward from an earlier invoice
is not payable here" is the sentence you would say out loud to a new
accounts-payable hire on their first day, and it belongs in the question.

**Why `day_first` is a toggle and not a guess.** `03/02/2025` is 3 February in
Britain and 2 March in America, and nothing in the document settles it.
Guessing in code would be worse than a knob you set for your supplier base. The
one case Python *can* settle — `14/03/2025`, where 14 cannot be a month — is
rescued automatically.

**Why thresholds are sliders.** The accept threshold decides what goes to a
human. Tune it on your own invoices; re-tuning re-runs no inference, because
the probabilities are already in hand.

## Limits

- **If the regex misses a span, the model cannot recover it.** This is the real
  price of the verbatim guarantee, and it is the failure mode to watch. The
  amount pattern requires two decimal places, so an invoice totalling `£1,500`
  yields no grand-total candidate at all. Extend `PATTERNS` for your corpus.
- **No line-item table extraction.** Individual line amounts are candidates, but
  the app does not reconstruct a structured `(description, qty, unit, ext)`
  table. That needs column geometry, which plain text has already thrown away.
- **`jev-1.13` cannot count, sum, or compare dates.** None of that is asked of
  it. If you extend this, keep it that way — `scripts/test_apps.py` AST-scans
  every question for arithmetic phrasing and fails the build on one.
- **It reads literally.** "Net 30" does not become a date, because the model is
  not asked to derive one; you get `none` and the term stays in the text.
- **It degrades on large, noisy state.** The per-kind candidate cap
  (`MAX_PER_KIND = 40`) exists for that reason. A dense 40-line invoice can
  produce dozens of identifier-shaped spans — bank details, VAT registrations,
  phone numbers — and accuracy on `invoice_number` falls as that set grows.
- **One invoice per text blob.** A scanned batch of six invoices in one file
  will produce one confused answer set, not six.
- **Confidence is not correctness.** A high winning probability on the wrong
  span is ordinary. The reconciliation check catches a useful slice of those;
  nothing catches all of them, which is why the review pile exists.
