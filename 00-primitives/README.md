# 00-primitives/

**Start here.** Five minutes, one command, and you will understand everything
the apps do.

```bash
cp ../.env.example ../.env     # add ONE key
uv run --with httpx python tour.py
```

## What it shows

`tour.py` sends a realistic support ticket and asks all three question types in
**one request**, then prints the raw distributions rather than just the answers.

```
── NOUL — probability that a condition holds ──
  churn_risk  P(yes) = 0.913

── CHOICE — one option wins, with the full distribution ──
  team = payments   confidence = 0.842
    payments    0.847  ██████████████████████████████████
    platform    0.104  ████
    success     0.041  █
    unclear     0.008

── SCORE — probability-weighted position on an ordered rubric ──
  severity = 2.61   confidence = 0.774
    [0]  0.012  Cosmetic or curiosity — no money or workflow affected
    [1]  0.061  Workflow degraded but they can still operate
    [2]  0.243  Revenue actively blocked right now
    [3]  0.684  Revenue blocked and the relationship is at risk
```

## The three primitives

| | Returns | Use it when | Watch out |
|---|---|---|---|
| **Noul** | `P(yes)` 0–1 | a condition either holds or doesn't | **no confidence field.** 0.5 means genuinely split, *not* "medium intensity" |
| **Choice** | winner + full distribution + confidence | exactly one option should win | it *must* pick something — always include a no-match option |
| **Score** | weighted position + distribution + confidence | degree along an ordered dimension | levels must describe concrete situations, not be labels |

Choose by **what the answer means**, not by what feels convenient. "How
frustrated is this customer" is a degree (Score). "Is this customer leaving" is a
condition (Noul). "Which team owns this" is a selection (Choice).

When several labels can apply at once, that is **one Noul per label** — not a
Choice, which would force them to compete.

## The four lessons in the file

**1. Batch.** All four questions go in one request. They run in parallel against
state ingested once and cannot see each other's answers. A second request is
only warranted when an answer is needed to fetch new evidence or decide the next
options.

**2. A Score can land between levels.** `severity = 2.61` is not a bucket — it is
a probability-weighted position. That is the point.

**3. Confidence is an abstain signal.** It measures how concentrated the
distribution is. It is *not* correctness and *not* permission to act. The tour
uses it to route to human triage, which is what it is actually for.

**4. Policy lives in code:**

```python
escalate = answers.noul("churn_risk") > 0.7 and answers.score("severity") > 2.0
if answers.confidence("team") < 0.6:
    route = "human-triage"
```

Change those numbers and nothing needs re-running. Bake them into the question
instead and every tuning change is a new API bill.

## Next

- [`../apps/`](../apps) — the eight applications, all built from these three questions
- [TypeSafe primitives docs](https://docs.typesafe.ai/primitives) — the full reference
- [Advanced: structure](https://docs.typesafe.ai/primitives/advanced) — passing
  JSON objects as instructions and criteria, which every app here does
