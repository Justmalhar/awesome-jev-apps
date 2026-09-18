# 🔀 rag-reranker

A drop-in replacement for a cross-encoder or a hosted rerank API — shipped next
to the cosine baseline it replaces, so the comparison is the product.

```bash
uv run --with httpx python app.py
```

```
▸ How long do I have to return a laptop bought on the business plan?
  rank  cosine baseline        jev reranked           move
  1     q1-p1                  q1-p2                  ▲3
  2     q1-p3                  q1-p5                  ▲3
  3     q1-p10                 q1-p10                 ·
  top: Business plan hardware may be sent back for a full credit up to 45 days after it arrives
       relevance 2.91/3 · direct P=0.93 · different subject P=0.04 · score 1.43
  biggest demotion: q1-p1 (-3) -- different subject P=0.88
```

`q1-p1` is about laptop *accessories* and repeats "laptop" four times. Cosine
loves it. It is the wrong answer.

## The problem

Vector search returns ten passages ordered by embedding distance, and embedding
distance is a similarity measure, not an answer measure. The passage that shares
the most vocabulary with the question is very often about the neighbouring
thing: the consumer plan instead of the business plan, the import job instead of
the export job, the 2023 terms instead of the current ones. Meanwhile the
passage that actually answers the question says "sent back" where the user said
"return" and lands fourth.

So everyone bolts on a reranker. The options are a cross-encoder you host
yourself (a GPU, a model to keep current, latency you own), a hosted rerank API
(priced per search, another vendor, another region question), or an LLM
reranking prompt (works, costs roughly what generation costs, and returns prose
you have to parse into an order). All three collapse the judgment into **one
number**, which is why none of them can tell you that a passage is perfectly
relevant *and* about the wrong plan.

## Why this needs Jev

Relevance is not one quantity. This app asks three questions per pair and gets
back **three separately calibrated numbers**, which is a thing a similarity
score structurally cannot give you:

- how much of the answer the passage carries (a degree → a Score),
- whether a reader could answer from it alone (a fact → a Noul),
- whether it is about a different product, plan or version (a fact → a Noul).

That third signal is the one that pays for the app. A passage can be maximally
relevant-looking and completely wrong, and only a separate probability lets you
push it down *by a weight you choose* rather than hoping one blended score
happens to rank it correctly.

The price makes it a default rather than a decision. At **$0.042 per million
input tokens, output free**, a query with ten candidates and three judgments
each costs a small fraction of a cent. Reranking every query — not the slow
path, not the top-1% of traffic — stops being a budget conversation.

## How it works

One request covers every (query, passage) pair in the batch. Three questions per
pair:

```python
questions[f"relevance_{qi}_{pi}"] = score(
    {"task": "How much of the answer to the question does this passage carry?",
     "pair": reference},
    RELEVANCE_LEVELS,
)
questions[f"direct_{qi}_{pi}"] = noul(
    {"task": "Could a reader answer the question from this passage alone?",
     "pair": reference},
    true="The passage states the fact being asked for, not merely where to find it",
    false="The reader would still have to consult something else to answer",
)
questions[f"different_subject_{qi}_{pi}"] = noul(
    {"task": "Is this passage about a different product, plan, version or system "
             "than the question is asking about?", "pair": reference},
    true="It describes a neighbouring subject -- another plan, another product, "
         "a superseded revision, a different job or service",
    false="It is about the same subject the question names",
)
```

The ordering itself is Python:

```python
def combine(relevance, direct, different_subject, *, w_relevance, w_direct, w_different):
    normalised = relevance / (len(RELEVANCE_LEVELS) - 1)
    return w_relevance * normalised + w_direct * direct - w_different * different_subject
```

Every weight is a CLI flag. Retuning `--w-different` re-sorts every result with
no further inference, which is the whole point of getting numbers back instead
of an ordering.

`baseline.py` is a real TF-IDF cosine reranker in pure stdlib — no numpy, no
model, nothing to install. `app.py` runs both and prints the movement column, so
you can see which passages the cheap method got wrong on your own data before
you decide anything.

## Run it

```bash
cp ../../../.env.example .env        # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run --with httpx python app.py
uv run --with httpx python app.py --w-different 0.0      # switch the distractor penalty off
uv run --with httpx python app.py --queries mine.json --json > ranked.json
uv run --no-project python baseline.py                   # the baseline's own self-check
echo $?                              # 1 if a query had no good passage at all
```

Exit code 1 when a query's best passage scores below `--min-top`. That is a
**retrieval** failure, not a ranking one, and it is worth failing a CI run over:
reordering ten irrelevant passages produces a confident, well-ranked, wrong
answer.

## Design notes

- **Why relevance is a Score and directness is a Noul.** "How much of the answer
  is here" is a degree with four concrete rungs, from "a reader would skip it" to
  "states the answer outright". "Could you answer from this alone" is a fact.
  Putting the second one on the rubric would make the two impossible to weight
  apart.
- **Why the distractor Noul is separate and negative.** It is subtracted, not
  averaged, because it is a disqualifier rather than a component of quality. At
  `--w-different 0` you get a conventional relevance ranking, which is a useful
  ablation to run on your own corpus.
- **Why the baseline ships in the repo.** A reranker that is only compared
  against its own output proves nothing. The baseline is the good version of the
  cheap method — smoothed IDF over the candidate set — so beating it means
  something. Its self-check asserts that it *fails* the vocabulary trap, which
  is the behaviour the app exists to correct.
- **Rank movement is computed in Python.** The model is never asked where a
  passage moved, or by how much. It is never asked to compare two positions.
- **Batching** splits by query against `provider.context_tokens` (TypeSafe 64k,
  OpenRouter 32k). A query and its candidates always travel together.

## Limits

- **It reorders; it does not retrieve.** Nothing here can rescue a candidate set
  that never contained the answer. `--min-top` exists to tell you that happened
  rather than hiding it behind a tidy ranking.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** The weighted
  score, the rank movement, the cost per million pairs are all Python. "Which of
  these passages is the most recent" is not a question to put to it — sort by
  your own timestamps.
- **It reads literally.** `different_subject` catches what its criteria
  describe. If your corpus distinguishes subjects in a way that sentence does
  not cover (region codes, SKU suffixes, internal release names), say so in the
  criteria or it will not be seen.
- **It degrades on indirection and on large noisy state.** Each question points
  straight at one passage's text. A 4k-token passage whose relevant sentence is
  in the middle is a harder case than a chunked one; chunk before you rerank.
- **Latency is one round trip, not zero.** A local cross-encoder on a warm GPU
  can beat a network call on p50. This wins on cost, on setup, and on giving you
  three signals instead of one — not on being the fastest possible reranker.
- **No head-to-head accuracy claim is made here.** The app prints both orderings
  so you can judge on your own queries. Anyone quoting an NDCG figure they did
  not measure on your corpus is selling something.
