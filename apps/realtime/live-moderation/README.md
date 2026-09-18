# 🛡️ live-moderation

Judge every message in a live chat **as it arrives**, not in a queue a human
drains an hour later.

```bash
uv run streamlit run app.py
```

```
🟢 priya · allow · 143 ms
   Does 4.0 still support the old CSV importer or is that gone?
   attack 0.02 · promo 0.01 · off-topic 0.03 · human 0.06 · severity 0.11

🔴 growthguy88 · remove · 138 ms
   Hey everyone — I run a free newsletter on SaaS growth, drop your email...
   attack 0.03 · promo 0.96 · off-topic 0.88 · human 0.04 · severity 2.91

🟠 qa_lena · allow · 151 ms                                  [ping a human]
   my whole team is locked out since the upgrade, nobody can log in...
   attack 0.02 · promo 0.01 · off-topic 0.09 · human 0.93 · severity 0.42
```

## The problem

Live chat moderation is structurally behind. Keyword filters catch the slurs
and nothing else: "asking that is pointless, they never answer the self-host
people, you're wasting your time" contains no bannable word and is exactly what
makes people leave a community. So the real work goes to a review queue, and by
the time a human sees message 7, the room has already read it and message 40 is
on screen.

The obvious fix — an LLM in the send path — costs a second or more per message,
which is a visible stall on every send, and a bill proportional to traffic.

## Why this needs Jev

A chat message renders in well under a second, so a moderation decision that
takes roughly 150 ms fits *inside the render*. That is the difference between
moderation as an after-the-fact cleanup and moderation as a property of the
send path: nothing is displayed before it has been judged.

The second property is batching. Four independent labels plus a severity rubric
are five questions in **one** request over a state ingested once — not five
calls, and not one muddled call asking for a blob of JSON.

## How it works

One request per message:

```python
"attacks_person": noul({...}, true="Belittles, insults, or tells off another named participant",
                             false="Criticises the product, the company, or nobody in particular"),
"is_promotion":   noul(...),
"off_topic":      noul(...),
"needs_human":    noul(...),
"severity":       score({...}, SEVERITY_LEVELS),
```

All four Nouls can be true at once — spam that is also a personal attack — so
they are four Nouls, not one Choice and not four levels of a rubric. Keeping
them separate is what lets each one carry its own threshold: `is_promotion`
auto-removes at 0.8, `off_topic` only ever holds.

`decide()` is ordinary Python. Moving a slider re-ranks the whole run with zero
inference.

**Per-call state is one message plus the channel topic** — two short strings.
No rolling history, no user reputation, no prior messages. That is deliberate:
the state is what you pay for in both tokens and latency, and a moderation call
that drags 50 messages of scrollback behind it is no longer a 150 ms call. If
you need reputation, keep it in Python and apply it to the thresholds.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx --with streamlit streamlit run app.py
```

The bundled 15-message stream is workplace-safe on purpose: the interesting
cases here are the belittling reply and the newsletter spam, neither of which a
word list catches, not shock content.

## Design notes

- **Four Nouls, one Score.** The Nouls are independent properties. The Score is
  the one genuinely ordered quantity — how far along a severity ramp this sits —
  and each of its four levels describes a situation you could point at in the
  sample stream.
- **`needs_human` never removes.** It is an escalation signal, not a punishment
  signal: `qa_lena` reporting an outage is perfectly allowed content that a
  human should nonetheless see immediately. Its threshold is a separate slider.
- **Thresholds are asymmetric by design.** Promotion and personal attacks can
  auto-remove; off-topic can only hold. That asymmetry belongs in policy, which
  is why it lives in `decide()` and not in an instruction.

## Limits

- One message at a time means no sense of a pile-on. Three users each posting
  one mild jab is three `allow`s; correlating them is Python's job, over the
  verdicts this app produces.
- No memory of the author. A first-time joke and the fortieth repetition of the
  same spam score the same. Reputation belongs in your own store, applied to the
  thresholds.
- `jev-1.13` reads literally, so sarcasm delivered as praise is the standing
  failure mode. Anything you would explain with "I meant…" belongs in the
  `true`/`false` criteria.
- It cannot count, sum, or compare dates — so "has this user posted the same
  link before" is a Python question over your own history, not a Jev question.
- Latency is measured client-side and includes your network path. The numbers in
  the footer are what your users would actually experience from where you ran it.
