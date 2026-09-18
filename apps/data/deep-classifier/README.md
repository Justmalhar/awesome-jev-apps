# 🌳 Deep Classifier

Files items into a 3-level taxonomy using **beam search over Choice
probabilities**. Three requests, however many items.

## The problem

Every marketplace, retailer and support desk has a taxonomy several levels deep.
The bundled one is small — 4 → 12 → 36 — and real ones run to thousands of
leaves. Two ways people classify against them, and both are broken:

**Flat.** One classifier over all 36 leaves. Options that differ only in their
parent (`mens_outerwear` vs `womens_outerwear`, `cookware` vs `camp_cooking`)
compete directly, the distribution spreads thin across near-synonyms, and it
degrades as the taxonomy grows — which it always does.

**Greedy descent.** Walk the tree, pick the best branch at each level. Far
better discrimination, because at each step you are choosing between 3 or 4
genuinely distinct options. But it has one catastrophic property: **a wrong turn
at level 1 is unrecoverable.**

> `Meridian bib shorts, men's, black — Italian lycra, chamois pad, silicone leg grippers`

Level 1: `apparel` 0.55, `sports_and_outdoors` 0.35. Greedy takes `apparel`, and
`sports_and_outdoors > cycling > cycle_clothing` — plainly the right leaf — is
now unreachable. The level-2 evidence that would have settled it (`cycling` at
0.95) is never collected, because the branch was pruned before anyone asked.

This is not a model failure. It is a **search** failure, and search failures have
search fixes.

## Why this needs Jev

Beam search needs three things from a classifier, and Jev is one of very few
things that gives you all three:

1. **A full calibrated distribution, not a label.** `expand()` multiplies
   `P(branch)` into a running score for *every* branch, not just the winner. A
   text LLM returns `"apparel"` — to get the distribution you are scraping
   logprobs and praying the tokenizer agrees with your option names. A Choice
   returns `{"apparel": 0.55, "sports_and_outdoors": 0.35, ...}` because that is
   what the primitive *is*.
2. **Batching across beams.** Every beam alive at a level is an independent
   question over the same state, so all of them go in **one** `ask()`. Keeping
   k=3 beams alive does not multiply your request count — it multiplies the
   question count inside three requests. The whole 3-level search over any
   number of items is 3 round trips.
3. **A price that makes k>1 a non-decision.** Beam width 3 costs roughly 3× the
   questions of greedy, and input is $0.042/M with output free. Tripling a
   fraction of a cent to make level 1 recoverable is not a trade-off anyone needs
   to think about.

If you take one thing from this app: the reason beam search is normally
impractical over an LLM is not that the algorithm is hard. It is that LLMs give
you a token where the algorithm needs a probability, and a bill where the
algorithm needs a batch.

## How it works

```
level 1:  1 question  per item      -> expand, keep top-k per item by cumulative P
level 2:  k questions per item      -> expand, keep top-k per item by cumulative P
level 3:  k questions per item      -> the winner is the highest-scoring full path
```

One Choice per surviving beam, all in one request:

```python
questions[f"beam_{index}"] = choice(
    {"task": "Which of these branches does this item belong under?",
     "item": f"`items[{beam.item}]`",
     "parent_category": " > ".join(beam.path),
     "note": "Judge only against the branches listed. The item has already been "
             "placed under the parent category named above."},
    branch_options(node),     # this node's children + none_of_these
)
```

That `note` matters. `jev-1.13` reads literally, and without it the question
"which branch does this belong under?" invites the model to re-litigate the
parent — which is the caller's job, not the question's.

Scoring is a product, done in Python:

```python
expanded.append(Beam(beam.item, beam.path + (name,), beam.probability * probability))
```

`0.35 × 0.95 = 0.3325` beats `0.55 × 0.40 = 0.22`. The bib shorts end up in
`cycle_clothing`. That single multiplication is the entire mechanism — the model
is never asked to compare, multiply or rank anything. It could not do it if you
asked.

### The no-match option is load-bearing here

`none_of_these` is not politeness. It is how a beam **terminates early**:

```python
if name == NO_MATCH:
    expanded.append(Beam(beam.item, beam.path, beam.probability * probability, open=False))
```

The path stops where it is, keeps its accumulated probability, and still
competes. So an item that genuinely belongs at `home_and_garden` but under no
listed sub-branch surfaces as a *partial* classification rather than being
shoved down to whichever leaf lost least. Without it, a Choice must return one
of the children, and it will — at entirely plausible confidence. Every level of
every branch gets one.

### Pruning is cost control, and it is yours

```python
survivors = [beam for beam in ranked[:width] if beam.probability >= floor] or ranked[:1]
```

`width` and `floor` are sidebar sliders. Both live in Python, so tuning them
changes what the *next* level costs, and nothing about the questions already
asked. The `or ranked[:1]` guarantees no item ever falls out of the search
entirely, whatever the floor is set to.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: a 36-leaf product taxonomy and 28 products, including the traps this
app exists for — cycling bib shorts and gloves (apparel vs cycling), a shower
speaker (electronics vs home), a camp stove (small appliance vs camp cooking),
everyday trainers (footwear vs fitness), a leaf blower (garden power tool vs
nothing sensible elsewhere), and a dry bag that could go several ways.

The results table shows each item's winning path, its runner-up and the margin
between them. The footer reports how many items beam search pulled onto a
different level-1 branch than greedy would have taken — set `k=1` and re-run to
watch them get misfiled.

## Design notes

**Why a Choice per level and not one Score.** The levels of a taxonomy are not
ordered and they are not a rubric. `electronics` is not "more" than `apparel`.
A Score would be a category error.

**Why cumulative probability, not the last level's confidence.** Confidence at
level 3 tells you the model found *that* question easy. It says nothing about
whether level 1 sent you down the right tree. The product is the only number
that carries evidence from the whole path, which is exactly why the auto-file
threshold reads it.

**Why the default threshold is 0.35 and not 0.8.** Cumulative probability is a
product of three numbers below 1, so it is small by construction — `0.9³ = 0.73`
is an *extremely* confident path. Do not port a threshold from a flat
classifier; it will reject everything.

**Why the state is re-sent every request.** Each level sends all items again.
That is the honest cost of the design, and it is why fewer, fatter requests
matter: `split_questions()` packs as many beams into one request as
`provider.context_tokens` allows, rather than splitting eagerly.

## Limits

- **Beam search does not fix a bad taxonomy.** If `trainers` genuinely belongs
  under both `apparel > footwear` and `sports_and_outdoors > fitness`, no width
  of beam resolves that. It will split the probability and both paths will score
  low — which at least surfaces the ambiguity in the review queue instead of
  hiding it.
- **Single-label only.** One item, one path. Real catalogs frequently need
  multi-label placement. That is a different app: independent Nouls, one per
  category, not a search.
- **Cost grows linearly in k and in depth.** Width 3 over 3 levels is
  `1 + 3 + 3 = 7` questions per item, against greedy's 3. Fine at 28 items and
  fine at 28,000; watch the footer before you assume it is fine at 28 million.
- **The floor can prune the right answer.** A branch that scores 0.005 at level
  1 and would have been vindicated at level 2 is gone. The default floor of 0.01
  is deliberately low, and a 0.0 floor is available; the cost is that you carry
  dead beams into the next level.
- **`jev-1.13` degrades on large state full of irrelevant detail.** Items with
  long marketing copy are worse than items with a title and a spec line. Trim in
  pandas before you send.
- **Confidence is not correctness.** A cumulative 0.8 means three easy
  questions, not a verified filing. Sample the auto-filed tier before trusting
  it against a live catalog.
