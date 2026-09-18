# 🎧 meeting-copilot

Flag commitments and dodged questions **during** the call, not in a summary
email the next morning.

```bash
uv run --with httpx python copilot.py
```

```
  Ravi: And did the finance team ever sign off on the new fee schedule?
        [nothing flagged · 141 ms of a 5600 ms utterance]

   Tom: So finance have been really busy with quarter end, and there's a lot
        going on their side at the moment, you know how it is.
        🚩  [dodged question · 148 ms of a 8800 ms utterance]

   Tom: I'll chase Priya today and get you a yes or no by end of day.
        📌  [commitment · 139 ms of a 5200 ms utterance]
```

## The problem

The two most consequential things in a meeting are the commitments people make
and the questions they slide past — and both are only actionable *in the room*.
"Tom didn't answer that" is a useful thing to know while Ravi still has the
floor. Read in a transcript summary the next day, it is a grievance.

Post-hoc meeting AI has this exactly backwards. It gets the transcript, thinks
for thirty seconds, and produces a tidy list of action items after the only
moment when anyone could have pressed. And the dodge never makes the list at
all, because the thing that makes it a dodge is the question two lines earlier.

## Why this needs Jev

The window is one utterance. A spoken sentence lasts a few seconds; a judgment
that takes a second and a half is already two sentences behind by the time it
lands, and the error compounds over an hour. At roughly 150 ms the flag appears
while the speaker is still finishing the sentence — the footer prints exactly
this comparison, measured: total judgment time against total speech time.

Four independent signals per utterance also means four questions in **one**
request, so the per-utterance cost stays flat over a 60-minute call.

## How it works

```python
"dodges_question": noul(
    {"task": "Does this reply avoid answering the question that was just asked?",
     "question_asked": "`previous.text`", "reply": "`utterance.text`"},
    true="Fills the space with context, deflection, or reassurance instead of the answer asked for",
    false="Answers it, or honestly says they do not know, or the previous line asked nothing",
),
"commitment": noul(..., true="Takes on a specific piece of work or an action they will personally do",
                        false="Describes work in general, agrees with someone, or commits nobody in particular"),
"decision": noul(...),
"raises_blocker": noul(...),
```

Four Nouls, not one Choice: an utterance can be a commitment *and* a decision,
and "Yes. I'll draft the rollback plan" in the sample is both. Thresholding them
separately is the whole point — you want commitments at 0.7 and dodges at 0.85,
because a false dodge flag is socially expensive and a false commitment flag is
not.

**Per-call state is two utterances** — the previous line and the current one, a
couple of hundred tokens. Not the meeting so far, not the attendee list, not
last week's notes. A dodge is defined entirely by the question immediately
before it, so that is exactly what gets sent; hauling a growing transcript along
would make every later utterance slower than the last, which is precisely the
property that would kill this in a real call.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx python copilot.py                 # replays at 6x speaking pace
uv run --with httpx python copilot.py --speed 1       # real time
uv run --with httpx python copilot.py --speed 0 --json
```

`--speed` paces the replay from word counts computed in Python. The measured
per-utterance latency is unaffected by it.

## Design notes

- **`dodges_question` needs the previous line, so it gets it — and nothing else.**
  This is the one question here that is relational, and the smallest state that
  makes it answerable is two strings.
- **Every Noul states the `false` side explicitly.** "Or the previous line asked
  nothing" is there because the model reads literally: without it, a statement
  following a statement can read as a dodge.
- **Flag policy is `flags_for()`.** Thresholds in Python, so retuning after the
  call re-runs nothing.
- **Speech duration is Python arithmetic.** Word counts and durations are never
  asked of the model — a documented `jev-1.13` failure mode.

## Limits

- It flags the sentence, not the thread. "I'll do it" three lines after the thing
  was named is a commitment with no visible object; resolving what *it* refers to
  would need more state than this design is willing to send.
- No speaker diarisation, no ASR. It takes a clean transcript. In a live call the
  real latency budget is ASR plus this, and ASR is usually the larger half.
- A dodge is a judgment about conversational behaviour, and a polite "let me come
  back to you on that" sits genuinely near the boundary. Run it as a private
  nudge to the chair, not as a scoreboard on screen.
- `jev-1.13` cannot compare dates, so "by Thursday" is captured as text and never
  turned into a deadline. Parsing that is Python's job downstream.
- Latency is measured client-side per utterance and includes the network path.
