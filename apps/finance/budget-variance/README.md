# 📉 Budget Variance

pandas computes every variance. Jev reads the owner's commentary and classifies
what the variance is being blamed on — and, crucially, whether that cause is
going to happen again.

## The problem

Month-end variance analysis is two jobs that get conflated. The first is
arithmetic: budget minus actual, as a percentage, flagged where material. A
spreadsheet has done that job perfectly since 1985.

The second job is reading eighteen paragraphs of commentary from six department
heads and turning them into the only distinction finance actually needs:

> **Is this variance going to be here next quarter?**

A £137,500 events overspend and a £41,900 energy overspend look identical in a
variance column. One is the Lisbon conference landing a quarter early — the full
year is unchanged and nothing needs doing. The other is a fixed-price contract
that expired and renewed 47% higher, which is the new baseline until 2028 and
needs the forecast moved today.

That distinction is not in the numbers. It is in a sentence somebody typed into a
commentary box.

## Why this needs Jev

**Because the split is unusually clean.** Every figure on screen is pandas:

```python
frame["variance"] = frame["actual"] - frame["budget"]
frame["variance_pct"] = (frame["variance"] / frame["budget"] * 100).round(2)
```

`jev-1.13` cannot subtract, cannot take a percentage, and is never asked to. It
never sees a budget figure or an actual. It sees the department, the line item,
the direction, and the note — and answers four things about the note:

- **Choice:** which cause the commentary states (11 options, including
  `no_cause_stated`)
- **Noul:** will this still be here next period?
- **Noul:** could the department have decided differently?
- **Noul:** did anyone commit to a specific action?
- **Score:** could any of this be checked against another record?

**The three Nouls are genuinely independent**, and collapsing them is the design
error this app exists to avoid. Energy is permanent *and* not controllable.
Cloud waste is controllable *and* not permanent once the teardown is scheduled.
Customer Success headcount is permanent *and* controllable *and* was approved in
advance. One rubric cannot express that; three probabilities with three gates
can.

**And filtering happens before judging.** Materiality is two comparisons in
pandas, costs nothing, and cuts 18 lines to 9 — so half the questions are never
asked. Filter in code, judge in the model.

## How it works

```
budget.csv + notes.csv  ->  reconcile()    every figure, in pandas
                        ->  material()     two free comparisons, halves the work
                        ->  1 request      choice + 3 nouls + score per line
                        ->  action()       what finance does next
                        ->  run_rate_impact()  variance x periods remaining
```

`action()` is the policy, and its ordering is the argument:

```python
if cause == NO_CAUSE:          return "explanation_required"
if recurring >= recurring_gate: return "reforecast" if controllable < control_gate else "reforecast_and_challenge"
if controllable >= control_gate: return "challenge_owner"
if cause in TIMING_CAUSES:      return "phasing_correction"
return "accept"
```

An unexplained material variance is a conversation regardless of anything else.
A permanent change needs the forecast moved whether or not anyone is at fault.
Blame and reforecasting are different decisions and the code keeps them apart.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 18 budget lines across five departments and 10 owner notes. Planted in
it — a genuine timing shift, a permanent price rise, contractors substituting for
vacant permanent roles, cloud resources nobody turned off, an approved permanent
headcount increase, a large variance with a useless six-word note, and several
material variances with **no note at all**, which is the most common real-world
case and the one the `no_cause_stated` option exists for.

## Design notes

- **`stated_cause` is what the commentary claims, not what is true.** The app
  classifies an assertion. Whether the events team really did move Lisbon at the
  partner's request is not a judgment anyone should be making from a text box, and
  the column name says so.
- **`no_cause_stated` is load-bearing, not decorative.** Roughly half of real
  commentary boxes are empty or say "higher than budgeted". Without the escape
  hatch, a Choice must nominate `price_increase` for an empty string, at
  confidence you would believe.
- **The Score measures checkability, not quality.** Its three levels are three
  different follow-ups: nothing to check, nothing checkable, or dates and
  approvals you can pull. That is more actionable than "good/poor commentary".
- **Materiality is two sliders, not a rule in a prompt.** Changing it re-runs the
  filter and changes what gets asked — which is the correct place for a policy
  that varies by company and by month.
- **`run_rate_impact` only fires on recurring lines.** Annualising a timing shift
  is how a forecast ends up double-counting spend that already happened.

## Limits

- **Notes are the ceiling.** BL-17's entire commentary is "Higher than budgeted.
  External advisory." — a £158k variance with nothing to classify. The app will
  correctly route it to `explanation_required`, which is useful, but it did not
  find out anything you did not know.
- **The model never sees the numbers**, deliberately. So it cannot notice that a
  note describing a small timing shift sits against a variance of £158k. Sanity
  checks like that are pandas comparisons between the stated cause and the
  magnitude, and they are not built.
- **No prior-period memory.** "Will this repeat?" is judged from the commentary
  alone. A line that has been over budget for four consecutive quarters with a
  fresh one-off excuse each time looks fine here. That pattern is a group-by over
  history and belongs upstream.
- **`jev-1.13` cannot compare dates**, so nothing reasons about whether a
  committed action date has passed or whether a "unwinds from August" promise was
  kept.
- **One note per line.** Multi-paragraph commentary covering three causes gets one
  label and loses two. Split the note upstream if that matters.
- **Favourable variances get the same treatment as adverse ones**, which is right
  for forecasting and wrong for tone — an underspend routed to `challenge_owner`
  reads harsher than intended.

## Privacy

Department names, line items, owner names and commentary are sent to whichever
provider `providers.toml` selects. Management-accounts commentary is internal
financial information and frequently names individuals and unannounced decisions —
"three CSMs hired ahead of plan", "the offers fell through". Check the provider's
retention policy, and consider stripping the owner column, before running this
against real management accounts.
