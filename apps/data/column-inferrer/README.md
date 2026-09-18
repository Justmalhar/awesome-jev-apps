# 🔍 Column Inferrer

Point it at a table nobody documented. Get back a data dictionary: what each
column semantically *is*, which columns are opaque machine keys, and — the
useful one — which column names are lying about their contents.

## The problem

You inherit a 200-column warehouse table. The columns are called `f_12`, `val`,
`dt2`, `cd`, `x9`, `h`. The data dictionary was a Confluence page written by
someone who left in 2019, and the page is gone.

Today you fix this by opening a notebook, `SELECT * LIMIT 100`, and squinting at
each column in turn. It takes an afternoon per table, it is the least
intellectually rewarding work in data engineering, and the moment anyone ships a
new table you do it again. Worse, it is exactly the work that gets skipped, so
the lineage doc says `amount: transaction amount` for eighteen months before
someone notices `amount` has held a status code the whole time.

## Why this needs Jev

**Because the model never sees the data.**

A 10,000-row column is a wall of tokens that says almost nothing a well-chosen
profile would not. So pandas profiles each column first and only the profile
goes over the wire:

```python
{
  "column_name": "email",
  "pandas_dtype": "object",
  "empty_or_null": "0% of rows",
  "distinct_values": "30 distinct out of 30 filled",
  "uniqueness": "every filled value is distinct",
  "value_length": "15-15 characters",
  "parses_as_number": "0% of filled values",
  "parses_as_date": "0% of filled values",
  "most_repeated_values": "no value occurs more than once",
  "example_values": ["+1-866-249-2186", "+1-845-842-2013", ...]
}
```

Every number in there was computed by pandas. **The model is never asked for a
statistic** — not the distinct count, not the null rate, not the length range.
`jev-1.13` cannot count and cannot do arithmetic; those are documented failure
modes, not tuning problems. It is asked one thing it is actually good at: *given
this shape and these examples, what kind of thing is this?*

The filtering is not just a cost trick, though it is that too — a 200-column
table at eight examples per column is one request, not two hundred. It is also
the reason the answers are better. A profile is signal. Ten thousand rows are
the same signal repeated ten thousand times, buried in a context window.

The structural property being exploited is **one request, many independent
questions**. Four questions per column, all columns in one `ask()`, all answered
in parallel against a state ingested once. Loop this instead and you pay for the
same ingestion per column and wait for it serially.

## How it works

One Choice for the semantic type, over a 15-type vocabulary plus a real escape
hatch:

```python
questions[f"type_{index}"] = choice(
    {"task": "What kind of real-world thing do the values in this column hold?",
     "column": reference,
     "note": "Decide from the observed values and their shape. The column name is a "
             "hint and is often wrong or abbreviated past the point of meaning."},
    SEMANTIC_TYPES,   # ... "unknown": "The values do not determine a semantic type"
)
```

Then three **separate Nouls**, because these properties are independent and can
all be true of the same column at once:

- `opaque_identifier` — a hash, UUID or surrogate key, meaningless to a person
- `name_is_misleading` — the name promises one thing, the values are another
- `placeholder_data` — fixtures and stubs rather than production records

These are not levels on a rubric and not options in a Choice. A column can be an
opaque machine key *and* be misnamed *and* be full of test data simultaneously.
Collapse them into one Choice and you force the model to pick which one to tell
you about; collapse them into a Score and you can no longer threshold them
separately. Each Noul returns its own probability with its own slider.

`name_is_misleading` is the one that earns its keep in practice. The bundled
sample has a column called `email` holding phone numbers and a column called
`amount` holding status codes — both real patterns, both invisible to a schema
crawler, both quietly wrong in every downstream consumer that trusted the name.

Batching is sized against the provider, never a constant:

```python
def batch_profiles(profiles, context_tokens, reserve=0.55):
    budget = max(1, int(context_tokens * reserve))
```

TypeSafe serves 64k for this model and OpenRouter serves 32k. Hardcoding either
breaks the other.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A 25-column x 30-row sample table ships with the app, so it runs cold with no
input. Your own CSV needs nothing but a header row.

## Design notes

**Why a Choice for the type and not Nouls.** Semantic types are mutually
exclusive by construction — a column is not both a postal code and a latitude.
That is exactly what a Choice is for, and it gives you a confidence number: the
concentration of the distribution. A flat distribution across
`enum_category` / `boolean_flag` / `unknown` is the app telling you it was
guessing, and that column goes to the review queue instead of the dictionary.

**Why `unknown` has a description.** A Choice must return something. Without an
escape hatch, a column of `N/A` in every row gets filed as `enum_category` at
plausible confidence, and you would never know. `"The values do not determine a
semantic type: too uniform, too sparse, or too ambiguous to call"` is a real
option a model can land on, which `"other"` on its own is not.

**Why the type descriptions carry exclusions.** `"postal_code": "A postal or ZIP
code. Not a full street address, not a city."` The trailing exclusion is doing
work. `jev-1.13` reads literally, and a bare label leaves the boundary with the
neighbouring type undefined. If you would explain "well, I meant…" to a
colleague, that explanation belongs in the criteria.

**Why both thresholds are sliders.** The acceptance gate and the flag gate live
in Python, over probabilities already in the results table. Move either one and
every number on screen re-derives with no new inference. Nothing about the
policy is baked into an instruction.

## Limits

- **It profiles what you give it.** Load 1,000 rows of a 40M-row table and the
  profile describes those 1,000 rows. A column that is 99.9% null and holds
  something interesting in the tail will profile as constant and be called
  `unknown`. Sample deliberately if the tail matters.
- **`primary_identifier` vs `foreign_key_reference` is guessed from uniqueness
  within the sample.** A foreign key that happens not to repeat in your 30 rows
  looks exactly like a primary key. This is a real, systematic confusion, not
  bad luck — check it against the actual constraints if it matters.
- **No cross-column reasoning.** Each column is judged alone. It will label
  `lat_v` and `lng_v` as `geo_coordinate` individually but has no notion that
  they are a pair, and it cannot tell you that `ref_7` joins to the accounts
  table.
- **The model cannot count, sum, average, or compare dates.** Everything
  numeric on screen was computed by pandas. If you extend this app, keep that
  line where it is — asking `jev-1.13` for a statistic returns a confident
  number that is wrong.
- **`name_is_misleading` is conservative on cryptic names.** `f_12` promises
  nothing, so it cannot be misleading. The flag fires on names that make a claim
  — `email`, `amount`, `created_date` — which is the useful case anyway.
- **Confidence is distribution concentration, not correctness.** A confidently
  wrong answer is possible and the gate will not catch it. The gate catches
  *uncertainty*, which is a different thing. Spot-check the accepted rows before
  you publish the dictionary.
- **Your data leaves the machine.** Example values are real values from real
  columns. Read your provider's data policy before pointing this at a table with
  anything sensitive in it, and consider dropping the example count to 3 — the
  statistics alone still carry most of the signal.
