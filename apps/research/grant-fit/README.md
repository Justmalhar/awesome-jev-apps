# 💰 Grant Fit

Which funding calls actually fit this proposal — ranked, with eligibility as a
veto.

## The problem

A PI with a half-written proposal faces a catalog: two research council calls, a
European action, three charities, an early-career scheme, an industrial
partnership fund, an open-source trust. Each carries a remit paragraph, an
eligibility paragraph and a deadline, and reading all of them *properly* is a
day of work that produces nothing publishable.

So nobody does it. People apply to the call they heard about in a corridor, find
out at submission that it required a four-country consortium, and never learn
that the scheme which would have funded them closed three weeks earlier.

Keyword search makes this worse in a specific way: it ranks by topical
similarity, which puts the perfectly-matched call you are **structurally barred
from applying to** at the top of the list.

## Why this needs Jev

This is relevance ranking over a catalog — one proposal, N calls, all judged
against **state ingested once, in a single request**. Nothing is sequential and
nothing depends on anything else, so nothing needs more than one round trip. Ten
calls is thirty judgments for a fraction of a cent.

Each of the three primitives earns its place:

```python
questions[f"fit_{index}"]          = score(..., FIT_LEVELS)   # ordered: a ranking key
questions[f"eligible_{index}"]     = noul(...)                # binary, independent
questions[f"disqualified_{index}"] = noul(...)                # binary, independent, a VETO
```

Fit is a **Score** because topical alignment genuinely is ordered — from
"outside every area the call names" to "aims, methods and outputs all match" —
and because a Score lands *between* levels, which is exactly what you need to
sort by.

Eligibility is **not** a level on that scale, and this is the whole argument.
Fusing them into one "suitability" number produces the keyword-search failure in
a more expensive form: a 2.9 fit at 2% eligibility averages to something
respectable and sails to the top of the list. The Python policy is a veto:

```python
disqualified ≥ threshold  →  out, whatever the fit
eligible     ≤ threshold  →  out, whatever the fit
fit < floor               →  out
otherwise                 →  shortlist, sorted by fit
```

**Deadlines never reach the model.** `jev-1.13` cannot compare dates — that is a
documented failure mode, not a prompting problem — so `open_calls()` filters the
catalog with `datetime.date` *before* any question is built. That also keeps
dead calls out of the state, which matters because the model degrades on large
state full of irrelevant detail.

## How it works

Every slider (lead time, eligibility floor, veto threshold, fit floor) is
Python-side. Re-ranking the whole catalog under a different policy re-runs
**zero** inference.

The "ruled out" table is deliberately shown with its fit scores visible: the
high-fit rejects are precisely the calls a keyword search would have put first
and a week would have been wasted on.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A synthetic proposal (federated anomaly detection for municipal water sensor
networks) and a ten-call synthetic catalog are bundled. The catalog is built
around the traps: a call that is a near-perfect topical match but requires a
four-country consortium, an early-career scheme the applicant is too senior for,
a company-led scheme a university cannot lead, a health call that needs a
clinical collaborator, one already-closed call to exercise the date filter, and
a humanities call that should land at the bottom on topic alone.

## Design notes

- **Four levels, not five.** Each has to be a situation a PI would recognise in
  their own proposal. Levels you cannot tell apart produce a flat distribution
  and a useless ranking key.
- **Two eligibility Nouls, not one.** "Does the applicant qualify?" and "does
  the proposal fail a structural requirement of the call?" fail differently —
  career stage versus a missing consortium — and a PI acts on them differently.
  Independent conditions, independent thresholds.
- **`confidence` is displayed, never used for policy.** It measures how
  concentrated the distribution is over levels, not whether the level is right.
  Two adjacent levels splitting evenly is a *correct* answer for a call that
  half-fits.
- **Undated calls stay in the live set.** Dropping them silently would hide
  rolling schemes, which are often the best option.

## This assists a human researcher

It produces a reading order, not a decision. The output says *"these three are
worth an hour each, in this order, and here is why the other seven are not"* —
and every reject names the clause that ruled it out, so a PI who disagrees can
check it in seconds.

**Recall matters more than precision here**, as in any screening task. A weak
call on the shortlist costs an hour of reading; a strong call wrongly vetoed
costs the grant. Keep the fit floor low and the eligibility threshold generous,
and read the "ruled out" table rather than trusting it.

## Limits

- **Judges the remit text, not the funder's actual behaviour.** Panels have
  preferences that appear in no published call document. Talk to your research
  office; they know things this cannot.
- **No budget or duration matching.** Those are numbers, and comparing numbers
  is Python's job. Add the filter yourself if your catalog carries the fields.
- **Eligibility rules are often layered.** "Eligible institutions are listed in
  Annex C" cannot be resolved from the call text alone, and reads as ambiguous.
- **A proposal summary is not a proposal.** Fit judged from four paragraphs is
  fit-of-the-summary. If the summary undersells the methods, so will the score.
- **`jev-1.13` reads literally.** A call whose remit is written in
  funder-speak ("transformative interdisciplinary challenges") gives everything
  a middling score, because that text genuinely excludes nothing.
