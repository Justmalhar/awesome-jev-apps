# 🧾 form-validator

Field validation a regex cannot express — "that isn't a job title", "that city
isn't in that country" — checked while the user is still in the form.

```bash
uv run streamlit run app.py
```

```
This validation pass · 149 ms

city   — That city does not appear to be in the country you selected.
         `Lyon`
bio    — This looks like placeholder text rather than a real bio.
         `asdf asdf placeholder text here will fill in later`

rule                  field       P(yes)  marked
name_is_a_person      full_name   0.981   False
title_is_a_real_job   job_title   0.243   False
city_in_country       city        0.031   True
bio_is_placeholder    bio         0.942   True
```

## The problem

Form validation stops at syntax. `type="email"` proves there is an `@`;
nothing proves `dana@northwind-logistics.com` belongs to the Dana who filled in
the name field. A country dropdown paired with a free-text city field will
happily accept Lyon, Italy forever. "Chief Vibes Officer" passes every
`minLength` check ever written.

So the semantic checks get pushed downstream: a server-side blocklist that
grows one special case per incident, a nightly data-quality job, or a human in
an onboarding review queue. All three find the problem after the user has left
the page, which is the one moment they could have fixed it themselves.

## Why this needs Jev

Inline validation is a latency problem before it is an accuracy problem. If the
check takes a second, it lands after the user has tabbed to the next field, and
an error that appears on a field you already left is worse than no error. At
roughly 150 ms the judgment arrives while the cursor is still there.

Second: the whole form is **one** request. Six independent rules across seven
fields, answered in parallel against a state ingested once — not six calls, and
not one prompt asked to return a JSON blob you then have to trust.

## How it works

```python
"city_in_country": noul(
    {"task": "Is the stated city located in the stated country?",
     "city": "`form.city`", "country": "`form.country`"},
    true="The city is in that country, including under a local or alternative spelling",
    false="The city is in a different country, or is not a city at all",
),
"bio_is_placeholder": noul(
    {"task": "Is this text placeholder or filler rather than a real biography?",
     "value": "`form.bio`"},
    true="Lorem ipsum, keyboard mashing, a note to self, or a promise to write it later",
    false="Actual sentences describing this person's work, however short",
),
```

Six rules, six Nouls — several can fail at once, so they are not a Choice and
not levels of a rubric. `RULES` maps each question id to the field it annotates
and whether a *low* or a *high* probability is the failure, and `field_errors()`
turns probabilities into marks. That table is the whole policy layer; moving the
slider re-marks the form with no new inference.

**Per-call state is the form itself** — seven short strings, a few hundred
tokens. Nothing else: no schema, no prior submissions, no reference list of
world cities. Keeping the state this small is what keeps the pass inside a
field-blur, and it is also why the same design would *not* survive being handed
your whole user record "just in case".

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx --with streamlit streamlit run app.py
```

The bundled profile fails three ways on purpose: a joke title, a French city in
Italy, and a bio someone meant to come back to.

## Design notes

- **Nouls, not a Choice.** "Which field is wrong" is the wrong question — several
  are. Independent properties get independent thresholds.
- **One threshold, two directions.** `city_in_country` complains when P is *low*;
  `bio_is_placeholder` complains when P is *high*. The `direction` column in
  `RULES` handles that, rather than inverting the question wording, which would
  make the criteria harder to read than the rule it encodes.
- **Messages are written in Python.** Jev returns probabilities; the user-facing
  sentence is a constant in `RULES`. The model never generates text here, because
  it cannot.
- **A missing answer marks nothing.** If a question id is absent, `field_errors`
  skips it rather than defaulting to "failed" — a validator that blocks a form
  because of a partial response is worse than one that misses.

## Limits

- It knows geography from training, not from a gazetteer. Small towns, recent
  renamings, and disputed borders are exactly where it will be least sure —
  route a low-confidence city through the existing dropdown rather than blocking.
- `email_matches_name` is a heuristic and will flag legitimate cases: married
  names, transliterations, a shared team mailbox someone genuinely uses. It is
  wired as a warning here, and should stay one.
- `jev-1.13` reads literally. A rare-but-real job title ("Chief Remembrancer")
  scores like a joke one; the fix is a boundary case in the criteria, not a
  higher threshold.
- No counting, arithmetic, or date maths is asked of the model here — so "is this
  date of birth consistent with that graduation year" stays a Python check.
- Latency is measured at the client and includes your network path. Fire this on
  blur rather than on every character if your users are on mobile networks; the
  measured numbers in the footer are how you decide that.
