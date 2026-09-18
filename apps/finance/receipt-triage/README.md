# 🧾 Receipt Triage

Give it a card feed and a pile of receipts. It tells you which receipt belongs to
which charge, and — the part anyone doing month-end actually cares about — which
charges have nothing behind them.

## The problem

Month-end reconciliation is a human doing a join by eye. Fifteen card charges,
fourteen receipts, and the answers are not a clean one-to-one: the Dishoom
receipt says £82.00 but the card says £98.40 because service was added at the
till; the Paris lunch receipt is in euros; there are two Pret receipts for £12.20
on the same day from two different branches, and two Pret charges to match them
to; the BA confirmation got forwarded twice.

Exact-match tooling gets the easy 60% and dumps the rest on a person, which is
the 40% that takes the afternoon.

## Why this needs Jev

**The comparisons are Python's, and the tie-breaks are not.** Date windows and
amount tolerances are arithmetic — `jev-1.13` cannot do them, and does not need
to, because pandas does them exactly. Blocking on those two facts throws away
~85% of the n×m grid before a single question is asked, and prints the reduction.

What survives is genuinely ambiguous, and ambiguous in a way that is about
*meaning*: is a £16.40 gap a service charge on the same dinner, or a different
dinner? Is a €74.00 receipt the same purchase as a £64.32 charge? A tolerance
cannot answer that. It either widens until it matches everything or stays tight
and misses every tipped meal.

**The two-tier structure is the interesting part.** Pairwise judgment is
structurally myopic. `TXN-507 ↔ RCP-F` and `TXN-507 ↔ RCP-G` are, viewed in
isolation, equally excellent matches — same chain, same day, same £12.20 — and
nothing *inside* either pair distinguishes them. So there is a second tier:

```python
questions[f"curate_{index}"] = choice(
    {"task": "Which one of these receipts is the receipt for this card charge?",
     "card_charge": f"`curations[{index}].transaction`",
     "candidate_receipts": f"`curations[{index}].candidates`"},
    {**curations[index]["options"], NO_MATCH: "None of these receipts is for this charge."},
)
```

A Choice that sees all the candidates for one charge at once, and must pick one —
including `none`, which is what makes "this charge has no receipt" a first-class
answer rather than the absence of one.

Both tiers are questions over the same state, so **they cost one request
between them**. That is the property being exploited: independent questions run
in parallel against state ingested once, so a second analytical tier is nearly
free. In a chat-completions world the curator pass would double the bill.

Then Python resolves them. A receipt matched to two charges is a double claim, so
the one-to-one constraint is enforced by greedy assignment in code — it is a
constraint, not an opinion.

## How it works

```
pandas   ->  candidate_pairs()   date window + amount tolerance, keeps ~15% of the grid
Jev      ->  noul per pair       "same single purchase?"
Jev      ->  choice per charge   "which of these, or none?"
pandas   ->  combine() + assign() fuse, then enforce one-to-one
```

Fusion is a slider (`curator_weight`), not a constant baked into a prompt, so you
can re-tune the balance between the tiers and re-derive the whole report without
a new request.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 15 card charges and 14 receipts, planted with a service-charge gap, a
euro receipt, a duplicate forwarded confirmation, a personal Waterstones receipt
with no matching charge, and the two-identical-Prets ambiguity that exists purely
to make the curator tier earn its place.

## Design notes

- **Noul for the pair, Choice for the curation.** The pair question is a genuine
  yes/no over one hypothesis; the curation is a selection among mutually
  exclusive options. Using a Score for either would invent an ordering that does
  not exist.
- **`none` is not optional.** Roughly a fifth of real charges have no receipt at
  all. Without the escape hatch the curator must nominate something, and it will,
  at perfectly respectable confidence.
- **The foreign-currency exemption is in the blocker, not the question.** A EUR
  receipt is kept regardless of total, because the two numbers are *supposed* to
  disagree. Encoding that in Python is one line; encoding it in an instruction is
  a hope.
- **Measurements go in as facts.** `amount_difference` and `calendar_days_apart`
  are computed by pandas and handed over as state. The model reads them; it is
  never asked to derive them.

## Limits

- **Greedy assignment is not optimal.** It takes the highest-scoring pair first
  and never backtracks, so a pathological set can be assigned suboptimally. The
  fix is the Hungarian algorithm; the reason it is not here is that with a
  sensible accept gate the contested set is tiny and a human is looking at it
  anyway.
- **One receipt, one charge.** Split payments (one dinner across two cards) and
  aggregated charges (one hotel charge covering three itemised nights) both break
  the one-to-one model. They need a different data model, not a better prompt.
- **`jev-1.13` cannot compare dates.** Everything about "how far apart" is
  pandas. If you widen the window to 30 days, the model will not object on
  temporal grounds, because it cannot.
- **Blocking is a recall ceiling.** A pair the tolerance rejects is never judged,
  so a 60% tip on a £10 coffee is invisible at the default 25%. Keep the window
  and tolerance generous — the cost of a junk candidate is a fraction of a cent.
- **Orphan receipts are listed, not explained.** The app will not tell you whether
  RCP-M is a duplicate or a missing charge. That is a next question, and it is
  yours.

## Privacy

Card descriptors, merchant names, receipt line items and totals are sent to
whichever provider `providers.toml` selects. Receipts are unusually revealing
financial text — they carry what you bought, not just where — so read the
provider's retention policy before pointing this at a real card feed.
