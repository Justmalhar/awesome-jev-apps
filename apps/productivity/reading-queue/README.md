# 📚 Reading Queue

Rank a large read-later pile by what you need **now** — the whole queue in one
request.

## The problem

Read-later apps are where articles go to die. The pile grows monotonically
because saving costs one second and reading costs thirty minutes, and the only
ordering any of them offers is *when you saved it* — which is the one dimension
that has nothing to do with what you should read next.

The pile is not uniformly worthless. Somewhere in those 200 items are the three
that bear directly on what you are stuck on this week. Finding them means
reading 200 excerpts, which is the problem you were trying to avoid. So people
declare bankruptcy, delete everything, and start a new pile.

What is missing is not summarisation. It is *ordering against a need that
changes every week*.

## Why this needs Jev

- **The whole pile, one request.** 20 items × 5 judgments = 100 questions in a
  single call. At 200 items it is 1,000 questions, still one call (the app
  splits only when the queue genuinely exceeds `provider.context_tokens`, which
  it reads from config and never hardcodes). A chat model would be 200 round
  trips or one enormous prompt with a fragile JSON contract.
- **Judge once, re-rank forever.** "How much working detail does this contain?"
  does not change when you decide you have 20 minutes instead of 90. Every
  slider and the time budget recompute the session for $0.00.
- **It never paraphrases.** Jev selects; it cannot generate. The excerpt you
  read is the one you saved. A summariser that quietly drops the caveat in
  paragraph six is worse than no tool.

## How it works

Five judgments per item, all independent, all in one batch:

```python
relevance = score("How closely does this match what the reader needs right now?", [4 levels])
depth     = score("How much working detail does this contain?", [4 levels])
decides   = noul("Would reading this change a decision the reader is about to make?")
evergreen = noul("Will this still be worth reading long after the news cycle it came from?")
reference = noul("Is this reference material to consult when needed rather than read end to end?")
```

`decides` and `evergreen` are deliberately separate Nouls rather than levels on
one rubric, because they are orthogonal and you threshold them differently. A
release-notes page is urgent-and-perishable; a paper on MVCC is neither urgent
nor perishable. Collapsing both into "priority" loses exactly the signal that
tells you what to *drop* rather than defer.

`reference` earns its place by catching the thing that corrupts every ranking:
a 6,800-word changelog scores high on relevance and depth and is not something
you *read*. Parked, not ranked.

Reading time and session packing are Python:

```python
def minutes_to_read(item):  return max(1, round(item["words"] / WORDS_PER_MINUTE))
def fill_session(ranked, items, budget):  # greedy, best-first, take what fits
```

The model is never asked how long anything takes, nor to compare dates.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_queue.json` — 20 saved items for someone rewriting a
Postgres ingest pipeline, salted with the things that really accumulate in a
pile: a job post, a survey results page, a CSS article, and a release-notes page
that is reference material pretending to be an article.

Your own pile: export from Pocket/Instapaper/Readwise to JSON with
`title, source, url, saved, words, excerpt`.

## Privacy

Titles, sources and saved excerpts of every queued item, plus the sentence
describing what you are working on, are sent to whichever provider
`providers.toml` selects.

## Design notes

- **Why relevance is a Score and `decides` is a Noul.** Relevance is a degree
  with four distinguishable situations. "Would this change a decision" is a
  fact about the world that is either true or not — a Noul at 0.5 means
  genuinely uncertain, not "somewhat".
- **Why the session is greedy, not a knapsack.** Optimal packing would swap a
  35-minute top-ranked piece for two mediocre 15-minute ones. You want the best
  thing first, then whatever still fits. Greedy *is* the correct policy here.
- **Why weights are sliders.** "What I need now" changes weekly; the weighting
  between relevance and depth changes by mood. Neither should cost a request.

## Limits

- **Excerpt-deep, not article-deep.** Judgments are made on the title, source
  and stored excerpt. An article whose excerpt is a subscription prompt judges
  like a subscription prompt. Fetching the full text before judging would fix it
  and is the obvious next contribution.
- **No date arithmetic.** `jev-1.13` cannot compare dates, so it is never asked
  whether something has gone stale — it is asked whether the substance is the
  *kind* that dates, and Python owns anything involving the calendar.
- **No deduplication.** Two articles covering the same benchmark both rank; the
  app does not compare items to each other. Pairwise comparison across a 200-item
  pile is a different app (see `note-linker`).
- **Word counts are yours.** If your exporter does not provide `words`, the
  session packer has nothing to work with and everything reads as one minute.
