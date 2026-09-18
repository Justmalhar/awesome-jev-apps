# 📰 Feed Ranker

Describe what you care about in one sentence. Get today's Hacker News front page
in *your* order.

> *"I build AI developer tooling. I care about inference cost, local models,
> compilers, and databases. I do not care about crypto, startup drama, hiring
> posts, or US political news."*

## The two things this app is actually demonstrating

### 1. Batch everything into one request

30 stories × 3 rubrics + 1 junk filter = **120 questions in a single call.**
Questions run in parallel against a state that is ingested once, so this is not
30 requests and not 120 — it is one. TypeSafe's own benchmark puts batching at
12.2× cheaper and 10× faster with identical answers; the footer in this app
prints what your run actually cost so you can check that claim yourself.

### 2. Score once, reweight forever

This is the part worth internalising. The sliders in the sidebar re-rank the
entire feed **instantly, with zero API calls.**

```
Jev returns raw judgments   →   your code applies policy
relevance: 2.7 / 3              total = Σ (weight × normalised score)
depth:     1.9 / 3
novelty:   1.2 / 2
junk:      P = 0.03
```

Changing a weight does not change the evidence or the meaning of the questions,
so there is nothing to re-infer. Most LLM pipelines re-prompt on every tuning
change because the policy is baked into the prompt. Keep the judgments raw and
tuning becomes free.

That separation also means these scores are just **numbers**. Log them, and
they become features for a classical model — which is what `ml-features/` does
with the same idea.

## Run it

```bash
cp ../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

## Design notes

**Why Score and not Noul for relevance.** Relevance is a degree along a
described dimension, not a yes/no. Each level describes a concrete situation
("adjacent — same broad field, not what they asked for") rather than a bare
label, because levels have to stand on their own to be usable.

**Why a separate junk Noul.** "Is this a hiring post" is an independent
property, not a point on the relevance rubric. Collapsing it into relevance
would destroy your ability to threshold it separately — and the threshold is
exactly what you want to tune.

**Scores are normalised before weighting.** The rubrics have different level
counts (4, 4, 3), so raw scores are not comparable. Dividing by `len(levels)-1`
puts everything on 0–1 so the weights mean what they look like they mean.

## Swap the feed

`fetch_front_page()` is 15 lines and returns `[{title, url, points, comments}]`.
Point it at your RSS reader, arXiv, a subreddit, or your company's PR queue and
nothing else changes.
