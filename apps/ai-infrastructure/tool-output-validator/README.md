# 🔍 tool-output-validator

Did the tool actually return what was asked for? Catches the silent failures
that agents happily build an answer on top of.

```bash
uv run --with httpx python app.py
```

```
✅ tc-01  accept    risk 0.11/3
🔁 tc-03  retry     risk 2.40/3
     wanted: Get the current outstanding balance on invoice INV-4471
     got   : {"status": "ok", "data": null, "message": "invoice lookup temporarily unavailable..."}
🚨 tc-07  escalate  risk 2.85/3
     wanted: Get last week's signup numbers broken down by country
     got   : {"metric": "signups", "period": "last_30d", "group_by": "plan", "rows": [...]}
🚨 tc-05  escalate  risk 2.60/3
     wanted: Read the deployment runbook for the checkout service
     got   : <!DOCTYPE html><html><head><title>Sign in - Northwind Wiki</title>...
```

## The problem

Tools fail silently far more often than they raise. `{"results": []}` with a
200. A session-expired HTML sign-in page returned where a document was expected.
An analytics query that quietly fell back to a 30-day window grouped by plan
when you asked for 7 days grouped by country. `{"ok": true, "cancelled": 0}`.

None of these throw. The agent receives a well-formed response of the expected
shape, concludes the step succeeded, and writes the rest of the answer from
whatever it can infer — which is to say, from nothing. The user sees a confident
reply built on an empty result, and the trace looks clean because every tool
call returned 200.

Schema validation cannot catch any of this. **Every example above is valid JSON
matching its expected schema.** Retry logic cannot either, because nothing
signalled a failure to retry. The only thing that separates a genuine empty
result from a disguised error is what the output *means* relative to what was
asked, and that has been too expensive to check on every call.

## Why this needs Jev

Agents make a lot of tool calls. Validating every one with a frontier model
roughly doubles the cost and latency of the agent, so nobody does it — they
validate the important ones, which are the ones that were already handled.

At $0.042 per million input tokens with output free, a tool call plus its output
put through six judgments costs on the order of $0.00002. Validating **every**
call is cheaper than the retry you avoid.

The second property is the shape of the answer. This is not one question. An
output can be well-formed, non-empty, on-schema, about the right entity, and
still answer a different question than the one asked — so `empty_but_valid`,
`error_in_disguise`, `wrong_subject` and `answers_intent` are **separate
calibrated numbers**, because the right reaction to each is different: retry the
disguised error, escalate the wrong subject, accept the honest empty. A single
"is this output good?" score maps all four onto one number and forfeits the
routing.

## How it works

Five Nouls and one Score per call, all in one request:

```python
PROPERTIES = {
    "answers_intent":    "Does this tool output supply what the caller was trying to find out?",
    "error_in_disguise": "Is this output reporting a failure while presenting itself as a success?",
    "wrong_subject":     "Is this output about a different entity, filter, period or grouping "
                         "than the call asked for?",
    "missing_requested_field": "Was the specific detail the caller wanted left out?",
    "empty_but_valid":   "Is this a well-formed result that simply contains nothing?",
}
```

The state names the three things explicitly — what the caller wanted, the call
that was made, and what came back — because the model reads literally and the
whole judgment is a comparison between the first and the third.

The reaction is a pure function:

```python
def decide(props, risk, *, gate, risk_gate):
    if props["error_in_disguise"] >= gate:
        return "retry"
    if props["wrong_subject"] >= gate:
        return "escalate"
    if props["answers_intent"] >= gate:
        return "accept"
    if props["empty_but_valid"] >= gate:
        return "escalate" if risk >= risk_gate else "empty"
    return "escalate" if risk >= risk_gate else "retry"
```

`wrong_subject` is checked *before* `answers_intent` deliberately: an output
that answers a different question convincingly is the most dangerous state
there is, and it scores high on "answers the intent" to a careless reader.

The Score decides what an honest empty result means. "No pending change found"
is a real answer when you are reporting to a user and a reason to stop and ask a
human when the next step was going to modify a billing record. Same Nouls,
different `risk`, different verdict.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --risk-gate 1.0        # escalate more readily
uv run --with httpx python app.py --json | jq '[.rows[] | select(.verdict=="escalate")]'
echo $?                                                   # 1 if anything needs a human
```

Ten calls ship in `sample_calls.json`, each a real silent-failure shape — empty
result, disguised timeout, wrong team filter, sign-in page, no-op success,
silently-changed query parameters, draft document where a signed one was asked
for, network error, and a record missing the one field that was wanted.

## Design notes

- **Why `empty_but_valid` exists at all.** Without it, every empty result looks
  like a failure, the validator cries wolf on every legitimate "no matches", and
  someone switches it off within a week. Distinguishing the honest empty is what
  makes the other verdicts trustworthy.
- **Why retry and escalate are different verdicts.** A disguised timeout is worth
  calling again. A query that answered a different question will answer the same
  different question next time — retrying it just burns latency before failing
  the same way.
- **Why `missing_requested_field` is separate from `answers_intent`.** They come
  apart on the common case in `tc-10`: the record exists, the call worked, and
  the one field the caller needed is not in it. That is a different fix (change
  the call, expand the projection) than a failed lookup.
- **Why the risk Score is about the agent, not the user.** The levels describe
  what an *agent proceeding on this output* does, because that is the actual
  next event. "Takes a wrong action on a real system" is the level that should
  stop the run.

## Limits

- **It judges plausibility, not truth.** An output that is well-formed, on-topic
  and entirely fabricated by an upstream service reads as `accept`. This
  validates the call/response relationship, not the tool's honesty.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** "Did this return
  the 20 rows I asked for?" and "is this timestamp within the requested window?"
  are Python checks. Do them first and pass the result in as state if you want
  them judged together.
- **Large outputs need truncating first.** A 200KB JSON blob is mostly
  irrelevant detail, and the model degrades on large noisy state. Truncate or
  sample in Python before validating; the shape of a silent failure is almost
  always visible in the first few hundred characters.
- **The intent string is doing a lot of work.** This compares the output against
  a stated intent. If your agent does not record why it called a tool, you have
  to reconstruct that, and a vague intent produces a vague judgment.
- **One call at a time.** A step that only makes sense as part of a sequence
  ("list ids, then fetch each") is judged in isolation. Cross-step coherence is
  `agent-trace-classifier`'s job.
- **Calibrated is not correct.** The gates decide what happens; keep a human on
  the escalate queue rather than auto-failing runs on a 0.61.
