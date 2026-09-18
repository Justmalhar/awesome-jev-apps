# 📉 Anomaly Narrator

Point it at daily metrics and a deploy/campaign calendar. Statistics find the
outliers; Jev sorts them into *page someone today*, *note it in the weekly
review*, *the campaign explains it*, and *the pipeline broke, not the product*.

## The problem

Every monitoring system flags the same three things: the real incident, the
Black Friday spike, and Saturday. Two of those are noise, and the system has no
idea which is which — so the alert channel fills with movements that are
perfectly fine, people learn to scroll past them, and the fourth one, the one
that mattered, scrolls past too.

The fix everyone reaches for is more statistics: a tighter threshold, a seasonal
decomposition, a better z-score. That improves *detection*, which was never the
weak part. The weak part is the step after: the detector says "revenue is 68%
below its 28-day median" and cannot say "because it was a public holiday and
there is a calendar entry saying so."

That second step is judgment over context. Today it is done by a human reading a
dashboard next to a deploy log, every morning, forever.

## Why this needs Jev

The split is the whole app:

| | does what | never does |
|---|---|---|
| **pandas** | trailing median, MAD, robust z-score, day-of-week baseline, exact-zero run length, joining the event calendar onto each date | judge anything |
| **jev-1.13** | decide whether a characterised movement needs action | see a number it has to do arithmetic on |

`jev-1.13` cannot count, cannot do arithmetic and cannot compare dates —
documented failure modes, not tuning problems. So it is never shown a value and
a baseline and asked how they relate. Python computes the relationship and hands
over the finished sentence:

```json
{
  "series": "checkout_errors",
  "series_meaning": {
    "measures": "HTTP 5xx responses returned by the checkout service during a payment attempt.",
    "normal_shape": "Low and flat. A sustained rise means customers cannot pay."
  },
  "characterisation": {
    "versus_baseline": "the value is 46.55x the 28-day trailing median for this series, which stood at 11.00",
    "versus_this_weekday": "a typical Wednesday for this series runs at 1.08x that trailing median, so a Wednesday here would ordinarily land near 11.85",
    "deviation": "a robust deviation of 134.9 MAD-sigmas from that day-adjusted expectation",
    "recent_range": "over the 28 days before this point the series ran between 6.00 and 16.00"
  },
  "context_events": [
    {"kind": "deploy",
     "note": "checkout-service v3.1.0 rollout began, including a new payment-provider SDK.",
     "timing": "recorded 1 day(s) before this point"}
  ]
}
```

`"recorded 1 day(s) before this point"` is the important detail. The event
calendar is joined to the point's date **by pandas**; the model is handed the
relationship, never two dates and the expectation that it will subtract them.

The structural property being exploited is the batch: every flagged point in the
run goes into **one** `ask()`, three questions each, all answered in parallel
against a state ingested once.

## How it works

One Score per flagged point, whose **levels are the actions**:

```python
LEVELS = [
    "An expected movement with an obvious benign explanation in the context given -- a "
    "known campaign, a public holiday, a planned change. Nobody needs to act on it.",
    "Unexplained, but within the range of things this series does on its own. Worth a "
    "line in the weekly review rather than interrupting anybody today.",
    "A genuine break in the thing being measured. Customers, revenue or reliability are "
    "affected right now and someone should investigate today.",
]
```

There is no severity scale to map onto a runbook afterwards, because the rubric
*is* the runbook. Ask for "severity 1-5" and you have bought yourself a second
problem: deciding what 3 means.

Then two **separate Nouls**, because they are independent of the action and of
each other:

- `pipeline_failure` — *"is this a failure of the data pipeline rather than a
  real change in the thing being measured?"* In practice this is the single most
  useful flag in the app. A series that reads exactly `0.0` for three days did
  not have a 100% outage; the collector fell over. Routing that to the product
  on-call is how alerting gets muted.
- `context_explains` — *"does the supplied context already account for this?"*

These are not levels on the Score, and here is the concrete reason: the bundled
sample has a checkout error spike one day after a `checkout-service v3.1.0`
rollout. The context **explains** it and it is **still an outage**. Collapse
"explained" into the action rubric and you cannot express that; keep it separate
and `route()` pages anyway:

```python
def route(action, pipeline, explained, page_at, flag_gate):
    if pipeline >= flag_gate:  return "pipeline"     # wrong team, not no problem
    if action >= page_at:      return "page"         # explained or not
    if explained >= flag_gate: return "explained"
    return "review"
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Four daily series over 90 days ship with the app, plus a context file, so it
runs cold. The sample has four planted anomalies of deliberately different
kinds: a genuine incident spike, a Black-Friday campaign spike, a flat-line to
exactly zero where the metrics collector was migrated, and a public-holiday dip.

Your own metrics CSV needs `date`, `series`, `value`. The context JSON is
optional but is most of the value — with no events, every movement is
unexplained by construction.

## Design notes

**Two groups of sliders, and only one of them costs money.** The sidebar
separates *Detector (pure Python)* — the baseline window, the MAD-sigma
threshold, the event radius — from *Policy (no inference)* — the paging
threshold and the Noul flag gate.

Changing a detector setting changes which points exist, so it needs a fresh run.
Changing a policy setting does not: the action scores are already stored, and
`route()` re-derives all four queues from them. **Re-tuning what pages you costs
nothing and calls nothing.** That is the practical argument for keeping policy
in Python rather than baking "page if severe" into an instruction — it is the
difference between tuning an alerting policy in an afternoon and tuning it over
a month of API bills.

**Why the day-of-week baseline is in the detector, not just the narration.** A
trailing median is weekday-blind, so a weekday-heavy series flags every single
Saturday. On the bundled revenue series that is 25 candidates out of 90 days,
all of them fine. Dividing by the weekday factor first drops the sample from 32
candidates to 11 — and the 11 are the interesting ones. Cheap Python filtering
before inference is the same move as blocking in `entity-resolver`: it is the
difference between affordable and absurd, and the model gets a better question
because the obvious cases never reach it.

**Why the baseline is trailing and exclusive.** A point does not contribute to
the window it is judged against. Include it and a sustained break slowly
normalises itself — by day four of an outage, the outage is the median.

**Why `robust_z` returns infinity on a flat window.** MAD of zero means the
recent window never moved at all. That is not a divide-by-zero to guard past;
it is the flat-line case, and "any movement here is unprecedented" is the
correct thing to say about it.

## Limits

- **The detector is univariate and knows nothing about correlation.** Revenue
  and signups crashing together is two independent flags, not one incident with
  two symptoms. Real incident correlation needs a layer this app does not have.
- **90 days is not enough for annual seasonality.** The day-of-week factor is
  the only seasonality modelled. A yearly pattern — quarter-end, back-to-school,
  the January renewal cliff — will be flagged as an anomaly every time it comes
  around. Weekly is the shape this handles.
- **A slow drift is invisible.** A metric degrading 2% a day never deviates far
  from its own trailing median. This finds step changes, not trends. If that is
  your failure mode you want a different detector entirely.
- **`context_explains` is only as good as your calendar.** With an empty context
  file every flagged point is "unexplained", and the app degrades into an
  expensive z-score. The deploy log is not decoration; it is the input.
- **The model cannot verify magnitude reasoning.** It is told a movement is
  "46.55x the trailing median" and takes that on trust — it cannot check the
  division, and if `characterise()` had a bug, it would reason confidently from
  the wrong sentence. The statistics are tested offline in `test_app.py` for
  exactly this reason.
- **Confidence is distribution concentration, not correctness.** A confidently
  wrong action score is entirely possible. `action_confidence` is in the
  downloaded CSV; it is not wired into `route()`, because deciding *not* to page
  on a low-confidence high score is a policy call this app should not make for
  you.
- **Judged results are cached in session state, not persisted.** Reload the
  browser tab and you re-run. Fine for tuning a policy in an afternoon;
  not a monitoring system.
