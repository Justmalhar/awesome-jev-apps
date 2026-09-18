# 📉 Churn Signals

Extract early-warning **features** from support and sales conversations. Not a
churn score — columns.

## The problem

The evidence that an account is drifting is almost always written down
somewhere: a support ticket that says "this is the fourth time", a CSM note
that says the champion moved teams, a passing question about the notice period.
It is written in prose, in three different systems, and nobody reads it in
aggregate. By the time it reaches a health score it has been reduced to
"tickets opened this month".

The usual fix is to ask a model "is this account at risk?" That produces a
number that cannot be audited, cannot be joined to your usage data, and moves
for reasons nobody can name. When the CSM asks "why is Northwind amber?", the
answer is a vibe.

## Why this needs Jev

**A verdict is a dead end; features compose.** This app extracts thirteen
independent, reusable judgments per conversation — champion departed, exit
mechanics asked about, repeat unresolved issue, competitor named, expansion
intent, and so on — and hands you the matrix. Each column is a number you can:

- join to product usage, billing and ticket volume,
- feed as a feature into whatever model you already have,
- threshold independently, at a threshold *you* choose,
- and explain to a CSM in one sentence.

The structural property being exploited is that **independent properties stay
independent**. All thirteen can be true at once. They are not a taxonomy, not a
rubric, and not a single score — each is its own Noul with its own threshold.
Collapsing them into one "risk level" is precisely what destroys the ability to
act on them separately, and separate action is the entire job.

The second property: one request. 12 conversations × 13 judgments = 156
questions, evaluated in parallel against a state ingested once.

## How it works

```python
for key, (task, yes, no) in SIGNALS.items():
    questions[f"{key}__{index}"] = noul(
        {"task": task, "conversation": reference}, true=yes, false=no)

questions[f"tone__{index}"] = score(
    {"task": "Rate the customer's tone in this conversation.", "conversation": reference},
    ["The customer is warm, patient, and collaborative throughout",
     "The customer is businesslike with no visible frustration",
     "The customer is visibly frustrated about a specific issue",
     "The customer is hostile, or expresses that their patience has run out"])
```

Rollups — max per account, fire rates, signal frequency — are pandas:

```python
rollup = frame.groupby("account")[feature_columns].max()
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve synthetic conversations across six accounts ship with it: a silent
churn (Halcyon), a healthy expansion with one compliance risk (Kestrel), a
stalled onboarding with a champion change (Sable), and an account one incident
away from an RFP (Vertex). Export the matrix as CSV from the app.

## Design notes

**Why no verdict, deliberately.** The app refuses to output a risk level. That
is not modesty — a composite would have to bake in weights, and those weights
are exactly what differs between a self-serve book of 4,000 accounts and six
enterprise logos. `renewal-risk/` in this same category *does* compose these
into a score; that composition belongs in a different app with its own sliders.

**Why `expansion_intent` and `advocacy` are in here.** Negative-only feature
sets produce models that cannot distinguish "quiet" from "healthy". The
counter-signals are as load-bearing as the warnings.

**Why tone and trust are Scores.** They are degrees, and a Noul is not an
intensity dial — `P=0.5` means genuinely undecided, not "moderately annoyed".

**Why the threshold slider is cosmetic.** It only decides what the rollup calls
"fired". The exported CSV carries raw probabilities, because thresholding is
the consumer's decision and throwing away the probability is irreversible.

## Limits

- **One conversation at a time, no history.** The model sees the transcript in
  front of it. "This is the fourth time" is a signal because the customer
  *wrote* it, not because the app counted tickets.
- **No arithmetic, no dates.** Nothing asks the model how long since the last
  contact or how many signals fired. All of that is pandas.
- **Signals are not calibrated probabilities of churn.** `P(competitor_named)`
  is the probability the *statement is true of this text*, not the probability
  the account leaves. Fit that mapping on your own outcomes.
- **Sales notes are written by humans with incentives.** A CSM who writes
  optimistic notes produces optimistic features. Garbage in, features out.
- **`jev-1.13` reads literally.** If the note says "Ingrid is no longer with
  us", it will not reliably infer a champion departure unless the criteria say
  what that phrase means. Put the boundary cases in `true`/`false`.
