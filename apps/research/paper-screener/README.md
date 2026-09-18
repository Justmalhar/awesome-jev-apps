# 📄 Paper Screener

Title/abstract screening for systematic reviews, against a real protocol.

Screening is the part of a review that eats weeks. Two reviewers independently
read every abstract against inclusion and exclusion criteria, a third resolves
disagreements, and the overwhelming majority of abstracts are obvious rejects
that still had to be read twice.

This app sorts them into three piles — **include**, **exclude**, **needs a
human** — and reports how much reading it just removed.

## Why this needs Jev

### Veto rules are not weighted scores

This does **not** ask *"should we include this paper?"* One broad question would
be a black box you cannot audit or tune, and reviewers cannot defend it.

Instead, every criterion in the protocol is its own Noul, and the protocol is
applied in Python:

```python
include  ⟺  every inclusion criterion holds  AND  no exclusion criterion fires
```

That is conjunctive with a veto. A weighted average would let three strong
inclusions outvote one fatal exclusion — an animal study with beautiful
methodology would sail through. Weighted scores suit compensating preferences;
*"any serious violation disqualifies"* needs separate conditions.

Independent labels also stay independently tunable. If your protocol's RCT
criterion is too strict, you move that one threshold and nothing else shifts.

## Three piles, not two

A forced include/exclude binary is what makes automated screening untrustworthy.
Anything that isn't clear-cut lands in the human pile:

| Condition | Verdict |
|---|---|
| Any exclusion fires (P ≥ accept) | exclude, naming the criterion |
| Any inclusion clearly unmet (P ≤ reject) | exclude, naming the criterion |
| All inclusions met, no exclusions fire | include |
| Anything else | **needs a human** |

Every verdict cites the criterion and probability that drove it, so a reviewer
auditing the output sees *why*, not just *what*.

## Run it

```bash
cp ../../.env.example .env
uv run streamlit run app.py
```

Ten synthetic abstracts are bundled, built around a plausible protocol
(*"Does structured exercise reduce HbA1c in adults with type 2 diabetes?"*).
They include the traps a real search returns: a meta-analysis, a study protocol,
an animal model, a type 1 cohort, and a trial that never measures the outcome.

## Thresholds are not universal

The accept/reject sliders default to 0.75 / 0.25. **Tune them on a labelled
pilot set from your own review**, which is standard practice for screening tools
anyway. The right operating point depends on your tolerance for missing a paper
versus reading more of them — and in screening, recall matters far more than
precision, so bias toward the human pile.

## Honest limits

- **Screens titles and abstracts only.** Full-text screening is a different job
  and this does not attempt it.
- **Not a replacement for dual screening.** Use it as one reviewer, or as a
  pre-filter ahead of two. Publication standards require human sign-off.
- **`jev-1.13` reads literally.** "Adults" needs to be stated in the abstract;
  it will not infer adulthood from context. Write criteria the way you would
  write them for a new research assistant, and put boundary cases in the
  `true`/`false` criteria text.
