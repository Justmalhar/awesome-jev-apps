# 📋 Community Moderator

Applies **your** written community rules to posts — one Noul per rule, a
severity Score, and a human tier that is supposed to stay full.

## The problem

A forum's rules are not a policy document. They are six lines pinned at the top
of the board, written by the people who run it, argued over in a meta thread,
and amended last Tuesday because someone found a loophole. Rule 5 exists because
of one specific incident in 2024 that nobody outside the community remembers.

Every off-the-shelf moderation API answers a different question: *is this toxic
in general?* That is not the question a moderator has. The question is **"does
this break rule 3?"** — and a general toxicity score cannot answer it, because
rule 3 is about crossposting and toxicity has nothing to do with it.

So moderators read everything by hand, fall behind at weekends, and apply the
rules inconsistently depending on who is on shift. The backlog is the product.

## Why this needs Jev

The rules live in a text box. Editing the box changes the questions on the next
run — no retraining, no fine-tune, no prompt rewrite, no redeploy. That works
because of three structural properties:

1. **Per-rule Nouls.** Rules are independent — a post can break R1 and R4 at
   once — so each rule is its own Noul with its own probability and its own
   threshold. A single "violation" label would let you tune nothing.
2. **The rule text is state, not prompt.** Each Noul points at
   `` `rules[j].text` ``, so the operator's own wording *is* the policy. Ten
   rules × ten posts is a hundred independent judgments in one request.
3. **Price.** A hundred judgments per request at $0.042/Mtok is what makes it
   viable to re-run the whole queue every time you amend a rule — which is the
   feature. A frontier model here is a budget meeting.

If your rules are the same as everyone else's, buy a generic classifier instead.

## How it works

```python
for position, rule in enumerate(rules):
    questions[f"rule_{index}_{position}"] = noul(
        {"task": "Does this post break the community rule quoted below?",
         "rule": f"`rules[{position}].text`",
         "post": reference},
        true="The post plainly does the thing the rule forbids",
        false="The post does not do that thing, or the rule is about something else entirely")

questions[f"severity_{index}"]  = score(..., SEVERITY_LEVELS)   # harm, 0..3
questions[f"contested_{index}"] = noul("Would two experienced moderators "
                                       "plausibly reach opposite decisions here?")
questions[f"concern_{index}"]   = choice(..., CONCERN_KINDS)    # incl. no_concern
```

and then, in Python where you can read it:

```python
def decide(hit, severity, contested, gates):
    if contested >= gates["contested"]:  return "human_review"
    if hit >= gates["hit"]:              return "block" if severity >= gates["block"] else "warn"
    if hit >= gates["uncertain"]:        return "human_review"
    return "allow"
```

`contested` is checked **first**, before `block`. An automated removal is
exactly the action you least want taken on an item two moderators would argue
about. That ordering is the whole design.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Six sample rules and ten synthetic posts are bundled: an affiliate spammer who
crossposts, a blunt but non-graphic put-down, a sourced disagreement that should
be left alone, an unsourced performance claim, someone posting another member's
employer, and a shop owner posting something genuinely useful. It runs cold with
no input from you.

## Design notes

- **Severity is a Score, not a Noul.** Degree needs ordered levels. A Noul at
  0.5 means "yes and no are equally likely", not "medium harm".
- **Levels are situations.** `["low","medium","high"]` tells a moderator nothing
  and tells the model less. Each level here describes something you could point
  at in a real thread.
- **`no_concern` exists** because a Choice must return something. Without it a
  perfectly clean post is filed under whichever complaint fits least badly, at
  entirely plausible confidence — the worst possible failure for a triage tool.
- **The model never counts.** Which rules fire most often, how big each queue is,
  the worst rule probability per post — all computed in Python from the Nouls.
  `jev-1.13` cannot count, and asking it to is a documented failure mode.

## Limits

- **This assists human moderators. It does not replace them.** Any decision that
  affects a user — a removal, a warning, a suspension — needs a human in the
  loop before it lands.
- **The thresholds here are placeholders.** They were picked to make the sample
  data demonstrate all four routes. Tune them on a few hundred posts your own
  moderators have already actioned, and measure what each gate costs you in
  false removals before you trust it.
- **Confidence is not correctness.** A confident label on a sarcastic post is
  still wrong. That is what the `contested` Noul is for, and it is not perfect
  either.
- `jev-1.13` reads literally. Sarcasm, quoted abuse ("he called me a ___"), and
  in-group banter are its known weak spots — the three things a community
  generates most.
- It degrades on indirection and on large state full of irrelevant detail. Post
  bodies are truncated to 2,000 characters on purpose; a rule that depends on
  what happened three replies earlier will not be applied correctly, because
  the thread is not in the state.
- Vague rules produce vague probabilities. "Be excellent to each other" will sit
  near 0.5 forever. That is a signal to rewrite the rule, not to move the gate.
