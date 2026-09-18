# 🎯 Lead Qualifier

Write your ICP as a paragraph. Get your inbound queue in the right order — and
re-tune the ordering for free, as often as you like.

## The problem

Every sales team has a lead score, and every sales team argues about it. The
weights come out of a pipeline review: *"we're over-indexing on company size,
authority matters more than we thought, and anything with a contract end date
should jump the queue."* That conversation happens monthly.

If the scoring lives in a prompt — "rate this lead 1–10 considering fit,
urgency and seniority" — every one of those arguments costs a full re-scoring
run over the whole queue, and you cannot see which part of the score moved.
Most teams give up and let the score rot.

The other half of the problem is that MQL scoring in a CRM is a form-field
exercise: company size, job title, UTM source. The thing that actually
qualifies a lead is what they *wrote*, and it never gets read by the scoring
system at all.

## Why this needs Jev

**Score once, reweight forever.** Jev returns the raw judgments — ICP fit,
stated pain, purchasing authority, urgency, plus independent disqualifier
probabilities. Those judgments do not change when you change a weight, because
a weight is not evidence. So the scoring policy lives in Python:

```python
total = Σ (weight × normalised_score) / Σ weights
```

and every slider in the sidebar re-ranks the entire pipeline with **zero API
calls**. That is the structural property: the evidence and the policy are
separable, and only the evidence costs money.

The second property: **disqualifiers veto, they do not average.** A competitor
doing market research can score perfectly on fit, pain and urgency. In a single
1–10 rubric they come out hot. Here they are separate Nouls with their own
threshold, and the veto survives a perfect composite.

## How it works

Four Scores and three Nouls per lead, plus one routing Choice, all in **one**
request:

```python
questions[f"{key}__{index}"] = score(
    {"task": spec["label"], "ideal_customer_profile": icp, "lead": reference},
    spec["levels"],
)
questions[f"competitor__{index}"] = noul(
    {"task": "Is the sender describing themselves as working at a company that sells a competing product?",
     "lead": reference},
    true="They work at a vendor in the same category, or say they are doing competitive research",
    false="They are a prospective user, or their employer is not in this category",
)
```

12 leads × 8 judgments = 96 questions, one round trip. The footer prints what
it actually cost.

Then, in Python only:

```python
def decide(total, judgment, veto, hot, warm):
    for key in DISQUALIFIERS:
        if judgment[key] >= veto:
            return "disqualified", ...
```

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Twelve synthetic inbound leads are bundled — including a competitor doing
research, an agency pitch, a student, and a two-word "please send info" — so it
runs before you supply anything.

## Design notes

**Why Scores for the four dimensions.** Fit and urgency are degrees, not
yes/no. Each level describes a concrete situation ("states a hard deadline, an
expiring contract, or a decision being made right now") because a level has to
stand alone to be usable — `["low","medium","high"]` tells the model nothing.

**Why Nouls for the three disqualifiers.** They are independent properties: a
lead can be both a competitor and an agency. They are not points on one rubric
and not options in one Choice, and keeping them separate is what lets you
threshold "competitor" tightly while leaving "job or study" loose.

**Why the ask is a Choice with `unclear`.** Exactly one intent wins, but plenty
of inbound has no intent at all. Without the no-match option the model would
nominate the least-wrong bucket at entirely plausible confidence.

**Normalisation before weighting.** The rubrics have 4, 4, 3 and 4 levels, so
raw scores are not comparable. Dividing by `len(levels)-1` and by the weight
sum puts the composite on 0–100 regardless of how you set the sliders.

## Limits

- **It reads the message, not the company.** Nothing here enriches from
  Clearbit or your CRM. A one-line email from a perfect-fit account will score
  low, correctly, because there is no evidence in front of it.
- **Thresholds are not universal.** 75/50 are placeholders. Tune them against
  a few hundred leads you already know the outcome of.
- **`jev-1.13` reads literally.** If your ICP says "Series B to Series D" and
  the lead says "we just raised", it will not infer the stage. Write the ICP
  the way you would brief a new SDR, and put boundary cases in the paragraph.
- **No arithmetic or date logic is asked of the model.** "Contract ends in
  November" is treated as an urgency *situation*, not a date to subtract from
  today. All aggregation is pandas.
- **Never a CRM write-back.** This ranks a queue for a human to work. It is not
  an auto-responder and does not decide who gets ignored.
