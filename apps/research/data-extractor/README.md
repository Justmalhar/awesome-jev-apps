# 🔬 Data Extractor

Population, intervention, comparator and outcomes off a methods section — as
verbatim spans, with provenance.

## The problem

Every systematic review reaches the same bottleneck twice. First screening, then
**data extraction**: two people independently fill the same form for every
included paper, field by field, and a third adjudicates the disagreements. The
form is boring and the work is exacting, because the extracted value is what
ends up in the meta-analysis.

Generative extraction makes this worse, not better. A model asked to "extract
the population" returns *"older adults with diabetes"* when the paper said
*"adults aged 60 to 79 years with a clinical diagnosis of type 2 diabetes of at
least two years' standing"*. The paraphrase is fluent, plausible, and no longer
the paper's claim. You cannot audit it against the PDF because the string does
not appear in the PDF.

## Why this needs Jev

**Jev cannot generate text — it selects.** That is normally described as a
limitation. Here it is the entire safety property.

Python splits the methods section into candidate sentences. Jev is handed those
candidates as the options of a Choice and picks one. Whatever comes back is a
span that exists in the source, byte for byte, with an index that points at it.
Paraphrase is not a risk that has been mitigated; it is structurally impossible.

The second property in use is **independent thresholding**. Each field asks two
questions that a single "extract the comparator" prompt would fuse:

```python
questions[f"has_{index}_{field}"]  = noul(...)    # is this field described at all?
questions[f"pick_{index}_{field}"] = choice(..., options | {NO_MATCH: "..."})
```

*"This trial had no comparator"* and *"this trial had a comparator but the
selector could not find the sentence"* are different facts. One is data; the
other is a human's job. Fused into one question they are indistinguishable, and
a single-arm feasibility study silently acquires a control group.

## How it works

Everything for every paper goes in **one** `ask()`: `papers × 4 fields × 2
questions`. Then the policy runs in Python:

| Condition | Result |
|---|---|
| P(field described) ≤ `report_min` | recorded as *not reported* |
| Selector returned the no-match option | **needs a human** |
| Selection confidence < `confidence_min` | **needs a human** |
| Otherwise | the verbatim span is recorded |

Both thresholds are sliders, so retuning costs no inference.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five synthetic methods sections are bundled, chosen for the cases that break
naive extraction: a clean parallel-group RCT, a **single-arm** feasibility study
with no comparator, a placebo-controlled trial, a retrospective cohort with
three compared groups and no intervention administered, and a protocol fragment
that reports almost nothing.

## Design notes

- **Choice over parsed spans, not a Noul per sentence.** Exactly one sentence
  states the comparator most directly, so the fields are mutually exclusive over
  candidates — that is a Choice. The *fields themselves* are independent and
  never share a question.
- **`max_candidates` is a Python knob.** A 60-sentence methods section produces
  a 60-option Choice, which dilutes the distribution. Cap it and split rather
  than asking a harder question.
- **Confidence is used as a triage signal only.** It measures how concentrated
  the distribution is across candidates, not whether the selected sentence is
  the right answer. Two near-identical sentences legitimately produce low
  confidence and a correct pick. That is why low confidence routes to a human
  instead of dropping the value.

## This assists a human researcher

It is an extraction *aid*, not an extraction *result*. Published review
standards require human-verified extraction, usually in duplicate, and nothing
here changes that. The right use is as the first of two extractors, with a human
adjudicating everything in the flagged pile.

As with screening, **recall matters more than precision**: a field wrongly sent
to a human costs a minute, and a field wrongly recorded as *not reported*
corrupts the synthesis. The defaults are deliberately biased toward flagging.

## Limits

- **Sentence splitting is a regex.** Abbreviations ("e.g.", "vs.") and numbered
  lists can split badly. Inspect the candidate count before trusting a run.
- **A field spanning several sentences loses the remainder.** The comparator
  described across three sentences returns the single most direct one. Widen the
  span by hand from the provenance panel.
- **`jev-1.13` reads literally.** If the comparator is only implied by the
  phrase "the other group", the no-match option is the honest answer and you
  will see it flagged.
- **No arithmetic.** Sample sizes, durations and dates are *never* asked of the
  model. If you need them as numbers, parse the selected span in Python.
