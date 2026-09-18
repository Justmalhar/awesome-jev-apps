# ✅ Task Prioritizer

Re-rank a task list against this week's stated goal — and keep most of the
judgments when the goal changes.

## The problem

Every task manager lets you set a priority field, and every task manager's
priority field is a lie within two weeks. P1 accumulates. Nothing is ever
demoted, because demoting something requires re-reading it in the light of what
matters *now*, and what matters now changed on Monday when the design partners
moved their date.

So the list is re-triaged by hand, badly, every Monday morning — or more
honestly, it is not re-triaged at all and people work from memory and whoever
asked most recently.

The judgment that changes weekly is narrow: *does this serve the current goal?*
Everything else about a task — how big it is, whether someone is blocked on it,
whether you promised it to a customer — is exactly as true this week as last.
Re-deriving all of it every Monday is the waste.

## Why this needs Jev

This app exists to demonstrate a property that only makes sense with cheap
structured judgment: **the judgment set splits by what it depends on.**

```python
STABLE_KEYS = ("effort", "unblocks", "promised", "decays")   # properties of the task
GOAL_KEYS   = ("serves", "required")                         # properties of task × goal
```

- **Cold run:** 18 tasks × 6 = 108 questions, one request.
- **Rewrite the goal:** 18 × 2 = 36 questions. The stable half is never asked
  again, because nothing about it changed. Two-thirds of the cost disappears.
- **Move a weight:** zero questions. Ranking is arithmetic in Python.
- **Add one task:** 6 questions for that task alone.

A chat model can be prompt-cached but cannot give you this, because the split is
not about tokens — it is about *which judgments a change invalidates*. That is a
property of question design, and question design is only worth doing when the
answer is a typed number you can cache.

## How it works

```python
# asked once per task, ever
effort   = score("How much work is this item, as described?", [4 concrete levels])
unblocks = noul("Is somebody else waiting on this item before they can continue?")
promised = noul("Was this item promised to someone outside the team?")
decays   = noul("Does this item get more expensive or harder the longer it is left?")

# asked once per (task, goal)
serves   = score("How much does finishing this item move the stated goal?", [4 levels])
required = noul("Must this item be finished before the stated goal can be reached?")
```

Ranking and bucketing are code:

```python
def priority(stable, goalj, weights):
    value  = w.serves*serves + w.required*required + w.unblocks*unblocks + ...
    effort = 1.0 + w.effort_penalty * stable["effort"] / 3
    return value / effort          # effort divides, so cheap wins float up
```

Effort **divides** rather than subtracting, which is what makes a ten-minute
access approval that unblocks a colleague outrank a three-week migration with
similar stated value. That is a policy choice, it is one line, and you can
disagree with it by dragging the penalty to zero.

Four buckets — *serves the goal*, *owed to someone*, *getting worse*, *not this
week* — because "everything that is not the goal is equal" is how you end up
with an expired TLS certificate. `decays` and `promised` exist to keep the two
categories of not-the-goal work visible without pretending they are the goal.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_tasks.json` — 18 tasks for a team shipping a
self-serve signup flow, including a one-click access approval that blocks a
colleague, an external compliance report with nothing to do with the goal, a
certificate that expires whether or not you care, and a three-week migration
that should not be started this week.

Then **edit the goal box** and press the button again: the footer shows the
second run costing a third of the first.

## Privacy

Task titles, projects and notes, plus the goal you type, are sent to whichever
provider `providers.toml` selects.

## Design notes

- **Why `serves` is a Score and `required` a Noul.** "How much does it help" is a
  degree with distinguishable situations; "is the goal blocked without it" is a
  fact. Encoding a hard blocker as the top level of a rubric would make it
  compete with merely-very-helpful items on a continuum, which is exactly the
  distinction you want to threshold separately.
- **Why `decays` is not a deadline question.** `jev-1.13` cannot compare dates.
  The Noul asks whether delay *compounds the work*, which is a property of the
  task and needs no calendar. If you want real deadlines, parse them from your
  task manager and compare in Python.
- **Cache keys are task ids.** If your exporter does not have stable ids, the
  cache cannot work and you are back to paying full price every time.

## Limits

- **The cache lives in Streamlit session state.** Close the tab and the stable
  judgments are gone. Persisting them to SQLite is ~15 lines and is the obvious
  first change for real use.
- **Edited notes are not detected.** The cache is keyed by task id alone, so if
  you rewrite a task's description the stale stable judgment is reused. Key on
  `(id, hash(notes))` if your tasks churn.
- **Effort is judged from the description, not from history.** A task described
  in one line reads as small. This is a judgment about the text, and the text is
  often wrong — which is an argument for writing better task notes, but a real
  limit on the output.
- **No dependency graph.** `unblocks` tells you someone is waiting; it does not
  know *which* task they are waiting for, so the app cannot topologically sort.
