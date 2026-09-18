# 🗣️ Interview Coder

Apply a thematic codebook to interview transcripts, one independent Noul per
code.

## The problem

Qualitative analysis has a well-known cost curve. Forty interviews, an hour
each, a codebook of a dozen themes. Two researchers code every transcript
independently, Cohen's kappa is computed, disagreements are talked through, and
the codebook gets revised — which means re-coding everything already coded.
Months of a funded post go into this, and none of it is the interesting part.

The tooling on offer (NVivo, ATLAS.ti, Dedoose) is excellent at *storing* codes
and does not help you *apply* them. The reading is still all yours.

## Why this needs Jev

**Codes co-occur, and that is the whole point.** A single sentence —

> "The kit was free but the batteries aren't, and it wants four of them every
> few weeks. It sounds trivial but on a pension that's not nothing, and nobody
> mentioned it when they signed me up."

— is `cost_concern` *and* `data_transparency`-adjacent *and* arguably
`onboarding_burden`. Those are not three candidate answers to one question.

A Choice over codes would force the model to nominate a winner and silently
destroy the co-occurrence. A rubric would imply the codes are ordered levels of
one thing, which they are not. The correct primitive is **one Noul per code**:

```python
questions[f"c_{index}_{code['code']}"] = noul(
    {"task": "Does this passage from an interview express the theme below?",
     "theme": code["name"],
     "definition": code["definition"],
     "passage": f"`passages[{index}].text`"},
    true=code["applies_when"],
    false=code["does_not_apply_when"],
)
```

The second property being exploited is **per-code thresholding**. Codes differ
enormously in how crisp their boundaries are. `cost_concern` is nearly
mechanical; `trust_in_clinician` is a judgement call. Independent Nouls let you
raise the review band for the fuzzy code alone, and because the policy lives in
Python, retuning re-runs **no inference at all** — you re-slice probabilities
you already paid for.

## How it works

All `passages × codes` judgments go out in as few requests as the context
window allows (`plan_batches` packs greedily against
`provider.context_tokens`; it is batching for size, never a dependency chain).
Then, in Python:

| P(code applies) | Result |
|---|---|
| ≥ `apply_at` | the code is applied |
| `review_at` … `apply_at` | **surfaced to the human** as borderline |
| < `review_at` | not applied |

Passages that match nothing at all are counted and reported separately — in
grounded work an uncoded passage is evidence the *codebook* is incomplete, which
is a finding, not an error.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A synthetic transcript (a patient interview about a remote monitoring service)
and a seven-code codebook are bundled. The transcript is written to contain
genuine multi-code passages, one passage that fits no code, and two codes that
overlap in a way a forced Choice would have to resolve wrongly.

## Design notes

- **Interviewer turns are dropped in Python.** Codes describe what participants
  said; coding the prompts inflates frequencies and costs tokens.
- **`applies_when` / `does_not_apply_when` carry the boundary cases.** `jev-1.13`
  reads literally, so the place to write "mentions of cost borne by the health
  service do not count" is the `false` criterion, not the definition.
- **No Score anywhere.** "How strongly does this passage express the theme" is
  not a question the analysis needs, and a Noul is not an intensity dial —
  P=0.5 means genuinely split, not "moderately present".
- **Speaker turns, not fixed-size windows.** A turn is the natural unit of an
  interview and keeps the passage quotable in the write-up.

## This assists a human researcher

It is a first pass, not a coder. Published qualitative work reports who coded
what and how agreement was established, and an automated pass does not satisfy
that. Use it as one of two coders, or as a pre-pass that a human confirms — the
saving is real either way because confirming a code is far faster than finding
one.

**Recall matters more than precision here**, exactly as in screening. A theme
the model misses may never be found again; a theme it over-applies is rejected
in seconds. The default band is set wide for that reason, and the borderline
column exists so near-misses surface instead of vanishing.

## Limits

- **No inter-rater reliability.** Kappa against a human coder is the number that
  matters and this does not compute it for you. Double-code a pilot transcript
  and calibrate the sliders against it before trusting a full run.
- **Codebook revision still means re-coding.** Changing a definition changes the
  question, so those judgments must be re-run. Changing a *threshold* does not.
- **Long turns dilute.** A five-minute monologue carrying six themes returns
  softer probabilities than a tight paragraph. Split long turns if you see this.
- **`jev-1.13` degrades on indirection.** Irony, and themes carried by what a
  participant pointedly *doesn't* say, are not reliably caught. Those are the
  passages a human was always going to have to read.
- **No counting.** Code frequencies in the summary table are tallied in Python
  from the probabilities. The model is never asked how often anything occurs.
