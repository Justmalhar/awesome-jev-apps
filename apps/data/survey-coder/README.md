# 📋 Survey Coder

Apply a fixed codebook to thousands of free-text survey answers. Several codes
per answer, a human queue for the borderline ones, and a list of the answers your
codebook does not cover.

## The problem

A cancellation survey returns 3,140 open-ends to "What is the main reason you
cancelled your subscription?". A coder at the research agency reads all of them
and ticks codes from a codebook agreed with the client before fieldwork started.
It takes days. It has to be consistent on day three at 5pm and on day one at 9am.
And the deliverable is not the codes — it is the frequency table, the
co-occurrence, and the honest note that 9% of answers did not fit anything.

The awkward part, and the reason naive automation of this fails:

> "Honestly it just got too expensive. £15.99 a month for something I open maybe
> twice a month isn't worth it."

That is `price` **and** `low_usage`. Both. A coder ticks two boxes and moves on.

> "The app crashed every single time I tried to download an episode for my
> commute, and I emailed support three times and never heard back."

`bugs` **and** `support`, and arguably `missing_feature`.

Multi-code answers are not an edge case in survey coding. They are most of the
interesting ones, and the co-occurrence between codes is frequently the finding
the client is paying for.

## Why this needs Jev

**Because these are ten independent questions, and the primitive lets them stay
that way.** Ten Nouls per answer, each returning its own calibrated probability,
each thresholded separately in Python.

Collapsing the codebook into a `choice()` over ten options would be wrong, and
worth being explicit about:

1. **A Choice returns exactly one winner.** The answer above would come back
   `price`, and `low_usage` would be silently deleted. Every co-occurrence in the
   study disappears with it.
2. **A Choice's probabilities are a distribution — they compete.** `price 0.55 /
   low_usage 0.40` does not mean "both are quite likely"; it means the mass had
   to be split between them. Two Nouls can legitimately both return 0.9, because
   they are not in the same normalisation.
3. **You lose per-code thresholding.** Real codebooks are not uniformly hard.
   `billing` is nearly always explicit and can be cut at 0.5; `usability` shades
   into `missing_feature` and a careful researcher wants it at 0.7. One
   distribution gives you one knob.

A Score rubric is wrong for the same structural reason plus one more: the ten
codes are not ordered. There is no sense in which `support` is "more" than
`bugs`, and a rubric asserts exactly that.

This is the general rule stated in the spec — *independent properties that can be
true at once are separate Nouls* — and survey coding is the case where the cost
of getting it wrong shows up directly in the client deliverable.

## How it works

**One Noul per (answer, code), N answers x M codes in one request:**

```python
noul(
    {"task": "Does this survey answer give the reason described below?",
     "survey_question": "What is the main reason you cancelled your subscription?",
     "answer": f"`responses[{index}].text`",
     "reason": {"label": code["label"], "means": code["description"]}},
    true=f"The answer gives this reason, stated outright or clearly implied: {code['description']}",
    false="The answer gives some other reason, or is blank, unreadable, or content-free "
          "('n/a', 'idk'). Several reasons may apply to one answer -- judge only this one, "
          "and do not withhold it because another reason also fits.",
)
```

That last clause in `false` is load-bearing. Without it the model reads "the main
reason" in the survey question and behaves like a Choice anyway, suppressing the
second code.

**Batch size comes from the provider, not from a constant.** Ten Nouls of
criteria is ~2,165 tokens per answer — the questions dominate, the answer text is
noise by comparison. `batch_responses()` measures the real per-answer payload and
packs against `provider.context_tokens`:

```python
per_answer = question_tokens_per_answer(codes)   # measured, not guessed
budget = int(context_tokens * headroom)
```

The bundled 40 answers go out in **4 requests on TypeSafe's 64k** and **8 on
OpenRouter's 32k**. Nothing is hardcoded; the request count is printed before you
press the button and again in the footer.

**Thresholds are Python, and re-thresholding runs no inference:**

```python
def decide(probability, apply_at, band):
    if probability >= apply_at + band: return "applied"
    if probability <= apply_at - band: return "not_applied"
    return "review"
```

The sidebar has the apply threshold (default 0.50) and the uncertain band
(default ±0.10). A code inside the band is neither applied nor dropped — the
answer goes to a human coder. Drag either slider and the frequency table,
co-occurrence matrix, uncoded list and queue all re-derive from probabilities
already in memory. **Not one new request.** The downloadable CSV carries the raw
probability for every (answer, code) pair, so the same is true offline, months
later, when the client asks what happens at 0.6.

**Everything numeric is pandas.** The frequency table, the percentages, the
co-occurrence matrix (`applied.T.dot(applied)`), the multi-code count and the
uncoded list are all computed in Python from the applied flags. The model is
never shown a total and never asked how many of anything.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY
uv run streamlit run app.py
```

Runs cold with no input: `sample_responses.csv` (40 real-shaped cancellation
open-ends) and `sample_codebook.json` (10 codes) are bundled. Your own data is a
CSV with a `response` column (plus optional `response_id`) and a JSON codebook of
`{id, label, description}` objects.

## Design notes

**Why the codebook descriptions are long.** `jev-1.13` reads literally. `"usability"`
as a bare label leaves the border with `missing_feature` undefined, and answers
about a confusing interface scatter across both. So each code says what it covers
*and what it does not*:

```json
{"id": "low_usage",
 "label": "Not using it enough",
 "description": "They rarely opened it, forgot it existed, or their life changed so they no longer had time for it. About frequency of use, not quality."}
```

If you would explain "no, I meant…" to a junior coder, that sentence belongs in
the description.

**Why "uncoded" is a headline and not a footnote.** The sample deliberately
contains answers nothing in the codebook covers — someone who moved abroad where
the service is not sold, someone whose employer now provides it, and the password-
sharing crackdown. A coding system that quietly files those under the nearest
code is worse than useless, because it manufactures a finding. Here they surface
as uncoded, next to the genuine junk ("n/a", "idk", "-"), and reading that list
is how the codebook gets a code 11 before the next wave.

**Why no `none` code.** These are Nouls, not a Choice, so there is nothing to
escape from: an answer that matches no code simply gets ten low probabilities.
"Uncoded" is derived in Python, which is also why it responds to the slider.

## Limits

- **Consistency is not accuracy.** This will code identically at answer 1 and
  answer 3,000, which a human will not. It can still be consistently wrong about
  a code whose description is ambiguous. Hand-code 100 answers, compare, fix the
  descriptions — that calibration step is not optional and this app does not do it
  for you.
- **No inter-coder reliability figure.** There is no Krippendorff's alpha here
  because there is one coder. If your methodology requires a reliability
  statistic, you still need a human sample to compute it against.
- **Cost scales with codes, not answers.** Doubling the codebook roughly doubles
  the token spend, because the criteria are re-sent per answer. A 40-code
  codebook on 3,000 answers is a real bill — estimate it on 100 answers first.
- **Sentiment, intensity and "main" reason are out of scope.** Every code is a
  flat yes/no. If the study needs "how strongly", that is a `score()` with
  concrete levels, and a separate question.
- **`jev-1.13` cannot count or do arithmetic.** It is never asked to. If you add
  a feature here, keep every frequency, percentage and cross-tab in pandas.
- **Long answers are not chunked.** A 2,000-word open-end is sent whole; a very
  long one can push a batch past the window despite the packing estimate, which
  is a 4-chars-per-token approximation and not a tokenizer.
- **Verbatim answers leave your machine.** Open-ends contain names, employers and
  complaints about identifiable staff. Check your consent wording and the
  provider's retention policy before pointing this at real fieldwork.
