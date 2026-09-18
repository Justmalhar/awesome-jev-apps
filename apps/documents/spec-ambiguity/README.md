# 🪤 Spec Ambiguity

Find the sentences in a specification that two engineers would read differently —
before one of them builds the wrong thing.

> *"The system retries a reasonable number of times before giving up."* →
> unquantified 0.94, undefined term 0.81, late-discovery cost 2.7 / 3

## The problem

Ambiguity in a spec is not found by reading the spec. It is found three weeks
later, in a pull request, when one engineer implemented "users and admins who
have verified their email" as *(users) and (admins who verified)* and the
reviewer read it as *(users and admins) who verified*. Both are defensible.
One of them is now a permissions bug.

Spec review today is a senior engineer reading thirty numbered sentences with
their guard up, which works for the first ten. The sentences that survive are
the ones that read *fluently* — and reading fluently is exactly what ambiguity
does. `must load quickly` does not look like a defect. It looks like a
requirement, right up until someone asks what the number is.

## Why this needs Jev

The five things worth flagging here are **independent properties**, not points
on a severity scale:

| Property | Fires on |
|---|---|
| `two_readings` | Two implementations, both defensible |
| `missing_actor` | An action with no named performer |
| `unquantified` | A quality bar with no testable threshold |
| `undefined_term` | A behaviour-driving term the spec never defines |
| `unhandled_failure` | The happy path only |

Sentence 4 of the bundled sample trips `unquantified` *and* `undefined_term`
*and* `unhandled_failure` at once. A single Choice would have to pick one and
throw the other two away. A single Score would have to pretend they are ordered,
which they are not — "no actor named" is not a milder form of "term undefined".

Separate Nouls also keep them **separately tunable**. On a backend project you
may not care about passive voice at all and care enormously about unstated
failure paths; you move one slider and nothing else in the report shifts. That
is a property of the primitive, not of the prompt — and it is the reason this is
not just "an LLM app but cheaper". A generated prose review cannot be
re-thresholded; a vector of probabilities can.

The other structural win: 32 sentences × 7 questions = **224 judgments in one
request**. They are answered in parallel against a state ingested once, so every
sentence is judged with the whole spec in view — which is what lets
`undefined_term` mean *"undefined anywhere in this document"* rather than
*"undefined in this sentence"*.

## How it works

Python splits the spec. The split is mechanical, so the model never sees it:

```python
def split_sentences(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [re.sub(r"^\d+[.)]\s*", "", line) for line in lines if re.match(r"^\d+[.)]\s", line)]
    if numbered:
        return numbered
    ...
```

Then one Noul per hazard per sentence, plus a Choice for the dominant family and
a Score for how late you would find out — all batched:

```python
for key, spec in HAZARDS.items():
    questions[f"{key}_{index}"] = noul(
        {"task": spec["task"], "sentence": f"`sentences[{index}]`", "specification": "`sentences`"},
        true=spec["true"], false=spec["false"],
    )
questions[f"family_{index}"] = choice({...}, FAMILIES)   # includes "unambiguous"
questions[f"cost_{index}"] = score({...}, COST_LEVELS)
```

The policy is a pure function, so every slider re-buckets the report with zero
further inference:

```python
def triage(probabilities, cost, family, thresholds, cost_cutoff) -> str:
    fired = flagged_hazards(probabilities, thresholds)
    if not fired:
        return "clear"
    if cost >= cost_cutoff:
        return "rewrite"
    return "clear" if family == "unambiguous" else "review"
```

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

A 32-sentence synthetic checkout spec is bundled, so it runs before you paste
anything. It contains one of each classic ambiguity family and about a dozen
genuinely unambiguous sentences as controls — if the controls light up, your
thresholds are too low.

### Sentences worth watching in the sample

| # | Sentence | Expected |
|---|---|---|
| 1 | "must load quickly on all supported devices" | `unquantified` |
| 3 | "The payment is captured once the gateway returns…" | `unhandled_failure`, `missing_actor` |
| 5 | "Users and admins who have verified their email" | `two_readings`, family `dangling_scope` |
| 6 | "in full and/or in part" | family `and_or` |
| 21 | "If the session expires mid-payment, it is handled appropriately" | all five |
| 15 | "recalculated server-side… client-supplied total is discarded" | clear |
| 20 | "expires after 30 minutes of inactivity, measured from…" | clear — the threshold is concrete |

## Design notes

**Why a Choice *and* Nouls.** The Choice earns its place as a grouping key for
the report, not as a detector. It is deliberately not allowed to flag a sentence
on its own: `triage()` returns `clear` unless a Noul fired. When the Choice
names a family whose Noul did not clear its threshold, the UI says so and tells
you to trust the Noul — a Choice must return something, and `unambiguous` is the
escape hatch that stops it nominating the least-wrong family for a clean
sentence.

**Why the Score is "cost of finding out late" and not "severity".** Severity is
an adjective and would have produced `["low", "medium", "high"]`, which is not a
rubric. "What would it take to discover this was built wrong" has four concrete
answers — nobody notices / a designer notices / code review catches it / only
production reveals it — and an engineer can place a sentence on that scale
without agreeing with you about anything else.

**Why `specification` is in the state for every question.** `undefined_term` and
`unhandled_failure` are only answerable with the whole document in view. Batching
gives that for free: state is ingested once, questions run against it in
parallel.

## Limits

- **`jev-1.13` cannot count, sum, or compare dates and versions.** Nothing here
  asks it to. "Is sentence 4 vaguer than sentence 25?" is not a question this
  app can ask; ordering comes from the Score, computed per sentence and sorted
  in Python.
- **It reads literally.** `unquantified` fires on *"the system should be
  performant"* because the sentence says so. It will not infer that your team
  has an unwritten 200 ms SLO. If your project has conventions, they belong in
  the spec, which is arguably the point.
- **`undefined_term` degrades on large specs.** The judgment needs the whole
  document in state, and the model degrades on large state full of irrelevant
  detail. Past a few hundred sentences, split by section and accept that a term
  defined in §2 will read as undefined in §9.
- **It flags ambiguity, it does not resolve it.** The model cannot generate, so
  there is no "suggested rewrite" — that is a human's job, and a rewrite you did
  not write is a rewrite you did not think about.
- **False positives on deliberate vagueness.** A spec that intentionally leaves
  a retry policy to the implementer will be flagged. Raise that one slider.
- **Sentence splitting is naive** for specs mixing numbered clauses with prose
  tables. Check the sentence list before trusting the counts.
