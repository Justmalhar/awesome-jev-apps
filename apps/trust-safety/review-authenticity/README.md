# ⭐ Review Authenticity

Flags incentivised, templated, and second-hand product reviews — against the
**operator's own** review rules, not a vendor's idea of a fake.

## The problem

A paid five-star review and a genuine five-star review have identical sentiment.
That is why sentiment models are useless here: the thing that separates them is
not tone, it is whether anyone ever held the product.

Worse, "fake" is not a fixed category. One marketplace allows disclosed sampling
and bans undisclosed it; another bans reviews written after a refund; a third
only cares about template farms and lets everything else through. Each of them
has written their rule down, and none of them can buy a classifier that knows it.

So the work falls to an integrity team reading reviews by hand, usually after a
seller complains about a competitor, which is the worst possible sampling
strategy.

## Why this needs Jev

1. **The rules are state.** Each Noul points at `` `rules[j].text` ``, so the
   operator's own wording is the policy. Change the box, change the questions —
   no fine-tune, no redeploy.
2. **Four independent tells, four Nouls.** A review can be incentivised without
   being templated, templated without being second-hand, and campaign-phrased
   without any of the above. They co-occur freely, so they cannot be levels on
   one rubric and cannot be options on one Choice: collapsing them would destroy
   the per-tell thresholding that is the entire point.
3. **First-hand detail is a Score,** because it is a degree, and it is used as a
   *brake*: `decide()` will not suppress anything with real lived detail in it,
   however many tells fired. That asymmetry is only expressible because the
   degree and the flags are separate answers.
4. **Price.** A catalogue's whole review history at $0.042/Mtok is a rounding
   error, so you can sweep everything rather than only what got reported.

## How it works

```python
for position, rule in enumerate(rules):            # the operator's own rules
    questions[f"rule_{index}_{position}"] = noul(
        {"task": "Does this review break the review-authenticity rule quoted below?",
         "rule": f"`rules[{position}].text`", "review": reference}, ...)

questions[f"incentivised_{index}"]    = noul("... given something in exchange?")
questions[f"templated_{index}"]       = noul("... assembled from a template?")
questions[f"second_hand_{index}"]     = noul("... something other than the writer's own use?")
questions[f"solicited_burst_{index}"] = noul("... supplied talking points?")
questions[f"firsthand_{index}"]       = score(..., FIRSTHAND_LEVELS)
questions[f"subject_{index}"]         = choice(..., SUBJECT_KINDS)   # incl. unclear
```

```python
def decide(rule_hit, tells, firsthand, gates):
    flagged = [n for n, p in tells.items() if p >= gates["tell"]]
    if rule_hit >= gates["rule"]:                                    return "human_review"
    if len(flagged) >= gates["suppress"] and firsthand < gates["firsthand"]: return "suppress"
    if flagged:                                                      return "label"
    return "publish"
```

A breach of the operator's own written rule **always** goes to a person — it is
the one signal with a named human author behind it, and it is the one most
likely to be contested by the reviewer.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five sample rules and ten synthetic reviews are bundled: a disclosed-sample
review, an undisclosed template, a review of a courier rather than a product, a
review of a product the writer's sister owns, a hashtag-and-discount-code
promotion, and four ordinary reviews — two positive, two critical — that must
come back clean. It runs cold with no input from you.

## Design notes

- **Suppression needs agreement plus an absence.** Two tells and no lived detail.
  One tell on a specific review is far more often a false positive than a farm.
- **`unclear` is mandatory** on the subject Choice. A two-word review is about
  nothing identifiable, and without the escape hatch it gets filed as
  `product_experience` at entirely plausible confidence.
- **Score levels are situations.** "Names a feature, a setting, or a moment of
  use" is checkable; "medium detail" is not.
- **The model never counts.** How many tells fired, how many reviews are in each
  bucket, which tell fires most often — all Python. `jev-1.13` cannot count.
- **`verified_purchase` is in the state but never decides anything.** It is a
  platform fact; the app treats it as context for the model, and leaves the
  weighting to your code.

## Limits

- **This assists a human integrity team. It does not replace it.** Suppressing a
  review affects a customer's account standing and a seller's livelihood — a
  person signs off before anything is removed.
- **The thresholds are placeholders.** They were chosen so the sample exercises
  every route. Tune them on reviews your own team has already adjudicated, and
  measure the false-suppression rate before you trust a single gate.
- **Text is the weakest evidence there is.** The real signals for review fraud
  are account age, purchase graph, review velocity, IP and device clustering,
  and payout links — none of which are in review text. Join this output to
  those; never substitute it.
- **A well-written fake beats it.** Someone who was sent a free unit, used it,
  and wrote honestly about it will read exactly like a genuine review — because
  in every respect except disclosure it is one.
- `jev-1.13` reads literally, so an ironic or deadpan review is a known weak
  spot, and it degrades on indirection: a rule that depends on the reviewer's
  other reviews cannot be applied, because they are not in the state.
- A single flagged tell is not evidence of wrongdoing and must never be shown to
  a customer as if it were.
