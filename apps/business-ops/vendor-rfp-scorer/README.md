# 📋 Vendor RFP Scorer

Score supplier RFP responses against weighted criteria. Re-negotiate the
weighting as often as the panel wants — the scoring re-runs for nothing.

## The problem

A mid-size RFP is five vendors × sixty questions of prose, and the evaluation
model is a spreadsheet that gets argued about. Finance wants commercial at 30%.
Security insists their section is a gate, not a weight. The business owner
thinks implementation risk is under-weighted, and the CFO asks late on what the
ranking looks like if you halve the integration weight.

Every one of those questions currently means re-reading the responses, or —
much more commonly — nobody answers them, and the model that shipped is the one
the first person typed in.

The scoring is also where procurement disputes start. "Why did we score you a 3
on data residency?" needs an answer that points at the page, not at a total.

## Why this needs Jev

**The reading and the weighting are separable, and only the reading costs
money.** Each criterion gets one Score describing how well the response
addresses it. Those judgments are evidence about the document and they do not
change when the panel changes its mind about importance. So the model is
Python:

```python
score = 100 * Σ(weight × quality / 4) / Σ weights
```

Every weight in the sidebar rebuilds the ranking with **zero API calls**. The
CFO's what-if is a slider drag, in the meeting.

**Mandatory requirements are gates, not weights.** They are Nouls with their own
threshold, and a vendor who fails one is ranked last regardless of score. This
is the difference between a scoring model and a compliant one: in a weighted
average, a vendor with no EU data residency makes it up on price. The offline
check asserts a perfect-scoring vendor that fails a gate ranks below a
zero-scoring vendor that passes.

One request: 5 vendors × (8 criteria + 3 gates) = 55 judgments.

## How it works

```python
questions[f"q_{key}__{index}"] = score(
    {"task": "How well does this vendor response address the stated requirement?",
     "vendor_response": f"`responses[{index}].sections.{key}`",
     "requirement": spec["requirement"]},
    QUALITY_LEVELS)

questions[f"m_{key}__{index}"] = noul(
    {"task": "Does the vendor response satisfy this mandatory requirement?",
     "mandatory_requirement": spec["mandatory"],
     "vendor_response": f"`responses[{index}].sections.{key}`"},
    true="The response states plainly that this is satisfied today",
    false="The response is silent, hedged, conditional, or places it in the future")
```

The quality rubric is the thing that makes this useful:

```
0  The response does not address this requirement at all
1  The response acknowledges the requirement but describes no capability
2  The response claims the capability in general terms with no specifics
3  The response describes how it meets the requirement with named specifics
4  The response describes how it meets the requirement and cites evidence, references, or measurements
```

Level 2 is where most RFP prose lives, and separating it from level 3 is the
entire job of an evaluator.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five synthetic RFP responses ship with it, written in the register these
documents are actually written in — including one that is pure confident
nothing ("we follow industry best practices and align with ISO 27001
principles"), one with a roadmap connector dressed as a capability, and one
strong vendor whose backups leave the EU.

## Design notes

**Why one generic quality rubric instead of per-criterion rubrics.** Every
criterion is judged on the same axis — did they answer it, and with what. That
makes the scores comparable across criteria, which is what a weighted sum
requires. Per-criterion rubrics would each need their own 5 levels and the
weights would stop meaning anything.

**Why the gate wording is about hedging.** The `false` criterion names the
actual failure mode in vendor prose: "silent, hedged, conditional, or placed in
the future". "Our Workday connector is on our roadmap and we would be happy to
prioritise it" is a no, and it has to be written down as a no.

**Why gates are per-criterion and optional.** Only three of the eight criteria
have one. A gate on every criterion turns an RFP into a pass/fail checklist and
you lose the discrimination the scoring was for.

**Why compliance sorts before score.** `sort_values(["compliant", "score"])`.
A non-compliant vendor stays visible — you can see what you would be giving up
by waiving a requirement, which is a real procurement decision — but it never
sits at the top of the table where someone might read it as the winner.

## Limits

- **It scores the response, not the vendor.** A supplier with a weak product and
  an excellent bid-writing team will score well. That is also true of your human
  panel, but this one is faster at being wrong.
- **Prose quality is not capability.** Level 4 means "cites evidence", not
  "the evidence is true". Verify claims in due diligence; nothing here checks a
  certificate number.
- **No arithmetic, no price comparison.** The model is never asked to compare
  EUR 318,000 with EUR 392,000, or to work out a five-year total. It cannot do
  arithmetic. Put the commercial modelling in a spreadsheet where it belongs —
  the `commercial` criterion scores *transparency of pricing*, which is a
  different and genuinely readable property.
- **Section structure matters.** Each criterion points at one named section of
  the response. Real RFP answers are PDFs where the residency answer is
  scattered across three sections; splitting them is your ingest problem and it
  is the hard part of deploying this.
- **`jev-1.13` reads literally and degrades on large state.** Feed it the
  relevant section, not the whole 90-page bid.
- **This informs a panel; it does not award a contract.** In regulated public
  procurement, scoring methodology and human accountability are legal
  requirements.
