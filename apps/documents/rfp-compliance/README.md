# 📋 RFP Compliance

Check a proposal against every requirement in a tender — and separate *"we
answered it"* from *"we committed to it"*.

## The problem

A public-sector tender arrives with 60–200 numbered requirements. Somebody has
to sit with the requirements matrix in one window and the 40-page draft
response in the other and tick each row. It happens twice: once by the bid
writer, once by the reviewer who does not trust the bid writer.

Two failure modes survive that process, and both are expensive:

1. **The silent omission.** A mandatory requirement nobody answered, because it
   was buried in section 4.7 of the ITT and the response was structured around
   the product instead of the questionnaire. The bid is non-compliant and gets
   binned before anyone reads the price.
2. **The capability boast.** The requirement says *"the supplier shall notify
   within 24 hours"*. The response says *"our incident process typically
   notifies customers quickly"*. That reads as an answer and scores as a
   non-answer, because it is not something the Authority could enforce.

The second one is the reason a compliance checklist that only asks *"is this
covered?"* is worth very little.

## Why this needs Jev

*Addressed* and *committed* are **independent properties**. A proposal can be
thoroughly addressed and entirely uncommitted — that is precisely what a
well-written vague answer looks like. So they are two Nouls with two
thresholds, not two levels on one "compliance" rubric:

```python
questions[f"addressed_{index}"] = noul(
    {"task": "Does the vendor proposal respond to this tender requirement?", **reference},
    true="Some passage of the proposal speaks directly to the subject of this "
         "requirement, whether or not the answer is adequate",
    false="The proposal never engages with this requirement, or touches the general "
          "topic only in passing without answering what was asked",
)
questions[f"committed_{index}"] = noul(
    {"task": "Does the proposal make a binding undertaking to deliver this "
             "requirement, as opposed to describing a capability?", **reference},
    true="The proposal states the vendor will do it, warrants it, or offers it as a "
         "contractual term the Authority could enforce",
    false="The proposal says only that it can do it, typically does it, has done it "
          "elsewhere, plans to, or would scope it later -- or says nothing at all",
)
```

Every question points at the *same* ingested state by path
(`` `requirements[3].text` ``, `` `proposal` ``), so a 14-requirement tender is
28 judgments in **one** request over a proposal read once. The naive shape —
one call per requirement — is 14 requests and 14 re-reads of the same document.

The other structural property: Jev **selects**, it does not generate. The
output is a probability per requirement, not a paragraph of prose about the
proposal. There is no route by which it invents a commitment the vendor did not
make.

## How it works

Per-requirement verdict, then a bid-level verdict, both pure Python:

```python
def classify(addressed, committed, accept, reject):
    if addressed <= reject:
        return "gap", f"no response to this requirement (P(addressed)={addressed:.2f})"
    if committed <= reject:
        return "weak", f"described but not undertaken (P(committed)={committed:.2f})"
    if addressed >= accept and committed >= accept:
        return "covered", "responded to and bindingly undertaken"
    return "weak", f"borderline (...)"
```

```
submittable  ⟺  every MANDATORY requirement is covered
```

Order matters in `classify`: an unanswered requirement is a **gap** whatever
the commitment Noul says, because there is nothing there to commit to.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

A synthetic 14-requirement local-authority casework tender and a synthetic
vendor response are bundled, so it runs before you supply anything. The
response is built with the traps a real one has: it answers most requirements
properly, gestures vaguely at two (notifications integration, product roadmap),
and is silent on two — one of which, BPSS clearance, is mandatory.

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

## Design notes

**Conjunctive with a veto, never a weighted average.** Ten excellent answers do
not compensate for one missing mandatory response — the bid is non-compliant
and the buyer does not get to exercise judgment about it. A weighted
"compliance score" would show 93% and hide exactly the row that disqualifies
you. The same reasoning as `research/paper-screener`: weighted scores suit
compensating preferences, and *"any mandatory omission disqualifies"* is not a
compensating preference.

**Three outcomes per requirement, not two.** `covered` / `weak` / `gap`.
Anything borderline lands in `weak` so a human reads it. A forced
compliant/non-compliant binary is what makes an automated checker untrustworthy
— and on a bid, the cost of reviewing a row you did not need to review is
minutes, while the cost of missing one is the whole tender.

**Optional requirements do not veto by default.** They are usually scored, not
gated, so blocking on them is a per-tender decision and lives behind a sidebar
checkbox rather than in the rule.

**Thresholds are sliders, not constants in the instruction text.** Retuning
accept/reject re-runs no inference — you move the slider and the same 28
probabilities are re-read. `test_app.py` asserts no numeric threshold has
leaked into a question.

## Limits

- **Requirement extraction is your job.** This takes a structured requirements
  list; it does not parse an ITT PDF into one. That is a document-conversion
  problem, and doing it badly here would corrupt everything downstream.
- **It reads the whole proposal for every requirement.** Fine at tender scale
  (tens of requirements, tens of pages). A 200-requirement framework against a
  200-page response will exceed `provider.context_tokens` — the app checks and
  refuses rather than silently truncating, but chunking by response section is
  not implemented.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** So it is never
  asked to. *"Does the SLA meet 99.9%?"* is not a question put to the model —
  the requirement text says 99.9%, the proposal says 99.95%, and the model is
  asked only whether the proposal makes a binding undertaking on availability.
  If you need the numeric comparison, extract both figures and compare them in
  Python.
- **It reads literally.** A requirement written as tender-speak
  (*"the supplier shall have regard to…"*) produces mush, because it is mush.
  Boundary cases belong in the `true`/`false` criteria, not in a longer
  requirement.
- **It degrades on large noisy state.** Annexes, pricing schedules and CVs
  pasted into the proposal box make every judgment worse. Feed it the technical
  response.
- **Not a compliance opinion.** It tells you which rows a human should look at,
  in what order. Somebody still signs the compliance declaration, and it is not
  this app.
