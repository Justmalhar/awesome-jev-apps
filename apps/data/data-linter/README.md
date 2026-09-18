# 🧹 Data Linter

Finds rows whose fields **contradict each other**. Not "is this value valid" —
every value in these rows is perfectly valid. `Lisbon` is a real city. `Spain`
is a real country. The row is still wrong.

## The problem

Every data quality tool you already have is a *column-at-a-time* tool.
dbt tests, Great Expectations, Pandera, a `CHECK` constraint, a regex in an
ingest job — they all ask the same shape of question: is this **value** in this
**column** acceptable on its own?

So this row passes everything:

| job_title | seniority | city | country | currency | account_status | description |
|---|---|---|---|---|---|---|
| Intern | C-level | Vienna | Austria | EUR | active | Twelve-week summer placement shadowing the platform team. |

`Intern` is in the title enum. `C-level` is in the seniority enum. Both columns
are 100% populated, 100% valid, and the row is nonsense.

These errors are the residue of merges, migrations and manual edits, and they
are the ones that survive longest — precisely because nothing catches them. They
surface later as a headcount report that says you employ four C-level executives
in Austria, or a payout in the wrong currency.

You *could* write the validators. To catch the geography rule you need a
gazetteer of every city and its country. For currency you need an ISO 4217
country map you keep current. For title-vs-seniority you need a title taxonomy
somebody has to own forever. For "the free-text description contradicts the
status column" there is no validator to write at all. Nobody writes these. They
write `not_null` and go home, which is the correct decision given the cost.

## Why this needs Jev

**The rule becomes prose, and prose is cheap.** The entire geography validator
is this:

```python
{"id": "geo",
 "fields": ("city", "country"),
 "task": "Is the city in this record located somewhere other than the country in this record?",
 "true": "The named city is not in the named country. Example: city Toronto with country Czechia.",
 "false": "The named city is in the named country, or the pairing is a genuine same-name city that really exists there."}
```

No gazetteer, no maintenance, and the `false` clause handles the case that makes
a lookup table wrong anyway — there really is a Paris in Texas.

**And — this is the structural part — every rule is a separate Noul.** A row can
have a wrong city *and* a wrong currency *and* a title that does not match its
seniority. Those are independent properties, so they are independent
probabilities with independent thresholds:

```python
for rule in rules:
    questions[f"{rule['id']}_{index}"] = noul({...}, true=rule["true"], false=rule["false"])
```

Collapse them into one Score ("how bad is this row?") or one Choice ("what is
wrong with this row?") and you lose the only output anyone acts on: **which rule
fired**. The per-rule breakdown in the app — the chart showing that 6% of rows
fail geography and 0.2% fail currency — exists *because* they are separate
Nouls. That is what routes the fix to the right team.

A Noul also returns a probability, not a verdict, which is what makes the
review band possible. `P=0.98` on `Lisbon`/`Spain` and `P=0.55` on a genuinely
arguable pairing are different situations, and the second is the one you want a
person to see.

## How it works

Per row: one Noul per active rule, plus one Choice naming the field most likely
to hold the bad value. All of it in **one** `ask()` per batch.

```python
questions[f"culprit_{index}"] = choice(
    {"task": "If one field in this record holds an incorrect value, which field is it?",
     "record": f"`rows[{index}]`",
     "note": "Judge only against the other fields in the same record. "
             "Do not use outside knowledge of the person or account."},
    culprit_options(columns),   # every column, plus no_contradiction, plus unclear
)
```

The Choice has **two** escape hatches, and they mean different things:
`no_contradiction` (the row is fine) and `unclear` (the fields really do
conflict, but the row does not show which side is wrong — `Toronto`/`Czechia`
could be either a wrong city or a wrong country). Without them the Choice must
name a field, and it will name one, with entirely plausible confidence.

Rules whose columns are absent from your CSV are dropped **in Python** before
any request is made, so you never pay for a structurally meaningless question.

Batching sizes itself against `provider.context_tokens`, so the same code runs
on TypeSafe's 64k and OpenRouter's 32k without a constant changing.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

32 employee/customer records are bundled, with contradictions planted for every
rule and a majority of rows that are genuinely clean — a linter that flags
everything is as useless as one that flags nothing.

Your own CSV needs no particular schema. Rules activate when their columns are
present; the sidebar shows which were skipped and why.

## Design notes

**Why `record` is passed alongside the two fields.** Each Noul names its two
fields by path *and* includes the whole row. The named fields tell the model
where to look — `jev-1.13` degrades on indirection — and the rest of the row is
the context that resolves ties. `Copenhagen`/`Norway` is a contradiction; seeing
`DKK` in the same row makes it a clearer one.

**Why the thresholds are sliders and not constants.** The flag threshold, the
review band width and the culprit confidence gate are all Python. The raw
probabilities go into the downloadable CSV, so re-tuning the entire policy costs
zero additional inference. Never bake a threshold into an instruction — you can
only change it by paying for the whole table again.

**Why the review band exists below the threshold, not around it.** A
contradiction is an assertion about the data; the band is the region where the
model leaned that way without committing. Rows in it are worth a look, not a
ticket.

## Limits

- **Cross-field only, and only within one row.** It cannot see that a row
  contradicts a *different* row, or a foreign key, or last month's export. That
  is a join, and joins belong in SQL.
- **Rules are pairwise by design.** A three-way contradiction (title, department
  and description all mutually inconsistent) shows up as several pairwise flags,
  not one. Usually that is what you want; occasionally it double-counts.
- **`jev-1.13` cannot count, sum, or compare dates.** So `start_date` after
  `end_date` — the single most common cross-field bug in real data — is *not*
  a rule you can add here. Write that one in pandas. It is three lines and it is
  exact; do not reach for a model where `<` will do.
- **It reads literally.** Every judgement is the plain reading of your `true`
  and `false` text. If a rule fires on rows you think are fine, the fix is
  almost always a boundary case missing from the `false` clause, not a
  threshold.
- **Culture and naming bias.** Job-title and department conventions are not
  universal, and the model's sense of "normally sits in" reflects whatever is
  common in its training data. Expect more false positives on non-English titles
  and on org charts that are deliberately unusual. Check the flagged set before
  you let anything act on it.
- **It cannot fix anything.** It nominates the suspect field. It cannot tell you
  the correct value, because it does not generate — it selects.
