# 🧮 Tax Classifier

Sort a year of business spending into piles: claim it, exclude it, or put it in
front of a human. The last pile is the product.

> ## ⚠️ This is not tax advice
>
> This tool produces a **triage list**, not a filing position. It does not know
> your jurisdiction, your trading structure, your accounting basis, or the
> hundred facts about your circumstances that decide whether a cost is
> deductible. Its category names are plain English, not statutory ones. It has
> no view on your national insurance, VAT, capital allowances election, or what
> your last inspector said.
>
> Nothing it outputs should be entered on a return without an accountant looking
> at it. Its actual value is the opposite of automation: it finds the ~20% of
> your spending that genuinely needs professional judgment, so the professional
> spends their hour there instead of on your Adobe subscription.

## The problem

A sole trader ends the year with 800 card lines and a shoebox. Most of it is
obvious in both directions — the Google Workspace bill is deductible, the weekly
Sainsbury's shop is not — and the obvious stuff is what fills the accountant's
billable hours.

The stuff that actually matters is a handful of lines: the phone contract used
for both, the energy bill for the back bedroom, the £240 suit for the pitch, the
Paris trip with an extra private night on the end, the garage conversion. Those
are the ones with a wrong answer, and they are invisible in a spreadsheet sorted
by amount.

## Why this needs Jev

**The confidence is the product.** A Choice *always* returns a category, whatever
it is given, with no more hesitation for `Selfridges — suit for the pitch meeting`
than for `Companies House — filing fee`. What distinguishes them is the shape of
the distribution: concentrated when the answer is obvious, spread when the model
is genuinely torn between `personal` and `marketing`.

That spread is not noise to be suppressed. In this domain it correlates almost
exactly with the cases where a human is needed — because ambiguity in the model
and ambiguity in the tax treatment come from the same place: a cost that serves
two purposes at once.

```python
if category == UNCLEAR or confidence < confidence_gate:
    return "ask_a_human"
```

**Three independent properties, three questions.** An expense can be correctly
categorised, *and* part-private, *and* capital. They are orthogonal, so they are a
Choice and two Nouls — not one rubric:

- `mixed_` — does this also serve a private purpose? (gate deliberately **low**,
  at 0.4: apportionment is a split somebody has to defend, and no model should
  pick a percentage)
- `capital_` — lasting asset or running cost? (different treatment entirely, so
  it leaves the claim route rather than being claimed in full)
- `evidence_` — a Score whose three levels are three different conversations with
  a reviewer, from "you'd have to ask what this was" to "this ties to a named job
  in the records"

**And not one number is asked of the model.** Category totals, the relief
estimate, the percentage reaching a human — all pandas:

```python
def relief(claimable, tax_rate):
    return round(claimable * tax_rate, 2)
```

`jev-1.13` cannot multiply, so it is not asked to. The marginal rate is a slider,
because it is a fact about you, not a judgment about the expense.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 25 expenses from a freelance designer's quarter, planted with the exact
cases that should *not* auto-file — a dual-use phone contract, a household energy
bill with a working-from-home claim, a suit, a garage conversion, a trip with a
private night attached, a parking fine, and a tax payment on account (which is
not an expense at all).

## Design notes

- **Categories are plain English on purpose.** Statutory box names would imply a
  precision this tool does not have and invite people to copy the output onto a
  return. `office_and_software` is a pile, not a box number.
- **`client_entertaining` is a category, not a low-confidence case.** In several
  jurisdictions it is disallowed however commercial the motive, so it is
  categorised correctly and then excluded in Python — the model is not asked
  whether it is allowable, only what it is.
- **`unclear` routes to a human even at confidence 1.0.** A confident "I cannot
  tell" is still "I cannot tell". Tested.
- **The mixed-use gate is low and the confidence gate is high.** Asymmetric,
  because the costs are asymmetric: over-routing to a human wastes five minutes,
  under-routing puts a wrong number on a return.
- **Order of operations is policy.** Disallowed beats unclear beats
  apportionment beats capital. All in Python, all tested at the boundary.

## Limits

- **No jurisdiction, no legislation, no rates.** There is no tax code in here.
  The categories encode a widely-held intuition about business costs, and
  intuitions are wrong at the edges — which is where tax lives.
- **The relief figure is arithmetic on an assumption.** `claimable × your slider`.
  It is not a computation of your liability and does not model bands, allowances,
  thresholds, or anything else.
- **No apportionment.** The app identifies dual-purpose costs and refuses to split
  them. That refusal is the design; a model that guessed "70% business" would be
  inventing a defensible-sounding number with nothing behind it.
- **One expense at a time.** No view of the year as a whole, so it cannot see that
  you have claimed a home-office proportion twice, or that the same conference
  appears on three lines.
- **`jev-1.13` cannot compare dates**, so nothing here reasons about accounting
  periods, year ends, or whether a cost falls in the right year.
- **Descriptions are all it has.** `AMZN MKTP UK*2K8LP` with no description is
  unclassifiable, and the app will correctly tell you so rather than guess. Garbage
  in, `unclear` out — which is the honest failure mode, but still a failure mode.

## Privacy

Vendor names, amounts and expense descriptions are sent to whichever provider
`providers.toml` selects. Expense descriptions are unusually revealing — they say
where you were, who you met and what you bought — and this is financial data
about a specific taxpayer leaving your machine. Read the provider's data-retention
policy before running this on real books.
