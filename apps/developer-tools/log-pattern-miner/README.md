# 🪵 log-pattern-miner

A wall of log lines in; a short list of named failure modes, with line volumes,
out.

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

## The problem

Log search answers questions you already knew to ask. The question you actually
have at 03:00 — *what is going wrong in here?* — has no query. You end up
scrolling, and the failure mode you have never seen before is the one you scroll
past, because it happens nine times and `request completed` happens 420.

Commercial log products do pattern extraction (Drain, Logreduce, the various
"patterns" tabs) and stop at the shape: they will tell you that
`pool exhausted size=<n>` occurs 33 times, and leave naming it to you. The
naming is the part that takes the judgment.

## Why this needs Jev

**Map-reduce is only affordable at this price.** The map step is per distinct
template, and the sample collapses 1,057 lines into 26 templates — roughly 40×,
and the ratio grows with volume, because production logs are overwhelmingly
repetition. Three judgments over a few hundred templates is a handful of
requests and a fraction of a cent, so you can run it over a whole day of logs
instead of a sampled window. Sampling is precisely the wrong move here: the
templates that matter are the rare ones.

```
MAP     regex → templates        (Python: exact, free)
        template → 3 judgments   (Jev: the only part that needs judgment)
REDUCE  sum line counts per mode (Python: jev-1.13 cannot count)
```

The model never sees a count and is never asked for one. It sees a line shape
and two example lines, and answers what kind of failure it is.

## How it works

```python
questions[f"mode_{i}"] = choice(
    {"task": "What kind of failure does this log line report?", "log": ref}, FAILURE_MODES)
questions[f"actionable_{i}"] = noul(
    {"task": "Could an engineer do something about this without more information than the line gives?", "log": ref},
    true="It names what failed specifically enough to start work",
    false="It is routine chatter, or so vague that the next step would be to go looking for a better log line")
questions[f"data_at_risk_{i}"] = noul(
    {"task": "Does this line suggest that data was lost, corrupted, or left in an inconsistent state?", "log": ref},
    true="Work was dropped mid-flight, a write half-applied, or a record could not be reconciled",
    false="The operation failed cleanly, or nothing was being persisted")
```

Normalisation is ordered regex substitution — timestamps, UUIDs, hashes, IPs,
paths, durations, then bare integers:

```python
a = normalise("03:00:01.220Z ERROR [db] pool exhausted size=20 waiting=7 timeout=30s")
b = normalise("04:11:59.001Z ERROR [db] pool exhausted size=40 waiting=51 timeout=30s")
assert a == b
```

## Design notes

**Why `data_at_risk` is its own Noul.** It cuts straight across the taxonomy. A
`resource_exhausted` line may be harmless back-pressure; a `timeout` may have
left a half-applied ledger write. That bit is what separates "investigate on
Monday" from "wake someone", so it gets its own threshold rather than being
inferred from the mode.

**Why `other` and `unclear` are both options.** They are different states and
they need different responses. `other` means *a real failure I cannot name from
this taxonomy* — extend the taxonomy. `unclear` means *this line does not say
enough* — fix the log line. The sample contains `ERROR [core] unexpected state`
precisely to exercise that. A Choice with neither would file both of them under
the nearest-looking mode with plausible confidence.

**Why the taxonomy is a dict in the app and not a prompt.** Editing
`FAILURE_MODES` is how you adapt this to your stack; the option descriptions
are what the model reads, so make them describe *your* systems.

## Limits

- **One line at a time.** A failure that only makes sense as a sequence —
  retry, retry, give up — is judged as three unrelated templates. Multi-line
  stack traces are especially affected: each frame may become its own template
  unless your logger emits them as one record.
- **Normalisation is a heuristic.** Too aggressive and two different failures
  collapse into one template; too lax and one failure splits into many. The
  sample splits a couple of shapes for exactly this reason. Tune `PLACEHOLDERS`
  for your log format.
- **The most frequent templates are judged first** and the rest are cut by the
  sidebar limit. That is backwards from what you want if the rare template is
  the interesting one — raise the limit, it is cheap.
- **No time reasoning.** "Started at 03:12" and "correlates with the deploy"
  need date comparison, which `jev-1.13` cannot do. Bucket by time in Python
  and cluster each bucket separately.
- **Volume is not importance.** The reduce step ranks by line count because
  that is what Python can compute exactly; nine lines about a lost job matter
  more than 420 about a successful request.
