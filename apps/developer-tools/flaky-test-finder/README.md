# 🎲 flaky-test-finder

Which of your red tests are flaky, and which are just broken? The dashboard
shows the same colour for both.

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

## The problem

A quarantine list is the standard remedy for a flaky suite, and building one is
where every team stalls. Failure rate alone does not identify a flake:

- `test_expires_after_ttl` fails **100%** of runs. Not flaky. A regression
  nobody has looked at since Tuesday.
- `test_rounding_property` fails **3%** of runs, always with a different
  falsifying example. Not flaky either — that is a genuine rounding bug in the
  money code, and quarantining it buries the most valuable signal in the suite.
- `test_chart_renders` fails **10%** of runs on a 5-second selector timeout.
  That one is a flake, and it is the test's fault, not the app's.

Telling these apart means reading the failure messages and judging *why* they
differ. Nobody does that across 500 tests every week, so suites get blanket
`--reruns 3` and the real intermittent bugs disappear.

## Why this needs Jev

**Per-test judgment at a price that lets you run it over the whole history.**
Three judgments across 10 tests is 30 questions in one request, well under a
cent. At frontier-model prices you would sample, and sampling is precisely
what fails here — the interesting tests are the rare ones.

The second structural property is the division of labour. The model is asked
*why the failures look the way they do*. Every **rate is computed in Python**:

```python
@property
def failure_rate(self) -> float:
    return self.failures / self.runs if self.runs else 0.0
```

`jev-1.13` cannot count and cannot compute rates — documented failure modes.
It is never shown the run tallies, only the deduplicated distinct failure
messages, because large state full of repetition measurably degrades accuracy.

## How it works

Python groups run records per test, computes rates, and deduplicates the
messages down to at most six distinct ones. Then three judgments per test, all
batched into one request:

```python
questions[f"mechanism_{i}"] = choice(
    {"task": "What produced these failures?", "test": ref}, MECHANISMS)
questions[f"self_inflicted_{i}"] = noul(
    {"task": "Would fixing this require changing the test rather than the code under test?",
     "test": ref},
    true="The test's own setup, teardown, waiting, or assumptions are what is wrong",
    false="The application code is what is wrong, or the messages do not distinguish the two")
```

`classify()` then combines the Python rate with the model's cause:

| rate | mechanism | test at fault | verdict |
|---|---|---|---|
| > 0.85 | anything | anything | **broken** — fix the code |
| 0.05–0.85 | a flake mechanism | ≥ 0.6 | **quarantine** |
| 0.05–0.85 | a flake mechanism | < 0.6 | **intermittent bug in the product** |
| 0.05–0.85 | `unclear` | — | **needs a human** |
| < 0.05 | anything | anything | **watch** |

## Design notes

**Why `self_inflicted` is a separate Noul.** It is orthogonal to the mechanism:
a timing race can live in the test's `sleep(1)` or in the application's event
loop, and they produce the same class of message. That single bit is the
difference between quarantining a test and filing a P2 — so it gets its own
threshold rather than being folded into the taxonomy.

**Why `deterministic_failure` is an option on the mechanism Choice.** Without
it, a consistently failing test gets assigned the least-wrong flake mechanism
with entirely plausible confidence, which is the one mistake this tool cannot
afford: it would quarantine a real regression. `unclear` covers the other
escape — logs that say `exit code 1` and nothing else.

**Why the thresholds are sliders and not instructions.** Rates differ wildly
between a unit suite and a browser suite. Tuning a slider re-runs no inference.

## Limits

- **Messages only.** A flake whose cause is invisible in its assertion message —
  most order dependencies, unless the runner reports them — reads as `unclear`.
  Feeding the surrounding log would help and costs more tokens.
- **No trend detection.** "Started flaking after Thursday" needs date
  comparison, which `jev-1.13` cannot do. Partition the runs by date in Python
  and compare two summaries if you want that.
- **Branch is recorded but unused** in the verdict. A test failing only on one
  feature branch is usually that branch's bug; filter in Python before running.
- **Six distinct messages per test.** A test with thirty different failure
  modes is judged on the first six seen.
- **The defaults are starting points.** `0.05 / 0.85 / 0.6` were chosen to make
  the sample separate cleanly. Measure them against a set of tests you have
  already triaged by hand before trusting the quarantine list.
