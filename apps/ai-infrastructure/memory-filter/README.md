# 🧠 memory-filter

Decide which stored memories belong in the context for *this* turn — ranking the
entire store in **one** request.

```bash
uv run --with httpx python app.py --turn t-1
```

```
turn t-1: Our Postgres primary keeps saturating the connection pool right after the nightly batch. What should I change first?

── kept (108/120 tokens) ──
   m-18  rank 1.87  omit-cost 2.71   28tok  The routing service's nightly batch job runs at 02:00 UTC and saturates...
   m-16  rank 1.74  omit-cost 2.60   30tok  The team's connection pooling is PgBouncer in transaction mode, which...
   m-09  rank 1.41  omit-cost 2.35   22tok  The routing service has a hard requirement that p99 latency stays under...
📌 m-10  rank 1.12  omit-cost 1.40   28tok  User dislikes being given three options and asked to choose; they want...

── dropped ──
   m-12  rank 0.94  no-budget   The routing service's read replicas are in eu-west-2 and the primary...
   m-03  rank 0.88  superseded  User's team standardised on Postgres 15 for all new services.
   m-06  rank 0.08  below-rank  User asked once about the best sandwich shop near Liverpool Street.
```

## The problem

Agent memory is now a standard feature and a standard disappointment. The store
fills up, and on every turn something has to decide which slice of it to paste
into the prompt.

The default answer is vector search: embed the turn, embed the memories, take
the top-k by cosine. It is fast and it fails in three specific ways.

**It cannot see supersession.** "We standardised on Postgres 15" and "we
migrated off Postgres 15 to 17" are near-identical vectors. Both retrieve, the
model reads both, and the advice comes back hedged or wrong.

**It cannot see standing preferences.** "Give me one recommendation, not three
options" is topically unrelated to every turn, so it never makes the top-k — and
it applies to every single reply.

**Similar is not useful.** "I found MongoDB migrations painful at a former
employer" is semantically adjacent to a database question and changes nothing
about the answer.

Adding a cross-encoder fixes the relevance ranking and none of the rest, and it
costs a scoring pass **per memory per turn**.

## Why this needs Jev

The whole store goes in as state **once**, and every memory's questions are
answered against it in parallel, in a single request:

```python
state = {"current_turn": turn["text"], "memories": memories}
answers = client.ask(state, build_questions(memories))
```

That is the structural property. A cross-encoder pipeline is `O(memories)`
scoring calls per turn; this is one call whose cost grows with the store's
*token* size, not with a per-pair multiplier. Twenty memories is 100 questions
in one round trip, at $0.042 per million input tokens with output free.

It is also the only way `superseded` can be asked honestly. Deciding whether a
memory is overridden requires seeing the **other** memories, which a pairwise
scorer structurally cannot do — it only ever sees one memory and the query.
Because the full store is in state, the question is answerable.

## How it works

Four Nouls per memory, because the properties are independent and pull in
different directions:

```python
PROPERTIES = {
    "relevant":            "Is this stored memory about the same subject as the user's current message?",
    "would_change_reply":  "Would a reply written without this memory be materially different?",
    "superseded":          "Does a later memory in this store replace what this one says?",
    "standing_preference": "Is this a standing instruction about how the user wants to be worked with...?",
}
```

A standing preference scores low on `relevant` and must be kept anyway. A
superseded fact scores high on `relevant` and must be dropped. One relevance
number collapses both cases; four thresholds keep them apart.

The Score asks what happens to the reply if the memory is left out — which is
the quantity the budget is actually trading against, and is not the same as
relevance.

Selection is pure Python, so every knob retunes for free:

```python
for row in ordered:
    if row["properties"]["superseded"] >= superseded_p:
        dropped.append({**row, "reason": "superseded"})
    elif row["rank"] < min_rank:
        ...
    elif used + row["tokens"] > budget_tokens:
        dropped.append({**row, "reason": "no-budget"})
```

Token estimates come from character length **in Python** — `jev-1.13` cannot
count, and it is never asked to.

Exit code 1 when a memory was dropped *only* because the budget ran out. That
distinguishes "the ranking worked and the budget is too small" from "nothing
else was worth keeping", which is the thing you need when tuning.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py                       # t-1, the database question
uv run --with httpx python app.py --turn t-2            # drafting a note to the manager
uv run --with httpx python app.py --turn t-3 --budget 60
uv run --with httpx python app.py --json | jq '.kept[].id'
```

Three turns ship with the sample, and they exercise different slices of the same
store: an infrastructure question, a writing task where the standing style
preferences matter more than the facts, and a lunch question that should keep
almost nothing — except the shellfish allergy.

## Design notes

- **Why `would_change_reply` is separate from `relevant`.** Relevance is about
  topic; this is about consequence. "The primary is in eu-west-1" is relevant to
  most database questions and changes the answer to very few of them.
- **Why standing preferences get a bonus rather than a bypass.** A hard bypass
  means every preference is pinned forever and the budget is gone before the
  facts get a look in. `--standing-bonus` lets them compete, and win when the
  budget allows.
- **Why supersession is checked before the rank.** A stale memory that ranks
  first is the most dangerous item in the store, not the most valuable one.
  Ordering the checks this way means a confident wrong memory is dropped rather
  than promoted.
- **Why the dropped list prints with reasons.** "Dropped for no budget" and
  "dropped as irrelevant" call for opposite fixes. Collapsing them into one
  count is how people conclude the ranking is broken when the budget is.

## Limits

- **`jev-1.13` cannot count, do arithmetic, or compare dates.** The token
  budget, the rank blend and the ordering are Python. Note especially that
  `superseded` is answered from the *content* of the memories, not from their
  `stored` dates — asking the model to compare timestamps is a documented
  failure mode. If your store has reliable timestamps, sort and de-duplicate in
  Python first; it is cheaper and exact.
- **One request has a ceiling.** The store must fit `provider.context_tokens`
  (TypeSafe 64k, OpenRouter 32k). A few hundred memories is comfortable; a
  hundred thousand is not. Past that, use a cheap recall stage to get to a few
  hundred candidates and run this over those — the same shape as
  `agent-tool-router`.
- **It degrades on large noisy state.** A store full of near-duplicate trivia
  makes every judgment worse, which is an argument for writing fewer, better
  memories rather than for a bigger budget.
- **Ranking is not writing.** Jev selects; it cannot generate. This app never
  rewrites, merges or summarises a memory — what it keeps is your own stored
  text, verbatim.
- **No conversation window.** Only the current turn is considered. "Do that for
  the other service too" will filter against a turn that does not name the
  service. Pass a short history as the `current_turn` text if that is your shape.
- **Calibrated is not correct.** These are probabilities over judgments about
  your own notes. A memory written ambiguously will be judged ambiguously.
