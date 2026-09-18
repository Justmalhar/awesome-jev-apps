# 📞 Sales Call Tagger

Objections raised, competitors mentioned, next steps agreed — pulled out of
free-text call notes, one independent judgment per signal.

## The problem

Every CRM has an "objection" field. It is a single-select dropdown, it is filled
in by the rep after the call if at all, and it records the *first* objection they
remember. So the quarterly pipeline review is conducted on data that says price
is the top objection, because price is the first thing anyone remembers, and the
fact that fourteen of those calls also raised a missing integration is nowhere.

The notes themselves are rich — reps write good notes when nobody makes them fit
a taxonomy. They just never get read in aggregate.

## Why this needs Jev

**Three different shapes of question, in their correct shape.**

- **Objections are independent.** A call can raise price *and* security *and*
  switching cost. Eight separate Nouls, one per objection, each with its own
  probability and its own threshold. A single-select field is structurally
  incapable of representing this and that is why the data is wrong.
- **Competitors are independent too**, and the list is config: one Noul per name
  on your battlecard, generated from a text box. Adding a competitor next
  quarter is one line, not a retrain.
- **The next step is exclusive.** There is one agreed next action or there is
  none, so it is a Choice — with `no_next_step`, because plenty of calls end
  with nothing agreed and a Choice must return something.

Because the outputs are probabilities rather than labels, the tagging threshold
is a slider: moving it re-tags the whole quarter with **zero API calls**. And
because the judgments are independent, the co-occurrence matrix — *which
objections travel together* — is just a pandas dot product over the fired
matrix. That matrix is the thing a single-select field can never give you.

All of it in one request: 12 calls × 17 judgments = 204 questions, one round
trip.

## How it works

```python
for key, (task, yes, no) in {**OBJECTIONS, **BUYING_SIGNALS}.items():
    questions[f"{key}__{index}"] = noul({"task": task, "call": reference}, true=yes, false=no)

for position, competitor in enumerate(competitors):
    questions[f"comp{position}__{index}"] = noul(
        {"task": "Did the prospect mention this alternative during the call?",
         "alternative": competitor, "call": reference},
        true="This alternative is named as something they use or are considering",
        false="This alternative is not mentioned")

questions[f"next__{index}"] = choice(
    {"task": "What next step was agreed on this call?", "call": reference}, NEXT_STEPS)
```

Qualification is policy, so it is Python:

```python
if strong_next_step and problem_owned and not blocked:  return "advancing"
if judgment["next_step"] == "no_next_step" and blocked:  return "stalled"
return "working"
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve synthetic call notes ship with it, across four reps — including a call
with four simultaneous objections, a call that is entirely procurement theatre,
and one that a rep would optimistically log as "proposal requested" when nothing
is live.

## Design notes

**Why `problem_owned` and `champion_language` are in here.** The objection list
alone ranks deals by how much the prospect complained, which inverts the truth:
the most engaged prospects object most. Pairing objections with buying signals
is what makes the qualification rule defensible.

**Why the next-step Choice has seven options including two soft ones.**
`customer_will_revert` and `no_next_step` are distinct: one is a real (weak)
outcome, the other is a call that went nowhere. Collapsing them lets reps hide
dead calls in a category that sounds alive.

**Why competitor mentions are not one Choice.** "We're looking at Datadome and
Northstar" is a normal sentence. One Choice would report one of them.

**Confidence is stored, not acted on.** `next_conf` is kept for spot-checking.
It measures distribution concentration, not correctness.

## Limits

- **It reads notes, not calls.** If the rep did not write it down, it is not
  there. Point it at transcripts and the signal quality changes completely (for
  better, and the token cost goes up a lot).
- **Rep-authored notes are biased.** Reps under-record objections and
  over-record next steps. The tagger reproduces that bias faithfully — it is a
  measurement of the notes, not of the call.
- **No counting, no arithmetic, no dates.** Frequencies, co-occurrence and
  averages are pandas. The model is never asked how many objections were raised.
- **`jev-1.13` reads literally.** "Check back after year end" is a timing
  objection only because the criteria say a busy period or another priority
  counts. Change the criteria, not the threshold, when it disagrees with you.
- **The qualification rule is a placeholder.** `advancing / working / stalled` is
  three lines of Python that you should replace with your own stage definitions.
  That is the intended use: the judgments are the asset, the rule is yours.
