# 📈 ML Features

Turns free text into calibrated numeric features, trains a scikit-learn model on
them, and reports **the lift over a text-free baseline**. The output is a
`.joblib` file and a number.

## The problem

You have a churn model. It is trained on `tenure_months`, `seats`,
`monthly_spend`, `prior_tickets_90d`, `first_response_hours`, `plan_tier` — the
columns that already live in a matrix. It gets AUC 0.70 and everyone agrees that
is a bit disappointing.

Sitting in the same warehouse, joined on the same customer id, is the support
ticket where they wrote:

> *"Raising this again: the SSO login flow. This is the fourth time we've raised
> it. Frankly the equivalent feature in Zendrift just works. We are seriously
> reconsidering the renewal."*

That text is not in the model, because text is not a number. The three standard
ways of making it one are all bad, and they are bad in different ways:

| Approach | What you get | Why it hurts |
|---|---|---|
| TF-IDF / bag of words | ~5,000 sparse columns | Encodes vocabulary, not meaning. `"reconsidering the renewal"` and `"not sure we'll stay"` share no tokens. Overfits instantly on 170 rows. |
| Sentence embeddings | 768 dense columns | Encodes meaning you cannot name. Needs far more labelled rows than you have. Nobody can explain a coefficient to Legal. |
| An LLM per ticket | A sentence | You have to parse it back into a number, and whatever calibration existed is gone by the time you have. Also: one request per row. |

## Why this needs Jev

**A Noul returns a float in [0, 1]. That is already a feature.** Not a label to
one-hot, not a string to parse, not a logit to hope about — a calibrated
probability, which is precisely the datatype `LogisticRegression` wants.

```python
ticket_text -> {"considering_leaving": 0.91, "competitor_named": 0.88,
                "repeat_problem": 0.94, "work_blocked": 0.12, ...}
```

Seven of those, plus a Score (`temperature`, 0–2) and a one-hot Choice
(`issue_type`), gives you **16 dense, named, model-ready columns**. Three
structural properties follow, and none of them is "it's an LLM but cheaper":

1. **They are named, so the model stays auditable.** The coefficient table in
   the app is readable: `considering_leaving` has a coefficient, and you can
   show it to the person who has to sign off on the model. This is usually the
   actual reason the text was dropped, not the engineering.
2. **They are calibrated, so no feature engineering follows.** No binning, no
   scaling story, no "what does 0.4 mean" conversation. A probability is already
   on the right scale, and `0.55` on `considering_leaving` is a genuinely
   different row from `0.95` — a hard label would have thrown that away.
3. **Independent properties are independent columns.** A ticket can be a repeat
   problem *and* name a competitor *and* mention leaving. One Choice would make
   those compete for probability mass; one Score would impose an order on things
   that have none. Seven Nouls keeps seven signals, and the model gets to
   discover that only some of them matter.

And all of it batches: every question for every row in the batch goes into one
`ask()`. 170 tickets × 9 questions is **1,530 judgements** in a handful of
requests, not 1,530 calls.

## How it works

```python
for index in range(len(rows)):
    reference = f"`tickets[{index}].ticket_text`"
    for feature in NOUL_FEATURES:
        questions[f"{feature['id']}_{index}"] = noul(
            {"task": feature["task"], "ticket_text": reference},
            true=feature["true"], false=feature["false"])
    questions[f"temperature_{index}"] = score({...}, TEMPERATURE_LEVELS)
    questions[f"issue_{index}"] = choice({...}, ISSUE_TYPES)
```

Then the comparison, which is the whole point:

```python
baseline = evaluate(design_matrix(frame), y, seed, folds, C)               # structured only
enriched = evaluate(design_matrix(frame, jev_frame(records)), y, seed, folds, C)
delta    = lift(baseline, enriched)
```

Same model, same stratified folds, same seed, same rows. The **only** difference
is the text block, which is what makes ΔAUC mean something. Both numbers are
out-of-fold from `cross_val_predict` — in-sample AUC would go up if you added
random noise as a feature.

The verdict banner is willing to say **not worth it**. An app that can only
report a win is a demo, not a measurement.

### Baseline runs with no API key at all

Open the app and the structured-only AUC is already on screen, before you have
spent a cent. That is deliberate: you should see what you are trying to beat
before you decide whether beating it is worth paying for.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

170 synthetic support tickets with a 90-day churn outcome are bundled. The
generating process puts real signal in the prose that is **not** recoverable
from the structured columns — `considering_leaving`, `competitor_named`,
`repeat_problem`, `work_blocked` and a calm-tone term all move the outcome, and
none of them has a structured proxy.

Measured offline, with crude keyword regexes standing in for the Nouls:

```
baseline (7 structured features)         ROC AUC 0.699
+ 6 keyword proxies for the text signals ROC AUC 0.759   (+0.060)
```

That +0.06 is the **floor**, not the result — keyword matching gets
`"reconsidering the renewal"` and misses `"I've been asked to justify keeping
this contract"`. `test_app.py` asserts that this floor exists, so the app cannot
quietly become a machine for producing a convincing zero.

Your own CSV needs `ticket_text` and `churned_within_90d`; any of the structured
columns it also has get used, and the ones it lacks are skipped.

## Design notes

**Why `temperature` is a Score and the rest are Nouls.** "Is the customer
thinking about leaving?" is a yes/no with a probability. "How hard are they
pushing?" is a *degree*, and a Noul at 0.5 would mean "equally likely to be
pushing and not pushing", which is not what anyone wants to model. The three
levels are situations that stand alone, so the resulting 0–2 float is
interpretable without a legend.

**Why the Choice becomes one-hot in pandas.** `jev_frame()` does
`get_dummies` over the full category list, including categories absent from this
batch. That keeps the matrix width stable across runs — otherwise a batch with
no `feature_request` tickets silently produces a different model.

**Why `class_weight="balanced"` and why accuracy is reported but not led with.**
The churn rate is 29%. Accuracy at a 0.5 threshold is dominated by the majority
class and moves very little; AUC and average precision are the numbers that
respond to better features. Accuracy is in the table because people ask for it.

**Why the features CSV is downloadable.** The expensive step is extraction, and
it happens once. Every subsequent retrain — different C, different folds,
different model entirely — reads the CSV and costs nothing. Treat the extraction
as an ETL job, not as part of the training loop.

## Limits

- **170 rows is a toy.** ΔAUC on a sample this size has a wide confidence
  interval, and this app does not compute one. Before you act on a lift, bootstrap
  it or re-run across several seeds — the seed box is there for exactly that.
  A single ΔAUC from a single split is a direction, not a decision.
- **The sample is synthetic, and generated from a known logistic model.** That
  guarantees the signal is *there*, which is what makes it a useful test
  harness. It also means the lift you measure here is cleaner than anything real
  data will give you. Run it on your own labelled export before believing a
  number.
- **The features are only as good as the criteria.** Every Noul's `true`/`false`
  text is doing the work. `jev-1.13` reads literally: if `competitor_named`
  under-fires, it is because your `true` clause did not mention "also trialling",
  not because the model is weak.
- **Leakage is your problem, not the app's.** If the ticket text was written
  *after* the churn decision was taken, these features will look spectacular and
  predict nothing in production. Check the timestamps yourself — `jev-1.13`
  cannot compare dates, so the app cannot check for you.
- **No confidence on a Noul.** `Answers.confidence()` raises on one, by design.
  A Noul's probability *is* its answer. Only `issue_confidence` exists, and it
  describes the Choice.
- **Sending customer text to a third party.** The whole ticket body goes to
  whichever provider `providers.toml` selects. Read that provider's data policy
  before pointing this at real support data, and consider extracting features
  from a redacted copy.
