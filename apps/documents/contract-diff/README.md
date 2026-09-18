# 📜 Contract Diff

Two versions of the same terms in, a ranked list of **what got worse for you** out.

## The problem

The email says *"we've updated our Terms of Service."* Attached is a redline, or
more often nothing at all. Somewhere in fourteen clauses of near-identical prose
sit the two edits that matter: the refund window went from thirty days to seven,
and a class-action waiver appeared under a heading that used to say *Governing Law*.

A `diff` finds those edits perfectly well. It also finds the nine cosmetic ones,
presents all eleven with equal weight, and renumbers every clause after a
deletion so half the document lights up red. Reading a redline is a lawyer's job
precisely because the machine can say *what moved* but not *who it moved against*.

## Why this needs Jev

The job splits cleanly along a line where each side is reliable, and the split is
the whole design:

| Question | Answered by | Why |
|---|---|---|
| Which clauses changed? | `difflib` | Alignment and renumbering are mechanical. A model asked to do this will hallucinate a clause boundary. |
| Is this change adverse to the *customer*? | Jev | A judgment about whose interest a wording serves. No amount of string distance encodes it. |

So Python never guesses at meaning and the model is never asked to count, align
or track clause numbers — the two things `jev-1.13` is worst at.

Three properties get judged per changed clause, and they are **three separate
questions** because they come apart in practice:

- `adverse` — the customer is worse off
- `obligation` — the customer now owes a duty they did not owe before
- `severity` — a Score, because this is a degree, not a yes/no

The bundled sample has a clause that proves the split is real. The new EXPORT
CONTROL clause imposes a genuine new obligation while being close to standard
boilerplate: high `obligation`, moderate `adverse`. Collapse those into one
rubric and you lose the ability to threshold them separately — which is exactly
the knob a reviewer wants.

## How it works

`difflib` aligns on clause *bodies* with the number stripped, so deleting clause
9 does not report clauses 10–14 as edits:

```python
def body(clause: str) -> str:
    return " ".join(re.sub(r"^\d+\.\s*", "", clause).split())

matcher = SequenceMatcher(None, [body(c) for c in old], [body(c) for c in new],
                          autojunk=False)
```

Each surviving change becomes three questions, all in one request:

```python
questions[f"adverse_{index}"] = noul(
    {"task": "Is this change to a Terms of Service adverse to the customer, "
             "judged from the customer's side rather than the vendor's?",
     "change": reference},
    true="The customer is worse off under the new wording: a right narrowed, a "
         "protection withdrawn, a vendor discretion widened, or a burden shifted onto them",
    false="The customer is unaffected or better off: pure rewording, a clarification, "
          "or a term that gives the customer more than before")
questions[f"severity_{index}"] = score({...}, SEVERITY_LEVELS)
```

Six changed clauses × three judgments = 18 questions in **one** round trip.
Verdicts are assembled in Python:

```python
if adverse >= t_adverse and severity >= t_severity:  return "worse", ...
if adverse >= t_adverse:                             return "watch", ...
if obligation >= t_obligation:                       return "watch", ...
```

Move a slider and the table re-sorts with no further inference.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Two versions of a synthetic SaaS ToS are bundled and pre-loaded, so it runs
before you paste anything. Between v1 and v2 there are six changes: two
genuinely adverse (refund window cut to seven days and switched to credit;
binding individual arbitration with a class-action waiver), one pro-customer
(data deletion in seven days with a certificate), one cosmetic reword, one
deleted clause, and one new clause.

## Design notes

**Why a Score for severity and Nouls for the rest.** *Adverse* and *new
obligation* are genuinely binary — a clause either shifts a burden or it does
not, and `P = 0.5` honestly means "could be read either way". Severity is a
degree, and a Noul is not an intensity dial. The three Score levels each name a
concrete situation, so a reviewer disagreeing with a rating can point at which
level the clause actually matches.

**Why the pairing cutoff is a slider.** Whether a heavily-rewritten clause is
one edit or a deletion plus an addition is a genuine judgment call about the
document, not about the model. It is exposed rather than hardcoded. The bundled
sample's GOVERNING LAW → DISPUTE RESOLUTION rewrite sits near that boundary.

**Nothing is sent when nothing changed.** Identical documents produce zero
changes, zero questions and zero cost.

## Limits

- **Clause-level, not word-level.** You get "this clause got worse", not "these
  four words did it". For the wording, read the before/after panes.
- **Numbered-paragraph documents only.** The splitter wants `1. HEADING. text`.
  A contract in nested sub-clauses (4.2.1(a)) is split more coarsely than a
  lawyer would split it.
- **`jev-1.13` cannot compare dates or do arithmetic.** It is never asked to.
  It will not tell you that thirty days became seven — it tells you the customer
  now has a shorter window, and you read the numbers yourself. If you need the
  delta, extract the figures in Python.
- **It reads literally.** "Adverse" means what the `true`/`false` criteria say
  it means. A clause that is adverse only in light of an unstated business fact
  — your industry's regulator, your renewal date — will read as benign.
- **It degrades on large noisy state.** The whole diff goes in one request. A
  200-clause master services agreement should be diffed a section at a time;
  the app warns when the estimate crosses `provider.context_tokens`.
- **Not legal advice**, and not a substitute for counsel on a contract you are
  about to sign. It is a triage tool for deciding which three clauses to read.
