# 🧭 Onboarding Gaps

Which onboarding steps your documentation *mentions* but never actually
explains — the gaps that are invisible to everyone who already works there.

## The problem

Onboarding docs are written by people who know the answers, which is precisely
why they are full of holes. "Request the database roles you need" is a
sentence, not an instruction. "Get a VPN profile issued" appears in the welcome
page as *"ask in the relevant channel and someone will sort you out"*, which
means the new joiner's first week is a series of DMs to strangers.

Nobody finds these gaps by reading the docs, because reading them requires
knowing what is missing. They get found by a new hire, who is the worst-placed
person to fix them and the least likely to complain. Then the next hire finds
them again.

The audit that would fix this — take every step on the checklist, search the
wiki, judge whether what you find is actually enough — is a couple of days of
tedious cross-referencing, and it goes stale in a quarter.

## Why this needs Jev

**It is a cross product, and cross products are what batching is for.** 14
steps × 6 documents × 2 judgments = 168 questions. Every pair is independent,
they run in parallel against a state ingested once, and the whole audit is one
request on a 64k context.

**And the shape of the question matters.** The naive version — "is this step
documented anywhere in our wiki?" — asks the model to search a large state and
aggregate over it, which is indirection plus aggregation, two documented
`jev-1.13` failure modes in one question. Asking *"does **this** document
explain **this** step"* is a small, local judgment. The aggregation (best
document per step) is a `max()` in Python, where it belongs:

```python
best, best_doc = max(scores)
```

**Two Nouls per pair, not one.** "Explains it well enough to act on" and
"refers to it at all" are separate properties, and the gap between them is the
entire product:

| explained | mentioned | state |
|---|---|---|
| high | — | covered |
| low | **high** | **mentioned but not explained** |
| low | low | missing entirely |

The middle row is the one a keyword search can never find, because a keyword
search finds exactly the documents that mention the thing.

## How it works

```python
questions[f"act_{step_index}_{doc_index}"] = noul(
    {"task": "Does this document explain the onboarding step well enough that a new "
             "joiner could complete it without asking anyone?",
     "onboarding_step": f"`steps[{step_index}].task`",
     "document_title": f"`docs[{doc_index}].title`",
     "document_body": f"`docs[{doc_index}].body`"},
    true="The document gives the concrete commands, links, names, or steps needed to do it",
    false="The document leaves out something the new joiner would have to ask a colleague for")
```

Chunking reads `provider.context_tokens` — TypeSafe serves 64k, OpenRouter 32k,
and neither number is hardcoded anywhere in the app.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 14 onboarding steps and 6 real-shaped internal documents — a wiki home
page, a service development guide, a platform runbook, an IT request page, a
people handbook and a team norms page. The docs are deliberately good in the
way real docs are good: the VPN is mentioned in three places and explained in
none of them.

## Design notes

**Why steps are the unit, not documents.** The question "is our onboarding doc
good?" has no answer. "Can a new joiner complete step 5 from what we wrote?"
has one, and it produces a task list.

**Why the `true` criterion says "without asking anyone".** That is the bar, and
stating it is what distinguishes level-2 prose ("VPN profiles are issued
per-person") from an instruction. The model reads literally, so the bar has to
be written into the criterion rather than implied by the word "explain".

**Why "which documents are pulling their weight" is in there.** It falls out of
the same judgments for free and answers a question nobody asks: which of your
six onboarding pages is actually carrying the load, and which exists to be
linked from the welcome email.

**Why the thresholds are separate.** `covered_at` and `absent_below` are two
different decisions — how much explanation counts as enough, and how little
mention counts as none — and collapsing them into one slider would hide the
"thin" pile, which is where most real docs sit.

## Limits

- **Documents in, documents out.** It audits the corpus you give it. A step
  explained perfectly in a Slack thread from 2024 reads as missing, which is
  correct but may not be what you expected.
- **Long documents degrade the judgment.** A 40-page handbook dilutes every
  pair. Split docs by section before feeding them in; the cross product gets
  bigger and the answers get better.
- **No counting, no arithmetic.** Coverage is a `max()`, the tallies are
  `groupby`. The model is never asked how many documents cover a step.
- **`P(explained)` is not a readability score.** It is the probability that the
  statement holds. A document can score high and still be badly written.
- **It finds gaps, not errors.** A confidently wrong instruction scores as
  covered. Staleness detection is a different app.
- **Your step list is the other half of the work.** If the checklist is the
  stale document, this audits the wrong thing very efficiently.
