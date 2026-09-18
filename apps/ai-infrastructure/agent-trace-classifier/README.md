# 🔬 agent-trace-classifier

Read every failed agent run, not a sample of twenty, and find out which failure
is actually eating your week.

```bash
uv run --with httpx python app.py
```

```
── 14 failed runs, 6 distinct causes ──
  tool_error_ignored         4 runs    29% ████████
      run-1041, run-1048, run-1052, run-1053
      → fix: validate_tool_results (confidence 0.81)
        Make the agent check each tool result before using it, and stop the run on an error.
  empty_result_invented      3 runs    21% ██████
      run-1042, run-1049, run-1051
      → fix: forbid_unsourced_answers (confidence 0.74)

3 runs failed silently · 5 had an external root cause · 2 would plausibly pass on a retry
  silent failures: run-1041, run-1048, run-1052
```

## The problem

An agent in production generates thousands of runs a week and a few hundred
failures. The failures are the only data that tells you what to fix, and nobody
reads them.

The two things teams do instead both fail in the same direction. Dashboards
count runs by exit status, which tells you *that* 8% failed and nothing about
*why*. And someone spends a Friday reading thirty traces by hand, which produces
a real answer about those thirty and no idea whether they were representative.

The distinction that never survives either approach is the one that matters
most: **did the agent know it had failed?** A run that reports "I couldn't do
that" is a bad afternoon. A run that says *"Your refund of $42.00 has been
processed"* when the payment gateway timed out is a support ticket, a refund
that never happened, and a customer who finds out three weeks later. Both are
one row in the failure count.

## Why this needs Jev

Reading a trace is a judgment, not a pattern match — the same `403` is a
permissions bug in one run and correct behaviour in another, and you only know
which by reading what the agent did next. That has meant paying frontier prices
per trace, so teams sample.

At **$0.042 per million input tokens, output free**, a compact trace costs
around $0.00002 to classify on four dimensions. Every failed run, every day,
costs less than the dashboard that counts them.

The structural property is that the answer comes back as **structure, not
prose**: a cause label with a confidence, plus three independent probabilities.
That is what makes the reduce step possible. You cannot group, tally and rank
paragraphs of critique; you can group labels, and Python does the counting that
`jev-1.13` cannot.

## How it works

**Map.** One Choice over eight causes — seven real ones and `unclear` — plus
three Nouls that are deliberately *not* levels on the cause rubric, because they
cut across it. A `missing_permission` failure can be silent or loud, retryable
or not:

```python
questions[f"cause_{index}"] = choice(
    {"task": "What is the single root cause of this failed agent run?",
     "run": reference,
     "note": "Choose unclear when no option fits or the trace is too thin to tell."},
    CAUSES,
)
```

Traces are chunked against `provider.context_tokens`, so a large run set costs
several requests. **That is one logical request split by size, not a cascade** —
every chunk asks the same questions and no chunk depends on another's answers.

**Reduce.** Python groups and tallies:

```python
tally = Counter(row["cause"] for row in rows)
...
"exemplars": [m["id"] for m in sorted(members, key=lambda m: -m["confidence"])[:3]],
```

Then **one** final request asks which remediation would prevent each of the
largest clusters, with `none` available so it can say "these failed for
unrelated reasons".

That second request is a genuine second stage, on exactly the grounds
[`agent-tool-router`](../agent-tool-router) sets out: **the map answers decide
which traces get sent in the reduce call.** You cannot fold it into the first
request, because the first request's job is to determine what the second one
should look at. Nothing else here takes a second round trip.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --clusters 5 --alert-share 0.4
uv run --with httpx python app.py --json | jq '.clusters[] | {cause, size, remedy}'
echo $?                                   # 1 if one cause dominates the run set
```

The bundled sample is 14 traces spanning ignored tool errors, invented results,
retry loops, missing scopes, lost constraints, an ambiguous goal, and two runs
that genuinely do not fit a bucket — there to check that `unclear` gets used
rather than forced into the nearest label.

## Design notes

- **Why the three Nouls are not levels on the cause Choice.** "Did the agent
  notice?" is true or false independently of every cause. Folding it in would
  double the option list and make each option rarer and worse-estimated. Two
  questions, two thresholds, both tunable.
- **Why `unclear` is excluded from the alert.** A large `unclear` pile means the
  traces are too thin to classify — a logging problem, not a failure mode. Paging
  on it trains people to ignore the page.
- **Why exemplars are the most confident members.** The reduce step sees three
  traces per cluster, not all of them. Sending the ones the map stage was surest
  about gives the remediation question the cleanest possible picture of the
  class. Confidence here is distribution concentration, not correctness — see
  the limits.
- **Why `mean_confidence` is reported per cluster.** A cluster of 20 runs at
  confidence 0.4 is a label that is not really holding together, and it should
  not be read the same way as 20 runs at 0.9.
- **Trace shape.** `{id, goal, steps[], final_state}`. The `final_state` field
  is doing a lot of work: it is what separates "reported the failure" from
  "claimed success". If your traces do not have an equivalent, add one before
  wiring this up — the classification gets markedly worse without it.

## Limits

- **`jev-1.13` cannot count, do arithmetic, or compare dates.** Every tally,
  share, mean and ranking here is Python. A cause defined by counting ("called
  the same tool more than five times") has to be computed in Python and passed
  in as state, not asked.
- **One cause per trace.** Real failures are often two things at once — a flaky
  tool *and* a plan that had no fallback. The Choice forces the dominant one.
  If you need multi-label, that is a Noul per cause, which is a different and
  more expensive app.
- **Confidence is not correctness.** `confidence 0.81` says the distribution was
  concentrated, not that the label is right. Use it to order exemplars and to
  spot mushy clusters, never as permission to act.
- **The remediation is a selection from a fixed list.** Jev cannot generate, so
  `REMEDIES` is written by you. If the real fix is not in the list, the honest
  answer is `none` — and getting `none` back is a signal to extend the list, not
  a failure of the app.
- **It degrades on large noisy traces.** A 200-step trace full of irrelevant
  logging reads worse than a compact one. Truncate the middle and keep the last
  few steps plus the final state; the tail is where the cause shows.
- **It reads literally.** A trace whose `final_state` is vague gets a vague
  answer, usually `unclear`. Better trace hygiene improves this app more than
  any threshold change will.
- **No cross-run correlation.** Each trace is judged alone, so "these 40 runs all
  failed because of one deploy at 14:00" is not something this can see. Group by
  timestamp in Python first, then classify within the group.
