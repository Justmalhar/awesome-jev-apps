# ⚖️ eval-judge

Score model outputs against a rubric and get the same numbers tomorrow — where
an LLM judge quietly gives you different ones.

```bash
uv run --with httpx python app.py
```

```
         score   inst   fact   comp   dire   verdict   label
✅ o-01    2.81   2.94   2.90   2.72   2.68   pass      excellent
✅ o-08    2.14   2.81   1.60   2.35   2.80   pass      correct but adds an unsupported detail
❌ o-07    1.88   2.85   2.70   0.35   2.62   fail      incomplete - billing only
❌ o-05    1.21   2.40   1.05   0.90   0.50   fail      verbose but hollow
⛔ o-03    2.05   0.40   2.85   2.90   1.05   gated     correct but ignores the format constraint
     gate: format_violation (refused=0.02, format_violation=0.91)
⛔ o-04    0.44   0.30   0.60   0.10   0.75   gated     refuses
```

## The problem

Everyone judging model outputs at scale uses an LLM as the judge, and everyone
who has done it for more than a month has been burned the same way: **the
numbers move when the outputs did not.**

The judge drifts. A provider ships a point release and last week's 4.2 average
is this week's 3.8. Someone adds a sentence to the judge prompt and every
historical score becomes incomparable. The same output scored twice in the same
hour comes back 3 and then 4, because generation is sampled. So the eval that
was supposed to be the ground truth becomes another moving part, and teams end
up trusting the direction of the number but not the number.

Then there is the plumbing tax. The judge writes prose, so you ask it for JSON,
so you write a parser, so you write a retry for when the JSON is malformed, and
you are now maintaining a parser as part of your measurement system.

And the weights are stuck in the prompt. Deciding that factual correctness
should count double means editing the judge and **re-running the whole eval**,
which costs money and, because the judge drifted in between, produces numbers
you cannot compare to the run you were trying to reweight.

## Why this needs Jev

Jev returns a **position on a fixed rubric as a number**, not a sentence
containing a number. There is no JSON to parse, no format to enforce, no retry
loop. `answers.score("factual_correctness_3")` is a float, and the levels it
sits between are written in your code where you can read them in a diff.

Because the levels are yours and versioned, a score is comparable across runs
in a way a prompt-defined 1-to-5 scale is not. The rubric is data in your repo,
not a paragraph you tweak.

And at $0.042 per million input tokens with free output, judging six dimensions
of every output on every commit costs less than the CI minutes that produced
them — so the eval runs on everything instead of a nightly sample.

## How it works

Four dimensions, **four separate Scores**, because they vary independently: an
output can nail the format and be factually wrong (o-06), or be correct and
answer a different question (o-02).

```python
for name, spec in DIMENSIONS.items():
    questions[f"{name}_{index}"] = score(
        {"task": spec["task"], "subject": reference}, spec["levels"],
    )
```

Every level describes a situation that stands on its own — *"Mixes correct
statements with at least one the brief does not support"* — so two people
reading a 1.6 agree on what happened. `["poor", "fair", "good"]` would not
survive review here, and would not survive a disagreement about a score either.

The two hard gates are **Nouls, not dimensions**, because refusing the task and
breaking an explicit format rule are not degrees of quality. They disqualify:

```python
def grade(total, gates, *, pass_score, gate_p):
    if any(probability >= gate_p for probability in gates.values()):
        return "gated"
    return "pass" if total >= pass_score else "fail"
```

o-03 is the case that makes this worth doing. It is factually perfect and
complete, and it ignores an explicit instruction. A weighted average scores it
respectably; the gate catches it.

Eight outputs × six judgments = 48 questions in **one request**. The weighting
is Python:

```python
return sum(dimensions[name] * weights[name] for name in dimensions) / sum(weights.values())
```

Deciding correctness should count triple is `--w-factual-correctness 3` over the
same `--json`. No inference re-runs, and the underlying scores do not move.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --w-factual-correctness 3 --pass-score 2.4
uv run --with httpx python app.py --json | jq '.rows[] | {id, total, verdict}'
echo $?                                    # 1 if anything tripped a hard gate
```

The bundled sample is one support-policy task with eight candidate answers —
excellent, off-topic, format-breaking, refusing, hollow, subtly wrong,
incomplete, and one that adds a plausible detail the brief never states. They
are committed data, so nothing calls a generative model to run this.

## Design notes

- **Why a Score and not a Choice per dimension.** Quality on a dimension is
  ordered, and the returned score lands *between* levels — a 1.6 says the output
  is closer to "mixes correct with unsupported" than to "everything supported".
  A Choice would throw that ordering away and force a bucket.
- **Why the brief is in the state.** `factual_correctness` has to be checkable
  against something. Without the brief, the question degenerates into "does this
  match what you already believe", which is exactly the drift being avoided.
- **Why gated sorts to the bottom rather than scoring zero.** A gated output
  still has informative dimension scores — o-03 tells you the model can do the
  content and not the format, which is a different fix from o-05.
- **Why weights are flags, not config.** They are the thing people argue about.
  Making the argument cost one re-run of a pure function keeps it an argument
  about values rather than about budget.

## Limits

- **Reproducible is not objective.** The same input gives the same numbers, and
  those numbers still encode whatever your rubric levels say. A badly written
  level produces a stable, repeatable, wrong score. Version the rubric and review
  changes to it like code.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** The weighted
  aggregate, the pass counts and the leaderboard ordering are all Python. A
  rubric level that depends on counting ("exactly three bullet points") is
  phrased here as a shape judgment; if you need the literal count, check it in
  Python and pass the result in as state.
- **Determinism is not promised across model versions.** Scores are stable for a
  given model. `jev-1.13` becoming `jev-1.14` is a new baseline, exactly as a
  judge model upgrade would be — the difference is that it is a discrete,
  visible event rather than silent drift.
- **It reads literally.** An implicit expectation that is not in the task text or
  the brief will not be judged. If your reviewers apply an unwritten standard,
  write it into the rubric or accept that it is not measured.
- **It degrades on large noisy state.** Eight outputs plus a short brief is
  comfortable. Fifty long outputs in one request is worse than several batched
  requests, which is why `batch()` splits against `provider.context_tokens`.
- **No free-text critique.** Jev selects; it cannot generate. You get numbers and
  gate flags, not a sentence explaining what was wrong. That is the trade being
  made — the numbers are the thing you can track.
- **It cannot judge what the rubric omits.** Tone, safety and style are not
  dimensions here. Add them as further Scores, keeping each one independent
  rather than widening an existing rubric.
