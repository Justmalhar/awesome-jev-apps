# 🔗 issue-deduper

Which of these issues are the same underlying bug — and what should the triager
do about each pair?

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

## The problem

Every repo over a thousand issues has the same two failures at once: the same
bug filed five times, and two reports closed as duplicates that were not.

Text similarity cannot fix it, and the bundled sample shows why. The single
most similar pair in it is:

- **#1388** "Crash on startup on Windows 11" — a 32-bit DLL next to a 64-bit binary
- **#1390** "Crash on startup on macOS Sonoma" — an unbundled `libssl.3.dylib`

Word overlap 0.60, the top of the list, and they are entirely different bugs.
Meanwhile:

- **#1412** "App shows a blank screen after logging in"
- **#1417** "Session token is dropped on the OAuth redirect"

score 0.34 and are one defect described from the user's end and the engineer's
end. No similarity threshold separates these two pairs, because the signal is
not textual.

## Why this needs Jev

**The Score's three levels *are* the three actions.** Not a rating that code
then has to interpret:

```python
ACTIONS = [
    "Two different defects. Closing either as a duplicate of the other would lose a bug report.",
    "Plausibly the same defect, but the two reports do not establish it; a triager should look at both.",
    "One defect reported twice. Closing the later report as a duplicate loses nothing.",
]
```

The answer lands *between* levels — 1.4 means "leaning duplicate, not certain" —
and `verdict()` cuts it into merge / human / keep-separate with two numbers you
can move without re-running anything. A model asked to output `"duplicate":
true` throws that gradient away, and the middle of the distribution is exactly
where a duplicate-closer does damage.

The three-way split matters more than the accuracy: the safe default for an
uncertain pair is *a human looks*, and that option has to exist in the answer
rather than being reconstructed from a confidence number afterwards.

## Blocking first, in Python

Judging every pair is quadratic. 14 issues is 91 pairs; 500 issues is 124,750.
So `difflib` blocks first — over **words**, not characters, because character
ratios on issue bodies are dominated by length:

```python
whole = difflib.SequenceMatcher(None, tokens_of(a), tokens_of(b)).ratio()
```

At the default floor the sample drops from 91 pairs to about 16, and the four
interesting ones all survive. Blocking is allowed to be sloppy — it only has to
be *recall-friendly*, since Jev decides and the top-ranked pair is one it gets
wrong.

## How it works

Two questions per surviving pair, batched into one request:

```python
questions[f"action_{i}"]      = score({"task": "Do these two issue reports describe the same underlying defect?",
                                       "pair": ref}, ACTIONS)
questions[f"a_is_richer_{i}"] = noul({"task": "Does report A contain reproduction detail that report B lacks?",
                                      "pair": ref},
                                     true="A gives steps, versions, logs, or an environment that B does not",
                                     false="B is as detailed as A, or more so")
```

The second answer decides which report survives the merge. It is independent of
the first — a pair can be duplicates with either report the better one — so it
is its own Noul and not a fourth level on the rubric.

## Design notes

**Why a Score and not a Choice.** The three outcomes are ordered: keep separate
< human decides < merge. A Choice would return one of them with a confidence
that conflates "the reports are ambiguous" with "the model is unsure", and you
could not tune the merge threshold independently of the separation threshold.

**Why blocking is tunable and judgment is not.** The similarity floor trades
tokens against recall and is worth sweeping on your own corpus. The levels are
fixed because they encode what your triage process *does*.

## Limits

- **Pairwise, not transitive.** Three reports of one bug produce three pairs,
  and nothing here builds the cluster. Union-find over the `merge` pairs in
  Python is the missing ten lines — deliberately left out, because merging a
  chain on one weak link is how good reports get closed.
- **Blocking can lose a true duplicate** whose two reports share no vocabulary
  at all. Lower the floor and pay more tokens; there is no free version of this.
- **Bodies are truncated to 900 characters.** A repro buried under a long stack
  trace may not reach the model.
- **No metadata.** Labels, components, reporters, and timestamps are ignored —
  `jev-1.13` cannot compare dates, so "filed within an hour of each other"
  belongs in Python if you want it in the blocking step.
- **Quadratic blocking.** Fine to a few thousand issues on a laptop. Past that,
  block on an index (labels, component, embedding neighbours) before `difflib`.
