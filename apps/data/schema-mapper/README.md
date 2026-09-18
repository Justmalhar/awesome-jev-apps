# 🗺️ Schema Mapper

Point it at two schemas. Get a one-to-one column mapping, split into the ones
you can apply without looking and the ones a data engineer has to confirm.

## The problem

Someone is migrating a CRM export into a new warehouse schema. The export has 40
columns named by whoever set the system up in 2011 — `cust_nm`, `dt_created`,
`st_prov`, `opt_in_marketing`. The target has 35 fields named by the data team
last month — `customer_full_name`, `created_at`, `state_or_province`,
`marketing_consent`.

That is 1,400 combinations, and the work is not typing the mapping file. The work
is the handful of pairs that look right and are not:

- `billing_email` holds `ap@acmeholdings.example`. `contact_email` holds
  `dana.whitfield@acmeholdings.example`. One is `invoice_email`, the other is
  `primary_contact_email`, and swapping them sends every invoice to a person who
  does not pay them.
- `phone` is the switchboard, `mobile_phone` is a person. Both are phone numbers.
- `cust_nm` is the *company*, not the contact, even though `contact_first_name`
  is lexically closer to `cust_fname`.
- `nps_score`, `fax_no` and `duns_num` have no target at all. `gdpr_erasure_requested_at`
  has no source. Both facts need to end up on someone's desk, not in a silent default.

Today this is a spreadsheet, two afternoons, and one column that gets found six
weeks later in a dashboard.

## Why this needs Jev

Three structural properties, none of which is "an LLM but cheaper":

**One-question-per-pair, batched.** 342 surviving pairs go out in three requests
on a 64k provider. The model answers them in parallel against a state it ingested
once. A chat model would need 342 round trips or one enormous prompt whose output
you then have to parse.

**The score levels are the actions.** The rubric is not "how similar are these
fields?" with a cutoff to fit afterwards. It is the three things you can do:

```python
LEVELS = [
    "These two fields hold unrelated data. Copying one into the other would put wrong values in the warehouse column.",
    "Plausibly the same field, but the names, the declared types or the sample values disagree. A data engineer should confirm before this mapping ships.",
    "The same field under a different name. The sample values are visibly the same kind of thing, so this mapping is safe to apply without review.",
]
```

A score of 1.2 is not "medium similarity". It is "leaning towards: someone should
confirm this", which is a queue you can staff.

**A calibrated number per pair, so Python can do the assignment.** The scores are
comparable across pairs, which is what makes greedy one-to-one assignment
meaningful. The model is never asked to rank, to pick a best target, or to
produce a mapping file — it judges pairs, and code turns judgments into a mapping.

## How it works

**1. Prefilter in Python.** 1,400 pairs is affordable, but 400 columns against
350 fields is 140,000 and is not. The filter is two cheap tests:

```python
def normalize(name: str) -> str:
    """`dt_created` -> `created date`. Tokens sorted so word order stops mattering."""
    tokens = []
    for part in re.findall(r"[a-z]+|\d+", name.lower()):
        tokens.extend(ABBREV.get(part, part).split())
    return " ".join(sorted(tokens))
```

plus a coarse type-compatibility test where *text is compatible with everything*,
because legacy CRMs keep dates and booleans in VARCHAR columns and this filter
must not be the thing that loses a real mapping.

On the bundled sample at the default 0.35 cutoff: **1,400 pairs → 342**, 75.6%
removed, and all 34 correct mappings survive. The app prints that reduction.

**2. One `score()` per surviving pair, batched against `provider.context_tokens`.**

```python
score({"task": "Should the source column be loaded into the target field during a migration?",
       "source_column": f"`pairs[{index}].source`",
       "target_field":  f"`pairs[{index}].target`",
       "note": "Judge the data, not the spelling. ..."}, LEVELS)
```

`batch_pairs()` estimates each pair's JSON at 4 chars per token and packs until
40% of the window is used. TypeSafe's 64k gives 3 requests; OpenRouter's 32k
gives 5. Nothing is hardcoded.

**3. Sample values are in the state, and they are the whole trick.**

```json
{"name": "billing_email", "type": "string", "samples": ["ap@acmeholdings.example", "billing@brightpath.example"]}
```

`billing_email` and `contact_email` are indistinguishable by name — both are
plausibly `primary_contact_email`. They are trivially distinguishable by three
values: one column is role addresses, the other is named humans. Every field in
the state carries `name`, `type` **and** `samples`, and if you strip the samples
the app gets noticeably worse at exactly the pairs you care about. If your schema
extract cannot produce sample values, profile the tables first — it is worth more
than any prompt change.

**4. Assignment in Python.**

```python
def assign(scored, floor):
    """Greedy one-to-one: best score first, each source and each target used once."""
```

Greedy rather than Hungarian on purpose: the scores are ordinal rubric levels,
not costs, so a globally optimal assignment over them is false precision.
Everything after that — auto tier, curator tier, unmapped targets, unmapped
sources, the reduction percentage, the cost — is pandas and plain Python. The
model is never asked to count anything.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY
uv run streamlit run app.py
```

Runs cold with no input: `sample_source_schema.json` (40 legacy CRM columns) and
`sample_target_schema.json` (35 warehouse fields) are bundled. Your own schemas
are JSON with a `fields` list of `{name, type, samples}` objects.

## Design notes

**Why a Score and not a Choice over target fields.** The obvious design is one
Choice per source column with 35 target options plus `none`. It fails for three
reasons: 35 options is a lot of criteria text repeated per column; a Choice gives
you one winner and no visibility into the runner-up; and it cannot express "this
target is already taken by a better source column", which is a global constraint
only code can enforce. Pairwise scores plus Python assignment keeps the constraint
where it belongs.

**Why three levels and not five.** Five levels needs five genuinely distinct,
concrete situations, and there are only three actions here: apply it, check it,
drop it. Extra levels would be a similarity dial wearing a rubric's clothes.

**Why thresholds are sliders.** `auto_at` and `review_at` live in the sidebar and
apply to already-returned scores. Moving them re-derives every table on screen
without one new request. The downloadable CSV carries the raw score for every
judged pair, including the ones that lost the assignment, so you can re-tier
offline.

## Limits

- **The prefilter is lexical, so a pure semantic rename can be lost before Jev
  ever sees it.** `lead_src` → `acquisition_source` survives only because both
  expand to share the token "source". A rename with no shared tokens at all
  (`flag_7` → `marketing_consent`) will not be offered as a candidate at any
  cutoff above 0. If you know your source has opaque names, run at cutoff 0 and
  pay for the full grid — the app will tell you what that costs.
- **The abbreviation table is hand-written and English.** It covers the ~30
  abbreviations that show up in CRM and ERP exports. `st` is guessed as *state*,
  which is wrong for a `st_addr` column.
- **One-to-one only.** Real migrations have splits (`full_name` → first + last)
  and merges (`addr_ln1` + `addr_ln2` → one text field). This app cannot express
  either; it will pick the best single pair and leave the other side unmapped.
  The unmapped-targets table is where you notice.
- **Transformations are out of scope.** "Yes, these map" says nothing about
  `ACTIVE` → `active`, `2021-03-14` → a timestamp, or NOK → USD. Score 2 means
  *the same field*, not *the same encoding*.
- **`jev-1.13` cannot count or do arithmetic.** It is never asked to. If you
  extend this app, keep every total, percentage and ranking in pandas.
- **Three sample values is a small window.** A column that is 99% NULL with three
  populated rows will look healthier than it is. Profile for null rates in your
  warehouse, not here.
- **Field names and sample values leave your machine.** Sample values are real
  data. Redact before pointing this at a production extract, and read your
  provider's retention policy first.
