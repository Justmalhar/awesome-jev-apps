# 🏷️ Catalog Normalizer

Point it at three supplier feeds that describe the same products in three
different dialects. Get one canonical vocabulary back — plus an explicit list of
the values your vocabulary does not yet cover.

## The problem

Supplier A sends `Blk`. Supplier B sends `black`. Supplier C sends `Jet Black`.
Same colour, same product, three strings. The same happens to sizes (`M` /
`Medium` / `Med`), to materials (`100% Cotton` / `Cotton`), and worst of all to
capacities (`500ml` / `0.5 L` / `50cl`).

Downstream, faceted search shows three "black" filters, the dedupe job fails to
match the SKUs, and a merchandiser maintains a spreadsheet of synonyms that is
out of date the week a new supplier is onboarded. The spreadsheet is the real
product here — and nobody owns it.

Two things make this harder than a `str.lower()` and a lookup table:

1. **Suppliers file values under the wrong attribute.** A `color` column
   containing `Cotton` is not a colour, and no amount of colour-synonym matching
   will tell you that.
2. **New values arrive forever.** `Merino Wool` is a perfectly real material
   that your vocabulary has never heard of. A matcher that must return its best
   guess will quietly file it as `Cotton`.

## Why this needs Jev

**The no-match option is the point of the app.** Every `choice()` here carries
`not_in_vocabulary`, described in the criteria as:

> The value is a real attribute value, but no canonical term above means the same
> thing. Choose this rather than the closest term — it is the signal to extend
> the vocabulary, not to force a value into it.

A Choice always returns something. Without that option, a Choice over eight
colours *must* nominate one for `Cerulean`, and it will do so at a confidence
that looks like every other row. The escape hatch converts the model's inability
to abstain into an explicit, countable, actionable output: **these are the
values your vocabulary is missing.** That list is the thing a merchandiser
actually wants, and it is exactly what a fuzzy-string matcher can never produce,
because a fuzzy matcher's nearest neighbour is always *something*.

The second structural fit is that this is a **judgment over a small closed set**,
run over distinct values rather than rows — which makes it a lookup table you
build once and join, not an inference you pay for per row.

## Units: the most instructive part of this app

`jev-1.13` cannot do arithmetic. That is a documented failure mode, not a
prompting problem. So **the model is never asked to convert `0.5 L` into
millilitres.** One regex in Python does it:

```python
MEASURE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Za-z][A-Za-z. ]*?)\s*$")

def parse_measure(raw_value):
    """Split "0.5 L" into (0.5, "l"). Python's job, start to finish."""
```

and `resolve_value()` multiplies by the factor from `sample_vocabulary.json`.
Values that parse as a measurement are not even asked a term question — they
skip it entirely, which is a real token saving as well as a correctness one.

**But the split is not "Python does numbers, Jev does nothing here".** Look at
`16 oz`:

| raw value | product | attribute | canonical |
|---|---|---|---|
| `16 oz` | Hydrating Shampoo | volume | `473.176 ml` |
| `16 oz` | Whole Bean Coffee | weight | `453.592 g` |

Same string. Same magnitude. Same unit abbreviation. Different attribute,
different conversion factor, different number — and the only thing that
disambiguates it is knowing what the product is. Jev picks the **attribute**;
Python applies the **factor**. Neither half works alone, and putting the
multiplication on the model's side of that line is how this app would fail.

The same division catches the supplier who put `Cotton` in a `color` column:
the attribute Choice disagrees with the column header, and the row is routed to
`relabel` instead of being normalised into a colour that does not exist.

## How it works

One request, two questions per distinct value (one for a measurement), all
independent, all against the same state:

```python
questions[f"attr_{index}"] = choice(
    {"task": "Which canonical product attribute does this raw supplier value actually describe?",
     "value": reference,
     "note": "The supplier's own label is often wrong -- judge the value itself, "
             "using the products it appears on as context.",
     "units": "The numeric magnitude and its unit have already been separated in code. "
              "Decide only which attribute the measurement belongs to: the same unit "
              "abbreviation can mean a capacity on a drink and a mass on dry goods."},
    attribute_options(vocabulary),          # + not_an_attribute
)
if item["measure"] is None:
    questions[f"term_{index}"] = choice(..., term_options(vocabulary))   # + not_in_vocabulary
```

The term Choice offers the **union** of every non-numeric attribute's terms,
namespaced `color:Black`, `size:Medium`. That is deliberate: it is how a value
in the wrong column gets caught, and it keeps everything in one request instead
of a second round trip conditioned on the first answer.

**Deduplication is where the money is.** The same `Blk` appears on every black
product from that supplier, so the app resolves only distinct
`(attribute, value)` pairs and joins the mapping back with `pandas.merge`. The
bundled sample collapses 59 rows to 31 values — the footer prints the real
ratio. On a production feed the same 200-odd distinct values cover millions of
rows, and the cost of the run does not move.

Batches are sized against `provider.context_tokens`, never a hardcoded window,
because the vocabulary is repeated in the option descriptions of every question.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A 59-row feed from three suppliers and a canonical vocabulary ship with the app
and load by default. The sample deliberately contains obvious synonyms, two
values filed under the wrong attribute (`color = Cotton`, `size = Navy`), three
values no canonical term covers (`Cerulean`, `XXS`, `Merino Wool`), and `16 oz`
on both a shampoo and a bag of coffee.

Your own feed needs `sku, supplier_id, raw_attribute, raw_value, product_title`.
Your own vocabulary is a JSON of `attribute -> {kind, description, terms|units}`;
see `sample_vocabulary.json`.

## Design notes

**Why two Choices and not one.** "Which attribute?" and "which term?" are
different questions with different option sets, and the first one has to be
answerable for values that have no term at all. Merging them into a single
flat Choice over `attribute:Term` would make `not_an_attribute` and
`not_in_vocabulary` indistinguishable — and those two route to completely
different humans.

**Why not a second `ask()`.** It is tempting to resolve the attribute first and
then offer only that attribute's terms. That would be two round trips where the
second depends on the first. Offering the union costs a few hundred tokens once
per batch and keeps it to one request. A second `ask()` is only justified when
an earlier answer changes what *state* you send — not merely which options you
list.

**Why the confidence gate does not override `relabel` and
`extend_vocabulary`.** Both are decisions about the *vocabulary*, not about the
row — a confident vocabulary gap is still a vocabulary gap, and suppressing it
because the model was sure would defeat the point. Confidence gates only the
automatic path.

**Why thresholds are sliders.** The raw confidences are in the result frame and
the downloaded CSV, so moving the gate re-derives every table on screen without
one new request.

**Why descriptions, not bare labels.** `"Navy": "Dark blue verging on black.
Distinct from the brighter Blue."` — that second sentence is doing the work.
`jev-1.13` reads literally; the boundary between `Navy` and `Blue` has to be
written down, not assumed.

## Limits

- **This is a mapping proposal, not a data contract.** Nothing here validates
  that a SKU should have a `volume` at all, or that two suppliers' disagreeing
  values can both be right. The per-SKU table shows `500 ml | 473.176 ml` as
  two values and leaves the reconciliation to you — that is a genuine supplier
  disagreement, and pretending otherwise would be worse.
- **Unit coverage is the vocabulary's, not the model's.** If a supplier writes
  `1 pt` and `pt` is not in `units`, the value lands in the curator queue. That
  is Python failing, and the fix is a line of JSON — no re-run of inference.
- **`jev-1.13` cannot count, sum, or compare dates.** Nothing in this app asks
  it to; every count, rate, ratio and conversion on screen is pandas or a regex.
  If you extend it, keep that line where it is.
- **Confidence is distribution concentration, not correctness.** A confidently
  wrong synonym is still wrong. The gate sorts by how much the model was
  guessing, which is useful but is not an accuracy estimate.
- **`not_in_vocabulary` is not free of false positives.** An unusual but covered
  synonym can land there. That is the safe direction of error — a value proposed
  for review costs a minute, a value silently mapped to the wrong term costs a
  quarter of bad facet data — but it does mean the gap list needs a human, not
  an automatic `vocabulary.json` patch.
- **Product titles go to the provider.** Read `providers.toml`'s chosen
  provider's data policy before pointing this at an unreleased catalogue.
