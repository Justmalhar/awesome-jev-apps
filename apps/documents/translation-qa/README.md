# 🌐 Translation QA

Flag the translations that changed **meaning**, not the ones that changed wording.

## The problem

A localisation run comes back with four thousand segments. The reviewer has
budget for two hundred. Which two hundred?

Every automatic tool available answers the wrong question. Edit distance and
BLEU measure surface overlap, so *"pan comido"* for *"a piece of cake"* scores
near zero and gets escalated, while *"puede activar"* for *"must enable"* scores
high and sails through — a one-word change that turned a mandatory security
control into an optional one. Back-translation has the same bias with extra
steps. So the reviewer ends up hand-checking the fluent segments and shipping
the dangerous ones, which is precisely backwards.

The defects that actually matter are semantic: a dropped negation, an inverted
obligation, a clause that vanished, a guarantee the source never made, a
number that drifted. None of them correlate with string distance.

## Why this needs Jev

Each segment is **two states compared** — what the source asserts, and what a
reader of the target would take away. That is a judgment about equivalence of
meaning, and it is the one thing string metrics structurally cannot do.

The structural property being exploited is **independent thresholding of
independent hazards**. An LLM asked "is this translation OK?" returns a verdict
you cannot tune, audit, or split. Here the hazards are four separate Nouls:

```
polarity_flip  ·  omission  ·  addition  ·  meaning_changed
```

They come apart in practice. Segment `s05` omits an exception clause without
inverting anything; `s04` inverts an obligation while omitting nothing; `s12`
swaps which party indemnifies which, omitting and adding nothing at all. Collapse
them into one rubric and you lose the ability to say *"we are shipping legal
copy, so set the polarity threshold to 0.4 and leave the rest at 0.7"* — which
is the entire reason a localisation QA lead would use this.

The fifth Noul is the one that makes the app usable. `stylistic_only` asks
whether the difference is idiom or register with the meaning intact, and Python
uses it to **suppress** rather than to flag — so good non-literal translation
stops being punished.

## How it works

Six judgments per segment, all in one request:

```python
questions[f"polarity_flip_{index}"] = noul(
    {"task": "Has a negation, permission, or obligation been inverted between the "
             "source text and the translated text?",
     "note": "A prohibition rendered as an allowance, an allowance rendered as a "
             "requirement, a requirement softened to an option, or a negation dropped.",
     "segment": pair},
    true="What the source forbids the translation permits, or what the source requires "
         "the translation merely allows, or the reverse",
    false="Negation, permission, and obligation carry across unchanged")
```

Twelve segments × six judgments = 72 questions in **one** round trip.

The policy is Python, and the ordering in it is deliberate:

```python
if stylistic >= thresholds["stylistic_only"] and probabilities["meaning_changed"] < thresholds["meaning_changed"]:
    return "pass", ...
fired = [h for h in HAZARDS if probabilities[h] >= thresholds[h]]
if fired:      return "defect", ...
if severity >= severity_flag:  return "review", ...
return "pass", ...
```

The suppression is conditional. A confident *"it's just idiom"* cannot override
a confident *"the meaning moved"* — `test_app.py` asserts that boundary, because
it is the one place a lazy guard would ship real defects.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Twelve EN→ES segments are bundled and pre-loaded. Six carry a seeded defect:

| Segment | Defect | Should fire |
|---|---|---|
| `s02` | negation dropped — "do not share" → "compartimos" | polarity, meaning |
| `s03` | period altered in prose — thirty → sesenta | meaning |
| `s04` | obligation weakened — "must enable" → "pueden activar" | polarity, meaning |
| `s05` | the mainland-China exception clause is gone | omission, meaning |
| `s06` | a one-hour response guarantee the source never made | addition |
| `s12` | the indemnifying party and the indemnified party are swapped | meaning |

The other six are correct, and three of them (`s08` *pan comido*, `s09`, `s10`)
are deliberately non-literal. Those are the interesting rows: watch
`stylistic_only` carry them past a hazard set that a BLEU score would flunk.

## Design notes

**Why four hazards and not one.** Independent properties that can be true at
once are separate Nouls, never levels on a rubric. The give-away is `s05` and
`s06`: an omission and an addition are opposite failures and a single scale
cannot hold both ends.

**Why severity is a Score.** "How bad is this" is a degree; a Noul at 0.5 means
*yes and no are equally likely*, not *medium*. Each level names a concrete
situation — "a reader would act differently: a duty, permission, deadline,
quantity, or named party does not match the source" — so a linguist disagreeing
with a rating can point at which level the segment actually matches.

**Why the number in `s03` is not a numeric question.** The model is never asked
whether thirty equals sixty. It is asked whether a reader would come away with a
different understanding, with *"a factual detail that has been altered — a named
party, a quantity, a period, a place"* spelled out in the note. Recognition, not
arithmetic.

**Why three verdicts.** `defect` / `review` / `pass`. A two-way forced split is
what makes automated QA untrustworthy; anything severe that no single hazard
caught lands in the human pile.

## Limits

- **Segments must already be aligned.** This reviews source/target pairs. It
  does not do the alignment, and a misaligned pair reads as a total rewrite.
- **Terminology and glossary compliance are out of scope.** "Is *account*
  consistently *cuenta*?" is a corpus-wide consistency question and needs a
  termbase, not a per-segment judgment.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** It is never
  asked to. It will tell you a factual detail moved; it will not tell you the
  translation says sixty where the source said thirty. Extract figures in Python
  if you need that assertion.
- **It reads literally.** Context outside the segment does not exist. A pronoun
  whose referent is three segments back, or a term of art that only means
  something in your product, will be judged on the segment alone.
- **It degrades on large noisy state.** Everything goes in one request, so a
  four-thousand-segment run must be chunked; the app warns when the estimate
  crosses `provider.context_tokens` rather than silently truncating.
- **Quality varies by language pair.** The bundled sample is EN→ES. Treat
  performance on a low-resource pair as unknown until you check it against a
  labelled set of your own, and tune the sliders there rather than trusting the
  defaults.
