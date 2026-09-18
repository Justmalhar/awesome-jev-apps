# 🔍 PII Scanner

Point it at a column-profile dump of your warehouse. Get back a PII register:
every column tagged with the kind of personal data it holds, whether it
identifies someone on its own, whether it helps identify someone in company,
whether it is special-category, and what handling tier that adds up to.

38 columns costs a fraction of a cent. A 900-column warehouse is still well
under a cent, because the cost scales with the schema and not with the rows.

## The problem

A DSAR arrives, or legal asks which tables fall under the retention policy, or
an engineer wants to copy prod into staging. Somebody now has to say which of
900 columns across 60 tables hold personal data.

Today that is a spreadsheet, a data steward's memory, and a regex pass that
finds `email` and stops. The regex pass misses `events.ua_string` (a device
identifier), misses `hr.absence_reason_code` (sickness data behind a two-letter
code), and confidently flags `orders.customer_id`, which turns out to be a dead
legacy column with 38 distinct values across 260,000 rows. Every one of those
mistakes has the same cause: the shape of a value tells you nothing about what
it means.

## Why this needs Jev

Two structural properties, and the app is built around both.

**One: the unit of work is a column profile, not a column.** Python reduces each
column to a fixed-size record — name, table, type, null rate, distinct count,
uniqueness ratio, a few masked example values, and which cheap regexes matched.
That profile is ~80 tokens whether the table has 1,800 rows or 88 million. So a
whole warehouse is one batched pass, and re-running it after a schema change is
loose change. **The filtering trick is that the regex prefilters are attached as
hints and never used to drop a column.** `ua_string` matches the IP-address
regex (because a Chrome version number looks like an IP) and `absence_reason_code`
matches nothing at all — a prefilter used as a filter would get both wrong in
opposite directions. Regex does shape, Jev does meaning, and the profile carries
both into the same question.

**Two: three independent Nouls, not one sensitivity score.** This is the app's
whole thesis. These three properties are genuinely independent and routinely all
true at once:

- *on its own this column singles out one specific person* — direct identifiability
- *combined with two or three other columns in the same table this would single out one person* — quasi-identifier
- *this falls in a special category needing extra protection* — health, biometrics, religion, sexuality, trade-union membership

`users.date_of_birth` is **no**, **emphatically yes**, **no**.
`hr.absence_reason_code` is **no**, **weakly yes**, **emphatically yes**.
`users.email` is **yes**, **yes**, **no**.

A single "sensitivity: high/medium/low" rubric cannot represent any of those
three rows, and worse, it destroys the thresholding. A privacy team wants to mask
everything special-category at P>0.5 while tolerating quasi-identifiers up to
P>0.8 — that is two different knobs on two different distributions. Collapse them
into one number and there is one knob, pointed at an average nobody asked for.

Noul probabilities are also directly comparable and directly thresholdable, which
is what lets the whole policy live in the sidebar.

## How it works

Python profiles first, and does every statistic:

```python
profile = {
    "table": raw["table"], "column": raw["column"], "type": raw["type"],
    "null_rate": round(float(raw["null_rate"]), 3),
    "distinct_values": distinct,
    "uniqueness": round(min(distinct / rows, 1.0), 4),   # Python divides, never the model
    "regex_shapes_found": hits or ["none"],
}
```

Then one Choice and three Nouls per column, batched into one request:

```python
questions[f"cat_{index}"] = choice({"task": "What kind of personal data, if any, "
                                            "does this warehouse column hold?",
                                    "column": reference}, CATEGORIES)
questions[f"direct_{index}"] = noul(
    {"task": "On its own, does one value from this column pick out one specific person?", ...},
    true="Somebody holding a single value from this column, and nothing else, could tell "
         "which individual the row is about",
    false="A single value is shared by many people, refers to no person, or is meaningless "
          "without joining to another table")
```

And Python maps the four answers onto a handling tier:

```python
def decide(category, direct, quasi, special, *, mask_p, restrict_p):
    if direct >= mask_p or special >= mask_p or category in ALWAYS_MASK:
        return "mask"
    ...
```

Both thresholds are sidebar sliders. Moving one re-tiers all 38 columns and
sends nothing — the raw probabilities are already in the dataframe and in the
downloadable register.

## 🔒 Privacy note — read this before you run it

**This app sends your column names, your table names, your profile statistics
and, if you let it, your example values to a third-party API.** A column named
`patients.hiv_status` leaks a great deal on its own; three real values from it
leak considerably more.

The sidebar has a **"Send example values"** toggle, defaulting to **off**. When it
is off the `examples` key is not present in the state at all — this is an
omission in `profile_column()`, not a redaction string, so there is nothing to
leak. `test_app.py` asserts it.

Recommended, in order:

1. **Names + profiles only** (the default). Works well for `users.email`,
   `orders.billing_iban`, `hr.absence_reason_code` — anything where the name and
   the cardinality carry the signal.
2. **Masked or synthetic samples**, as in the bundled sample file, where values
   are already starred out. Profile your warehouse into this JSON shape with your
   own masking, and check the file by eye before you upload it.
3. **Raw production values**: only against a provider whose data policy you have
   actually read, and only if your DPA covers it.

The cost of running names-only is real: free-text columns like
`support.note_body` become nearly unjudgeable, because the name says "note" and
nothing else. That is the honest trade, and the app prints which mode produced
each run in the footer.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A 38-column, 5-table synthetic warehouse profile ships with the app and is the
default, so it runs cold. Your own file is JSON: a list of
`{table, column, type, row_count, null_rate, distinct_count, examples}`. Every
warehouse can produce that from `information_schema` plus one `approx_count_distinct`
query per column.

## Design notes

**Why a Choice for the category.** The ten categories are mutually exclusive by
construction — a column has one primary kind. `not_personal_data` is the no-match
option, and it is load-bearing: without it, a Choice must file `currency_code`
somewhere personal, and it will, at plausible confidence.

**Why the category descriptions are long.** `jev-1.13` reads literally.
`"financial_instrument"` as a bare label leaves `total_amount_cents` ambiguous,
so the description says "Not an order total or a price". Boundary cases belong in
the criteria, not in your head.

**Why uniqueness is precomputed.** The model cannot count and cannot divide —
documented failure modes. So Python computes `distinct / rows` and hands over the
ratio. That ratio is what exposes `orders.customer_id`: a column whose name
promises a person pointer but whose 38 distinct values across 260,000 rows say it
cannot be one.

**Why the policy is not in the prompt.** Thresholds in the sidebar mean a privacy
officer can argue about where the line goes for an hour without spending a token.
The judgments are fixed; the policy over them is cheap and reversible.

## Limits

- **It judges a profile, not your data.** If your profiler samples badly — three
  nulls as "examples", or a stale distinct count — the judgment inherits that.
  Garbage profile, garbage register.
- **Free-text columns are the weak spot.** `support.note_body` can contain
  anything, and a per-column verdict cannot express "2% of these rows contain a
  phone number". It gets flagged as high-risk and that is the best a column-level
  scan can do. Row-level scanning of free text is a different app.
- **It does not know your joins.** The quasi-identifier Noul is scoped to "other
  columns in the same table" because that is what the profile shows. A column
  that is harmless alone and devastating after a join to a dimension table will
  read as harmless.
- **`jev-1.13` cannot count, sum, or compare dates.** Nothing on screen is a
  number the model produced; every statistic is pandas. If you extend this, keep
  it that way.
- **It is not legal advice.** "Special category" is a legal test with jurisdiction
  in it, and this is a prior, not a determination. It puts a lawyer's attention on
  40 columns instead of 900 — that is the claim, and it is the whole claim.
- **Confidence is not correctness.** A high-confidence `not_personal_data` on a
  column you know is sensitive means the name and profile genuinely look innocent,
  not that the model checked and cleared it.
