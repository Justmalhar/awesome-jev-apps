# 📋 Expense Policy Checker

Feed it a written expense policy and a batch of claims. Get a verdict per claim,
and the specific rule that sank it.

## The problem

Every company has an expense policy nobody has read. Finance approves claims by
skimming for round numbers and recognisable vendors, which catches the £2,000
laptop and misses the bar tab buried in an £88 "dinner with colleagues". The
rules that actually create exposure — no gifts to public officials, no alcohol,
no personal items — are exactly the ones that require *reading the description*,
which is why they go unenforced.

The naive automation is worse. Bolt an LLM on and it returns "this claim is 78%
compliant", which is not a thing. Compliance is not a percentage. A claim that
satisfies nine rules and breaches one is a rejected claim.

## Why this needs Jev

Two structural properties, and the app is built around both.

**Veto semantics need independently thresholded probabilities.** Each rule is
its own Noul. The verdict is a `max` over breaches, never a mean:

```python
def verdict(breaches, reject_gate, review_gate):
    worst = max(breaches.values())
    if worst >= reject_gate:
        return "reject"
```

This only works because independent properties are separate Nouls. Collapse ten
rules into one Choice ("which rule does this breach?") and a claim can only
breach one thing. Collapse them into a Score and you have re-invented the
weighted average you were trying to avoid. The app prints both verdicts side by
side so you can see which claims averaging would have waved through.

**The policy splits into two kinds of rule, and only one kind is an AI problem.**
`"Meals up to 45.00"` is `amount > cap` — Python does it exactly, for free, and
`jev-1.13` cannot do it at all. `"Alcohol is not reimbursable"` needs someone to
read *"two bottles of red with the table"* and know what that is. Rules declare
their own kind:

```json
{"id": "CAP-08", "check": "amount_cap", "category": "meals", "cap": 45.0}
{"id": "ALC-01", "check": "judgment", "breach": "The claim covers alcoholic drinks..."}
```

Only the judgment rules reach the model. A Python-detected breach enters the
verdict at `P=1.0`, because it is not a probability — it is a comparison.

## How it works

One request per batch of claims, carrying every (claim × judgment rule) Noul:

```python
questions[f"b_{claim_index}_{rule_index}"] = noul(
    {"task": "Does this expense claim breach the policy rule quoted below?",
     "rule": f"`rules[{rule_index}].text`",
     "claim": claim_ref},
    true=rule["breach"],
    false=rule["clean"],
)
```

The rule text is *referenced*, not pasted, so the policy JSON stays the single
source of truth — edit the policy, the questions change, no code moves. The
`breach`/`clean` criteria come from the same file, because that is where the
boundary cases belong: "including when it appears as part of a meal bill" is the
line between catching EXP-1002 and missing it.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: a 10-rule policy and 20 synthetic claims planted with a hamper for a
regulator, a parking penalty, a Steam purchase, a direct hotel booking with a
written exception (which is *not* a breach), and business class on a domestic
hop.

## Design notes

- **One Noul per rule, not one Choice over rules.** Claims routinely breach two
  rules at once — EXP-1011 is both a gift to an official and arguably a personal
  item. A Choice forces a single label and hides the second exposure.
- **Two gates, not one.** `reject_gate` and `review_gate` split "the model is
  sure" from "the model is unsure", and both are sliders because the right cutoff
  depends on whether a false reject costs you an argument or an employee.
- **The averaged verdict is shown on purpose.** It is the single most common way
  this app gets built wrong, and one table of disagreements makes the argument
  better than a paragraph.
- **No thresholds in any instruction.** The model is asked whether a rule is
  breached, never "is it *seriously* breached" — severity is the gate's job, in
  Python, tunable without re-running inference.

## Limits

- **Per-person and per-day caps are approximated.** `CAP-08` is applied per
  claim, because splitting a table bill across attendees needs headcount the CSV
  does not carry — and asking the model to do it would be asking it to count,
  which it cannot do.
- **Duplicate-claim detection is not here.** It is a join, not a judgment; do it
  in pandas upstream.
- **No cross-claim reasoning.** Each claim is judged alone, so "three separate
  £240 claims to dodge the £250 approval threshold" reads as three clean claims.
  That pattern is a group-by, and belongs in Python.
- **`jev-1.13` reads literally.** A rule written vaguely ("expenses should be
  reasonable") produces a probability near 0.5 for everything, which is the model
  correctly telling you the rule is unenforceable. Fix the policy, not the prompt.
- **A verdict is not a decision.** This shortlists and evidences; a human still
  rejects. Automating the rejection email is how you get a tribunal.

## Privacy

Claim descriptions, vendor names, employee names and the policy text are sent to
whichever provider `providers.toml` selects. This is financial and HR-adjacent
data leaving your machine — check the provider's retention policy, and strip the
employee column if you do not need it.
