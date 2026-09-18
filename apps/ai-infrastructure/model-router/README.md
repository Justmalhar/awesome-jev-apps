# 🚦 model-router

Classify every turn for difficulty **and** stakes, send the easy ones to a cheap
model, and abstain when the turn is too vague to route at all.

```bash
uv run --with httpx python app.py
```

```
💸 u-01  cheap    difficulty 0.21 stakes 0.14  [multi_step=0.03 underspecified=0.02 needs_fresh_facts=0.01]
🧠 u-04  frontier difficulty 0.62 stakes 2.88  [multi_step=0.11 underspecified=0.18 needs_fresh_facts=0.04]
     My 7-year-old weighs 22kg and has a fever of 39.1C. How much paracetamol...
🙋 u-08  abstain  difficulty 1.10 stakes 0.90  [multi_step=0.21 underspecified=0.91 needs_fresh_facts=0.12]
     make it better
```

## The problem

Most production traffic is boring. Reformat this, what does this acronym mean,
make this sentence friendlier — and all of it goes to the same frontier model as
the one genuinely hard question, at the same price.

The obvious fix is a router, and the obvious router is another LLM call, which
reintroduces the cost you were removing. So teams route on proxies instead:
prompt length, a keyword list, whether the user is on the paid tier. Those
proxies are wrong in the expensive direction. **"How much paracetamol for a 22kg
child?" is short, keyword-free, trivially easy — and the one turn you least want
on the cheap model.**

## Why this needs Jev

Two structural properties.

**The router has to be nearly free or it eats the saving.** At $0.042 per
million input tokens with free output, classifying a turn costs on the order of
$0.00001 — three orders of magnitude below the frontier call it is deciding
about. The routing decision is rounding error against the thing it routes.

**Difficulty and stakes come back as separate calibrated numbers**, not a
sentence. That lets the gate be two independent thresholds in Python. A
generative router returns "this seems moderately complex", and you are back to
parsing adjectives.

## How it works

Two Scores, because difficulty and consequence-of-being-wrong are genuinely
independent axes, plus three Nouls for the properties that flip the route on
their own:

```python
questions[f"difficulty_{index}"] = score(
    {"task": "What does producing a correct reply to this turn demand of a model?",
     "turn": reference}, DIFFICULTY_LEVELS)
questions[f"consequence_{index}"] = score(
    {"task": "If the reply to this turn were wrong, what happens to the person who acts on it?",
     "turn": reference}, CONSEQUENCE_LEVELS)
```

Twelve turns × five judgments = 60 questions in **one request**. The route is a
pure function:

```python
def route(difficulty, consequence, flags, *, difficulty_gate, consequence_gate, abstain_p):
    if flags["underspecified"] >= abstain_p:
        return "abstain"
    if difficulty >= difficulty_gate or consequence >= consequence_gate:
        return "frontier"
    ...
```

`abstain` is checked first on purpose: a turn nobody can act on should produce a
clarifying question, not a confident answer from either tier.

The saving is projected in Python from the `MODELS` price table and a token
estimate derived from character length. **Jev is never asked to do arithmetic.**
The footer labels the two numbers separately — the Jev cost is measured, the
routing saving is a projection from a table you can edit.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --difficulty-gate 1.5      # more to the frontier model
uv run --with httpx python app.py --abstain-p 0.4            # ask more clarifying questions
uv run --with httpx python app.py --json | jq '.projected_saving_usd'
```

## Design notes

- **Why stakes are a Score and not a Noul.** "Is this high stakes?" forces a
  binary on something that is plainly a gradient, and `0.5` on a Noul means
  *genuinely uncertain*, not *medium stakes*. The four levels name situations —
  "a wrong reply can hurt someone's health, safety, legal position or finances" —
  so a 2.9 is a claim you can audit rather than a vibe.
- **Why `needs_fresh_facts` forces the frontier tier.** Small models hallucinate
  hardest exactly where they have no data. This is not about difficulty; it is
  about which failure mode the tier has.
- **Why the price table lives in code.** Prices change monthly and vary by
  contract. Baking them into a prompt would mean re-running inference to update
  a number you already know.
- **Why the projection includes the Jev cost.** Subtracting it is the honest
  arithmetic. On this sample it is negligible, but saying so with the number
  visible is better than hiding it.

## Limits

- **The saving is a projection, not a measurement.** It uses list prices and an
  estimated reply length. Your real saving depends on your models, your contract
  and how long your answers actually run. Replace `MODELS` and `REPLY_TOKENS`
  with your own numbers before quoting any figure from this.
- **Routing quality is not measured here.** This app shows what it *would* route,
  not whether the cheap model would have been right. Measuring that needs an
  offline eval against both tiers — see `eval-judge`.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** All cost maths,
  tallies and percentages on screen are Python.
- **It reads literally and degrades on indirection.** Each turn is pointed at
  directly via a backticked path. Wrap a turn in three layers of metadata and
  the judgment gets worse.
- **No conversation history.** A turn that only makes sense after the previous
  three ("do that again but for Q3") will read as underspecified and abstain.
  That is the right default here, but it means a real deployment should route on
  the turn *plus* a short window of history.
- **The abstain path is a gate, not an answer.** Jev selects; it cannot write the
  clarifying question. Your agent still has to ask it.
