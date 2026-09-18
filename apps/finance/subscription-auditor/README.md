# 🔁 Subscription Auditor

Point it at a statement export. Get back every merchant that is billing you on
an ongoing basis, an annual run-rate for each, and a shortlist of the ones you
have probably forgotten you are paying for.

## The problem

The money leaks nobody notices are not the big ones. They are £9 a month for a
cloud-storage tier you stopped using in 2023, £4.99 for a meditation app from a
new-year resolution, and a £12 analytics tool you signed up for during a trial
and never cancelled. Banking apps have "subscription" tabs, but they work off a
merchant allowlist, so anything niche — which is precisely the stuff you forget —
never appears.

Doing it by hand means scrolling a year of statement lines, mentally grouping
`NETFLIX.COM 4471` with `NETFLIX.COM*8812`, and spotting a regular pattern
across 700 rows. People start, get bored around March, and stop.

## Why this needs Jev

Because the hard part splits cleanly into a part code is good at and a part it
is hopeless at.

**Cadence is arithmetic.** Grouping merchants, diffing dates, taking a median
gap, measuring amount drift — pandas, exactly, for free, and `jev-1.13` could
not do it if you asked. So it is never asked.

**"Is this a subscription" is judgment, and cadence does not settle it.** A
weekly grocery shop is more regular than most subscriptions. An annual
membership renewal has two data points and a 365-day gap. A commute fare is
metronomic. Regularity gets you a candidate list full of Tesco.

So the measured cadence goes *into the state as a fact*, and the model reads it
alongside the merchant name to answer something a regex cannot:

> Is this merchant billing the account holder automatically, or is this somewhere
> they choose to spend repeatedly?

The second structural property is **separate thresholding**. "It recurs" and
"you have abandoned it" are independent — your home insurance recurs and is not
abandoned; a trial you forgot is both. They are two Nouls with two sliders, not
two levels on one rubric. Collapse them and the app starts recommending you
cancel your car insurance because it scored high on "recurring".

## How it works

One request. Four questions per merchant, answered in parallel:

```python
questions[f"recurring_{index}"] = noul(
    {"task": "Is this merchant billing the account holder on an ongoing subscription "
             "or contract, rather than being somewhere they choose to spend repeatedly?",
     "merchant": reference,
     "note": "The cadence fields are measurements already taken from the statement. "
             "Read them; do not re-derive them."},
    true="An automatic ongoing charge the account holder must act to stop: subscription, "
         "membership, contract, premium, or licence renewal.",
    false="Repeat discretionary spending they choose each time, such as a weekly food "
          "shop, a commute fare, or a favourite coffee shop.",
)
```

plus `abandoned_` (a second, independently gated Noul), `kind_` (a Choice, with
`not_a_subscription` as the escape hatch) and `cancel_` (a Score whose three
levels are the three things ending it can actually involve).

Then Python does every number:

```python
def annualise(typical_amount, gap_days):
    return round(typical_amount * (365.0 / gap_days), 2)
```

The annual run-rate, the recoverable total, the per-kind chart — all pandas,
from the labels. The model never sees a total and is never shown one to check.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A 129-row synthetic six-month statement is bundled: ten real monthly
subscriptions, a quarterly water bill, an annual membership that went up in
price, and three regular-but-not-subscription patterns (weekly shop, commute,
coffee) planted specifically to catch a cadence-only detector.

## Design notes

- **Two Nouls, not one Score.** Recurrence and abandonment are orthogonal. Two
  probabilities, two sliders, tuned independently against your own habits.
- **`not_a_subscription` in the Choice.** A Choice must return something. Without
  an escape hatch, `TESCO SUPERSTORE` gets filed as `membership` with entirely
  convincing confidence.
- **Score levels are consequences, not adjectives.** "Self-service button",
  "notice period", "minimum term or retention call" are three different Saturday
  afternoons. `["easy","medium","hard"]` would be rejected here and should be.
- **Thresholds live in the sidebar, never in the instruction.** Moving a slider
  re-derives the whole table from probabilities already in hand — no new request,
  no new cost.

## Limits

- **Merchant normalisation is a regex, and regexes are dumb.** It collapses
  trailing reference numbers and little else. A merchant that appears as
  `AMZNMktplace` and `AMAZON.CO.UK` stays two merchants, and the model never gets
  the chance to say otherwise. Fixing that properly is the entity-resolver app's
  job, not this one's.
- **Annual and quarterly subscriptions with one charge in the window are
  invisible**, because a single charge produces no cadence to measure. Feed it
  two years if you care about those.
- **`jev-1.13` cannot compare dates.** It cannot tell you a subscription stopped
  three months ago; `last_charged` is a pandas column, and deciding what counts
  as stale is your Python, not its judgment.
- **"Abandoned" is a prior about households, not a fact about you.** The model is
  inferring from the merchant's character that this is the *kind* of thing people
  forget. If you genuinely use that obscure analytics tool daily, it will still
  flag it. It is a shortlist to review, not a cancellation queue to execute.

## Privacy

Merchant names and statement descriptions are sent to whichever provider
`providers.toml` selects. Financial text leaves your machine. Read that
provider's data-retention policy before pointing this at a real account.
