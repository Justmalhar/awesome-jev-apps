# evals/

**Empty on purpose.** This is where reproducible benchmarks go, and nothing is
here yet because nothing has been measured yet.

## Why it exists as an empty folder

Every app in this repo prints its real cost and latency at runtime. That answers
*"what did this cost?"* but not the question that actually matters:

> Is Jev better than what I'm already using, on **my** data?

That needs a baseline, a dataset, and a metric — which is what belongs here, and
what this repo currently cannot honestly claim.

## What an eval must have

Anything added here needs all four, or it is marketing:

1. **A public dataset**, or a generator that produces one. A stranger has to be
   able to re-run it.
2. **A named baseline** still in the repo and still runnable — a cross-encoder,
   an embedding model, a frontier LLM, a regex, a human-labelled set. Not a
   remembered number.
3. **Cost, latency, and accuracy** for both sides. Cost is the whole argument;
   an eval that reports only accuracy is missing the point.
4. **The losses published too.** Jev will lose some of these. Publishing that is
   what makes the wins believable.

## Suggested first evals

Each maps to an app that already exists:

| Eval | Baseline | Metric |
|---|---|---|
| `rerank/` | BGE cross-encoder, Cohere Rerank, cosine similarity | top-1 / top-10 on a BEIR subset |
| `screening/` | human-labelled systematic review decisions | recall (the metric that matters), plus % auto-resolved |
| `tool-routing/` | all schemas stuffed into a frontier model's context | selection accuracy, tokens/turn, $/turn |
| `ci-classification/` | hand-triaged failure history | per-class precision/recall, and false "flaky" rate |

`screening/` is the most valuable and the hardest — recall matters far more than
precision there, and a tool that misses relevant papers is worse than useless.

## Structure

```
evals/<name>/
├── run.py         # runs both sides, writes results.json
├── baseline.py    # the incumbent, actually runnable
├── data/          # dataset or download script
├── results.json   # committed, with the model version and date
└── README.md      # method, numbers, and what it does NOT show
```

Pin the model version in results (`jev-1.13.0`, not `jev-latest`) and record the
date. Aliases move, and an unlabelled number becomes a lie the moment a new
release ships.

## Before you start

Read TypeSafe's [cookbooks](https://docs.typesafe.ai/llms.txt) — several are
already evals with published numbers, and reproducing one of those
independently is a more useful first contribution than inventing a new
benchmark.
