# Contributing

## The bar for a new app

An app gets merged if it clears all four:

1. **It is a workflow someone actually runs.** Not a benchmark, not a
   demonstration of an API call. If the honest description is "this shows you
   can classify things", it is not an app.
2. **It exploits something structural about Jev.** Parallel questions over one
   shared state, calibrated abstention, the ~700× cost delta, 150ms latency, or
   the fact that a model which cannot generate cannot invent a value. "An LLM
   app, but with Jev" is the thing this repo exists *not* to be.
3. **It runs from a cold clone.** `cd apps/your-app && uv run streamlit run app.py`,
   with sample data committed so it works before anyone signs up for anything.
4. **It is honest about cost.** Print real token counts and real dollars in the
   footer. Never estimate. If Jev loses on your workload, say so in the README —
   that is more valuable than another win.

## Structure

Apps are **standalone by design**. Someone should be able to copy one folder out
of this repo and have it work. That means:

```
apps/your-app/
├── app.py            # the whole app, ideally one file
├── jev_provider.py   # GENERATED — do not edit
├── providers.toml    # GENERATED — do not edit
├── pyproject.toml    # deps for this app alone
├── README.md         # what it does, why it needs Jev, design notes
└── sample_*.txt      # committed sample data
```

No repo-root imports. No shared framework. The client is **duplicated on
purpose** — that duplication is what makes a folder forkable, and it is
generated rather than hand-maintained:

```bash
# edit _shared/jev_provider.py, then:
python scripts/sync_provider.py
python scripts/sync_provider.py --check   # CI runs this
```

## Designing the questions

Read [TypeSafe's building guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
first. The mistakes that show up in review, in order of frequency:

**Asking one broad question instead of several narrow ones.** Split independently
useful dimensions. But do not shred a relationship into fragments that no longer
mean anything on their own.

**No escape hatch on a Choice.** A Choice *must* pick something. Without a
`"none"` / `"unclear"` option it will nominate the least-wrong answer with
perfectly reasonable-looking confidence.

**Score levels that are labels, not situations.** `["low", "medium", "high"]` is
not a rubric. Each level has to describe a concrete situation and stand on its
own.

**Treating a Noul like an intensity dial.** 0.5 means the model thinks yes and no
are equally likely — not "moderately". If you want degree, use a Score.

**Policy baked into the question.** Put thresholds and weights in Python. Then
tuning is free and the raw judgments stay reusable.

**Looping when you could batch.** Independent questions over the same state
belong in one request. A second request is only warranted when an answer is
needed to fetch new evidence or decide the next options.

## Things to not build

- Anything needing arithmetic, counting, or date comparison — documented failure
  modes. Extract in Jev, compute in Python.
- Chatbots. Jev cannot generate.
- Resume screeners, generic sentiment classifiers, generic moderation demos.
  Crowded, generic, and they make the repo look like every other one.

## Pull requests

One app per PR. Include a screenshot or a terminal capture in the description
with the real cost footer visible.
