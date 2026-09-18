# 🔄 Renewal Risk

Which accounts are drifting toward a non-renewal — with a risk score that is
composed in your code, not asked for.

## The problem

Health scores rot. Every CS org builds one, and within two quarters it is
producing amber for accounts everyone knows are fine and green for the one that
churned last week. The reason is always the same: the weighting was set once,
by whoever built it, and the world moved. Enterprise accounts fail differently
from SMB. A departed champion matters enormously in a 6-logo book and barely at
all in a 4,000-account self-serve base.

Meanwhile the actual evidence — the QBR notes, the CSM's write-up, the
post-incident review — is prose in a CRM field that the health score never
reads.

## Why this needs Jev

**Composite scores should be composed, not requested.** This app never asks
"how likely is this account to churn?" It asks ten separate, concrete
questions, and builds the risk score out of them in Python:

```python
risk = 100 * Σ(weight × normalised_level) / Σ weights
```

The consequence is the sidebar. Every weight is a slider, both band lines are
sliders, and moving any of them re-scores the whole book with **zero API
calls** — because a weight is an opinion, and opinions are not evidence. The
raw judgments stay valid across every version of your scoring model, which
means you can re-cut last quarter's book under this quarter's model without
paying for inference again.

**Vetoes bypass the sum.** Two hard flags (departed champion, exit enquiry,
budget review, executive dissatisfaction) send an account red regardless of the
composite. In a pure weighted score, five calm dimensions outvote the fact that
the buyer just left. The offline check asserts this holds at a *zero* composite.

**And the same judgments serve three consumers**: the ranking, the one-line
"why" beside each account, and the CSV your data team joins to product usage.
A single opaque risk number serves only the first.

## How it works

Five Scores and five Nouls per account, one request:

```python
questions[f"value_story__{index}"] = score(
    {"task": "Can they articulate the value", "account": reference},
    ["They describe a specific outcome in their own numbers or their own words",
     "They believe it helps but cannot point to anything concrete",
     "They are unsure what it has changed since they bought it",
     "They state plainly that it has not delivered what they expected"])

questions[f"champion_departed__{index}"] = noul(
    {"task": "Do the notes indicate the person who sponsored this purchase has left "
             "the company or changed role?", "account": reference},
    true="Their sponsor is described as having left, moved teams, or handed the account over",
    false="The original sponsor is still in place")
```

Banding, offsets and the ARR rollup are all Python:

```python
if len(fired) >= 2:  return "red", total, ...
if total >= red:     return "red", total, ...
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve synthetic accounts ship with it, chosen so the easy heuristics fail:
a quiet account with no support tickets *because nobody uses it*, a loud
account with a furious executive that is deeply embedded and safe, and one that
is healthy on every rubric but whose champion left in December.

## Design notes

**Why `support_burden` is a rubric and not a ticket count.** Ticket volume is
the classic health-score input and it is close to useless — Halcyon Media in
the sample has almost no tickets because nobody logs in. The rubric asks about
lived friction, which is what the notes describe.

**Why `expansion_in_flight` is a flag, not a negative weight.** It offsets a
single warning flag but is explicitly not allowed to rescue a red composite.
Expansion conversations happen inside accounts that then leave; treating growth
intent as generic negative risk would mask exactly that pattern.

**Why five levels of nothing and four levels of everything.** Each rubric has
four levels because that is how many distinguishable situations the notes
actually support. Adding a fifth would produce levels a CSM could not tell
apart, which is worse than fewer.

**Why the flags are Nouls.** All five can be true at once and each wants its
own gate. On one Choice they would compete; on one rubric they would average.

## Limits

- **Notes in, notes out.** It reads what the CSM wrote. An account nobody has
  written about for a quarter scores as whatever the stale note says — that is
  a process problem this app cannot fix, though the empty `notes` field makes it
  visible.
- **The risk number is not a probability.** It is a weighted position on
  rubrics you chose. Calibrate the bands against accounts whose outcome you
  already know before anyone puts it in a board deck.
- **No dates, no arithmetic in the model.** Renewal quarters are carried as
  data and never compared; ARR sums, means and rollups are pandas. `jev-1.13`
  cannot do either and is never asked.
- **This is one of two apps over the same evidence.** `churn-signals/` extracts
  conversation-level features and deliberately refuses to compose them. This one
  composes — with the weights visible. Use both, or pick the shape you need.
- **`jev-1.13` reads literally.** "Marta has moved to the loyalty team" reads as
  a champion departure only because the criteria say changing role counts. Write
  the criteria for your own vocabulary.
- **It ranks accounts for a human to work.** It does not decide who gets a save
  play, and it should never be the only input to a renewal forecast.
