# 🧑‍⚖️ Peer Review Router

Match submissions to reviewers by expertise, at conference scale, with conflict
of interest as a separate veto.

## The problem

Bidding works for the reviewers who bid. For everyone else — which at most
venues is most of the programme committee and a long tail of submissions — a
chair assigns by reading titles at 1am. That is how a paper on secure
aggregation reaches three reviewers who work on HCI, and how the one person in
the world qualified to review it gets three papers on diffusion models.

The volume is the problem: 800 submissions × 200 reviewers is 160,000 pairwise
judgments, and every one of them is independent.

## Why this needs Jev

Two things are decided per pair, and they are **not the same kind of thing**:

```python
questions[f"fit_{s}_{r}"] = score(..., FIT_LEVELS)  # ordered: a ranking key
questions[f"coi_{s}_{r}"] = noul(...)               # binary, independent, a VETO
```

Expertise fit is ordered — from "outside every area this reviewer lists" to
"core methods and application area both appear in their recent work" — so it is
a Score, and a Score lands between levels, which is what a ranking key needs.

Conflict of interest is not a low level of expertise. It is orthogonal, and it
is a veto. Note what a blended "suitability score" would do: **the conflicted
reviewer is usually the best-qualified one**. Their fit is 3.0 precisely because
they work with the authors. Averaging fit and conflict assigns the paper to
exactly the person who must not review it, at high confidence. The blocked-pairs
table in the UI is sorted by fit descending to make this visible.

The Python policy:

```python
hard conflict, or P(conflict) ≥ threshold  →  blocked, whatever the fit
fit < floor                                →  not assigned
otherwise                                  →  greedy on fit, under a load cap
```

### Exact conflicts never reach the model

Shared affiliation and co-authorship are **string comparisons**, so
`hard_conflict()` settles them in Python before a single question is built.
Three reasons, in order of importance:

1. **A chair has to explain an exclusion.** "Your institution string matches an
   author's" is an explanation. "The model thought so" is not, and the excluded
   reviewer *will* ask.
2. It is exact where a probability is not.
3. It removes those pairs from the request entirely — they cost nothing.

The Noul is asked only about what actually needs reading: a described recent
collaboration, a joint grant, a shared student, a supervisory relationship. The
bundled sample has one of each, including a reviewer whose declared text reveals
a joint student that no affiliation check would catch.

## Scale

Pairs are packed into as few requests as `provider.context_tokens` allows
(`plan_batches`, greedy, reads the provider config rather than hardcoding 32k or
64k). This is batching for **size**, not a dependency chain — every question is
still independent and nothing waits on anything.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Eight synthetic submissions and ten synthetic reviewers are bundled, built so
that every mechanism fires: a reviewer who is an author of a submission, two
same-institution pairs, a declared co-author relationship, a joint-student
relationship visible only in prose, a reviewer whose expertise matches nothing
in the pool, and a load cap that binds.

## Design notes

- **Greedy, not optimal.** A min-cost flow would produce a better matching and
  an unexplainable one. "The highest-fit unconflicted reviewer with remaining
  capacity" is something a chair can defend to a PC member. The tie-break is
  deterministic so re-running gives the same allocation.
- **Shortfalls are reported, never resolved by relaxing a veto.** A submission
  that cannot be filled without breaking a conflict rule goes to the chair. A
  tool that quietly lowers the bar to fill a slot is worse than no tool.
- **The COI Noul is asked about the *reviewer's own declared text*.** It reads
  what the reviewer wrote about their relationships; it does not infer social
  graphs it cannot see.
- **Every slider is Python-side.** Re-allocating under a different load cap or
  conflict threshold re-runs no inference — and a hard conflict survives every
  slider position, because it is not a threshold.

## This assists a human researcher

It produces a draft allocation for a chair to approve, not a final one. Conflict
declaration remains the reviewer's responsibility and the venue's process;
nothing here replaces the declaration step, and a conflict nobody wrote down
anywhere is invisible to any tool.

**Recall matters more than precision** in the conflict direction specifically:
a false conflict costs one reassignment, a missed conflict costs the integrity
of a review. The default threshold (0.45) is deliberately low — lower than you
would set for a fit judgment — and should be lowered further, not raised, if the
venue is high-stakes.

## Limits

- **Abstracts and self-declared expertise only.** No publication graph, no
  citation overlap, no DBLP lookup. A reviewer whose expertise blurb is three
  years stale gets matched to three-year-old interests.
- **No institutional-hierarchy conflicts.** Same funder, same department under a
  different name, same spin-out — none of these are caught by string identity,
  and only some appear in prose.
- **Greedy allocation is order-sensitive at the margins.** Papers considered
  later compete for reviewers already loaded. The load cap bounds the damage;
  the shortfall table shows where it bit.
- **`jev-1.13` cannot count.** Reviewer loads, quotas and pair totals are all
  tallied in Python. The model is never asked how many papers anyone has.
- **Costs scale with pairs, not papers.** Pre-filtering by topic area in Python
  before routing is the obvious optimisation for a genuinely large venue, and is
  left to the caller.
