# ⚖️ Appeal Router

A second opinion on moderation appeals that is **allowed to abstain** — it
orders the queue, it never decides it.

## The problem

Appeal queues are where moderation goes to die. They are long, they are
first-in-first-out, and the overturn rate in most of them sits somewhere between
5% and 20%. So the overwhelming majority of a reviewer's day is spent confirming
decisions that were already right, while the one user who was wrongly suspended
waits three weeks behind them.

Nobody wants an AI to *decide* appeals. That is the one place in trust and
safety where a human decision is not negotiable — it is often a legal
requirement, and it is always the right thing. But everybody wants the queue
sorted so the plausible overturns are not at the back of it.

## Why this needs Jev

1. **Abstention is a first-class option.** `cannot_tell` is one of the three
   Choice options, and `decide()` honours it *even at high confidence*. A second
   opinion that never says "I cannot tell" is not a second opinion, it is a coin
   toss with a number attached. Most classifier APIs cannot express this at all.
2. **Confidence is a separate axis from the view.** A Choice returns a winner, a
   distribution, and a concentration. The app abstains on low confidence
   *without* the model having to be asked to rate its own certainty — which is
   the thing models are worst at.
3. **Three signals that are independent of the outcome.** An appeal can bring
   genuinely new information and still deserve to fail. A decision can be
   procedurally broken and still have reached the right answer. Separate Nouls
   keep those separable, and each one routes differently.
4. **The rule lookup happens in Python.** `resolve_citations()` maps the cited
   rule id to its text from the operator's own rulebook and attaches it to the
   appeal, so the model reads the actual rule rather than being asked to look an
   id up in a list. A citation that is not in the rulebook is reported as a
   process defect on its face — found in code, exactly, with no inference.

## How it works

```python
questions[f"view_{index}"] = choice(
    {"task": "Reviewing this moderation decision fresh, should the appeal succeed?",
     "appeal": reference},
    {"overturn": ..., "uphold": ..., "cannot_tell": "The record does not contain "
                                                    "enough to have a view either way."})

questions[f"rule_fits_{index}"]       = noul("Does the content actually do what the cited rule forbids?")
questions[f"new_information_{index}"] = noul("Does the appellant supply context the original reviewer could not have had?")
questions[f"process_error_{index}"]   = noul("Is there a defect in HOW the decision was made, separate from whether it was right?")
questions[f"cost_{index}"]            = score(..., COST_LEVELS)   # used only to order the queue
```

```python
def decide(view, confidence, signals, cost, gates):
    if view == "cannot_tell" or confidence < gates["confidence"]: return "abstain"
    if signals["process_error"] >= gates["signal"]:               return "priority_review"
    if view == "overturn":
        return "priority_review" if cost >= gates["cost"] else "overturn_recommended"
    if signals["new_information"] >= gates["signal"]:             return "priority_review"
    if signals["rule_fits"] < gates["rule_fits"]:                 return "priority_review"
    return "uphold_recommended"
```

Note what `uphold_recommended` requires: a confident uphold, no process defect,
nothing new from the appellant, **and** a cited rule that actually fits the
content. An uphold on a rule that does not fit is not an uphold — it is a
mis-citation wearing an uphold's clothes, and it goes to the front.

Every route ends at a person. The four labels are queues, not outcomes.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A seven-rule sample rulebook and ten synthetic appeals ship with it: a useful
post removed as advertising, an insult appealed as banter, an unsourced claim
whose author has since found a source, a baking post removed under a trading
rule that has nothing to do with it, a suspension the appellant thinks
disproportionate, a "wrong post actioned" claim, and a "which rule even covers
this?" objection. It runs cold, with no input from you.

## Design notes

- **Cost never decides an appeal.** It only expedites. Weighting an outcome by
  how much the user stands to lose would be a thumb on the scale, and would be
  indefensible in an appeal process.
- **`cannot_tell` is honoured before confidence is even read.** An explicit
  refusal at 0.95 confidence is still a refusal; treating it as a weak uphold
  would quietly delete the feature.
- **`probabilities()` is shown for abstentions**, because the useful thing about
  a 0.48/0.46 split is that it *is* a split — the senior reviewer should see
  that, not a rounded label.
- **The model never counts and never looks anything up.** Rule resolution, queue
  sizes, and route tallies are all Python. `jev-1.13` cannot count.

## Limits

- **This assists human appeal reviewers. It does not replace them, and it must
  not auto-resolve a single appeal.** Every decision here affects a user's
  account, reach, or income; each one needs a person, and in several
  jurisdictions that is the law rather than a preference.
- **The thresholds are placeholders**, chosen so the sample exercises every
  route. Tune them on appeals your own team has already adjudicated, and measure
  against the *final* human outcome — not against the original decision, which
  is the thing under question.
- **It sees the record, not the history.** No account history, no prior strikes,
  no the-same-user-appealed-nine-times, no thread the post sat in, no
  attachments. A rule that depends on any of those cannot be evaluated here.
- **The appellant's statement is unverified**, and a fluent, plausible statement
  will move the `new_information` Noul whether or not it is true. That signal
  routes to a human precisely because it cannot be checked.
- **It inherits the framing of the original decision.** If the reason given was
  wrong in an interesting way, the model reads that wrong reason too.
- `jev-1.13` reads literally. Appeals are often written by angry people in
  compressed, sarcastic prose, which is its weakest register — expect the
  abstention pile to be large, and treat a small abstention pile as a symptom
  rather than a success.
