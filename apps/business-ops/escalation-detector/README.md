# 🚨 Escalation Detector

Find the support threads that need a manager *before* they blow up — and be
honest about the ones it cannot call.

## The problem

Escalation is currently detected by a support lead skimming the queue between
meetings, or by a keyword filter on the word "lawyer". Both miss the thread
where nobody has shouted yet: three unanswered follow-ups, a regulator's clock
running, a customer who has stopped asking and started documenting.

The failure mode of automating this is not missing escalations. It is the
opposite: a detector that flags everything, gets muted in week two, and is then
worse than nothing because everyone believes it is running.

## Why this needs Jev

**Calibrated probabilities you can abstain on.** The output of every judgment
is a number, not a label, which means the app can decline to answer. Threads
land in one of three piles:

| Condition | Verdict |
|---|---|
| Any hard trigger fires (P ≥ gate) | **escalate**, naming the trigger |
| Composite ≥ escalate line | **escalate** |
| Composite ≤ ignore line | **leave to the agent** |
| Anything in between | **a human looks at it** |

The band between the two lines is the product. The app reports how much of the
queue it actually decided, so nobody mistakes "quiet" for "handled".

**Vetoes are not weighted contributions.** A legal threat escalates on its own,
even in an otherwise calm thread. In a single weighted score, four calm
dimensions outvote one mention of general counsel. Here the triggers are
separate Nouls with their own gate and they bypass the composite entirely — the
offline check asserts exactly this.

**And re-tuning is free.** Weights and both thresholds are sliders; moving them
re-decides the whole queue with zero API calls, because a threshold is policy,
not evidence.

## How it works

Five trigger Nouls and four graded Scores per thread, one request:

```python
questions[f"legal_threat__{index}"] = noul(
    {"task": "Does the customer reference lawyers, regulators, contractual breach, "
             "or formal complaint procedures?", "thread": reference},
    true="They mention legal counsel, a regulator, a breach of contract, service credits, "
         "or a formal complaint",
    false="No legal, regulatory, or contractual process is referenced")

questions[f"authority_gap__{index}"] = score(
    {"task": "Beyond the front line's authority", "thread": reference},
    ["An ordinary agent can fully resolve what is being asked",
     "An agent can resolve it after checking with someone",
     "What is being asked needs a refund, credit, exception, or commitment an agent cannot give",
     "What is being asked needs a change of contract, policy, or roadmap"])
```

Then, in Python only:

```python
fired = [key for key in TRIGGERS if judgment[key] >= trigger_gate]
if fired:                    return "escalate", total, f"hard trigger: {', '.join(fired)}"
if total >= escalate_at:     return "escalate", ...
if total <= ignore_below:    return "leave_to_agent", ...
return "look_at_it", ...
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve synthetic threads ship with it, chosen to stress the band: a regulator
clock with counsel copied, a polite renewal discount question that looks
alarming to a keyword filter, a data-exposure report written calmly, and a
customer who is furious but asking for nothing.

## Design notes

**Why `authority_gap` is its own dimension.** The most commonly missed
escalation is not an angry one — it is a reasonable customer asking for
something an agent has no authority to give (TH-912). Anger-only detectors miss
it entirely.

**Why triggers are Nouls and not one Choice.** A thread can contain a legal
threat *and* a churn threat *and* an unanswered chase. They are independent,
and each deserves its own gate: `legal_threat` you want to fire early,
`unanswered` you probably want higher.

**Why the composite is normalised.** All four rubrics have four levels here,
but dividing by `len(levels)-1` and the weight sum keeps the 0–100 scale stable
if you add a three-level dimension later.

**Why the calibration table exists.** It shows where the abstention band
actually lands on your data, so you can move the lines with evidence instead of
taste. It is `pd.cut` and `groupby` — the model is never asked to bucket
anything.

## Limits

- **The composite is not a probability of escalation.** It is a weighted
  position on rubrics you chose. Treating 60 as "60% likely" is a mistake.
- **No history, no timers.** It reads the thread text. "Three days without a
  reply" registers because the customer wrote it, not because the app compared
  timestamps — `jev-1.13` cannot compare dates and is never asked to.
- **Abstention is not free.** A wide band means a person reads more threads.
  The metric row prints the auto-decided percentage so that trade-off stays
  visible rather than implied.
- **Thresholds ship uncalibrated.** 60/30 are placeholders. Run a week of
  known-outcome threads through it before anyone relies on it.
- **`jev-1.13` reads literally.** A customer who says "I'll have to review our
  options" is not a stated churn threat under the criteria as written. Decide
  whether you want that, and put it in the `true`/`false` text either way.
- **It ranks; it does not act.** No auto-assign, no auto-reply, no paging.
