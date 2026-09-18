# 🪟 context-compressor

Pick which context items survive a tight window. **Selection, never
paraphrase** — every surviving item is the original bytes.

```bash
uv run --with httpx python app.py --budget 450
```

```
── kept (7 items, ~418 tokens of ~450 budget) ──
📌 sys-01   system          value 1.35 loss 2.10   38t
     You are the on-call engineering assistant for Northwind. Never restart a production...
📌 usr-03   user_turn       value 2.98 loss 2.60   27t
     Also, the eu-west-1 account is the one with the older IAM boundary policy...
   tool-02  tool_output     value 3.11 loss 2.90   24t
     kubectl logs checkout-7d9f: FATAL: could not connect to database 'orders'...

── dropped (9 items) ──
   doc-02   document        loss 0.08  Northwind expense policy: receipts over 40 GBP require...
   usr-04   user_turn       loss 0.21  Unrelated, but remind me to book the offsite venue...
```

## The problem

Long agent runs overflow the window, and the standard fix is to summarise the
oldest part of the conversation. That is lossy in precisely the wrong place.

The items a summariser drops are the short, boring, structural ones: *"the
deploy role there can't touch RDS"*, *"nothing can be down between 14:00 and
16:00"*, *"never restart a production service without confirmation"*. They
carry no narrative, take one line each, and each one is a constraint that
decides whether the next action is correct or an incident. Meanwhile the summary
faithfully preserves three paragraphs of a tool output that has since been
superseded.

There is a second, quieter failure. Once a summariser has run, the context is no
longer the user's words. It is the model's paraphrase of the user's words, and
every subsequent turn reasons over that paraphrase. Errors compound and there is
no way to audit back to the original.

## Why this needs Jev

**Jev cannot generate. That is the whole argument.** There is no paraphrase
failure mode available to this app, because selection is the only operation it
can perform. Whatever comes out the other side is byte-identical to what went
in — `test_app.py` asserts it.

The affordability matters too: every item in the window gets five judgments,
every time the window is rebuilt, on every long-running agent. At $0.042 per
million input tokens with output free, judging a 16-item window costs a fraction
of a cent — orders of magnitude below the frontier call whose context it is
shaping. Cheaper than the model whose window you are protecting is the only
price at which "re-select on every turn" is a real option.

## How it works

Four Nouls and one Score per item. They are separate questions because they are
separate properties — an item can be a binding constraint *and* superseded, or
needed-now *and* duplicated elsewhere:

```python
PROPERTIES = {
    "needed_now":         "Is this context item needed to act correctly on the current turn?",
    "binding_constraint": "Does this item impose a rule, limit or permission boundary "
                          "that continues to apply?",
    "superseded":         "Has a later item in this context already replaced what this one says?",
    "only_source":        "Is this the only item carrying the detail it contains?",
}
```

`binding_constraint` is the one that earns its keep. It is asked separately from
`needed_now` because a standing rule is often *not* needed for this particular
turn — right up until the turn where violating it causes an outage.

Selection is a greedy pick by value density under a token budget, with pins:

```python
row["pinned"] = (row.get("kind") in KINDS_PINNED
                 or (row["properties"]["binding_constraint"] >= pin_gate
                     and row["properties"]["superseded"] < 0.5))
```

Pinning exists because a one-line constraint loses a straight knapsack race to a
big, obviously-relevant tool output — which is exactly how agents quietly forget
they were told not to restart production. Token costs come from character
length, in Python. Kept items are re-sorted into their original order so the
window still reads chronologically.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --budget 200            # brutal squeeze
uv run --with httpx python app.py --constraint-bonus 3.0  # protect rules harder
uv run --with httpx python app.py --json > judged.json    # re-budget offline, free
```

The last one is the point of keeping policy in Python: the judgments are the
expensive part, and the budget is not. Re-run the selection at ten different
budgets from one `--json` file without paying for inference again.

## Design notes

- **Why `superseded` multiplies rather than subtracts.** A fully superseded item
  is worth exactly nothing regardless of how interesting it looked —
  `value * (1 - superseded)` says that cleanly, and a partially-superseded item
  degrades smoothly.
- **Why `only_source` is weighted lower than `needed_now`.** Uniqueness is a weak
  reason to keep something on its own; plenty of unique items are unique
  because they are irrelevant. It matters most as a tiebreak between two items
  that are both plausibly useful.
- **Why the loss Score and not just the Nouls.** The Nouls say which properties
  hold; the Score says what it costs to be wrong about them. Its levels are
  concrete outcomes ("the assistant takes a wrong or unsafe action"), so the
  budget trade-off is legible.
- **Why the system prompt is pinned by kind, not by judgment.** Some things are
  not up for debate, and spending a judgment on them is waste.

## Limits

- **Selection cannot beat paraphrase on raw ratio.** A summariser can turn 4,000
  tokens into 200. This app can only drop whole items, so if every item is
  individually essential it has nothing to give. That is the trade: honest
  bytes at a worse compression ratio.
- **Granularity is the item.** A 3,000-token tool output is kept or dropped
  whole. Split large items in Python before passing them in if you need finer
  control.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** All token
  estimation, budgeting, sorting and totals are Python. "Supersession" is asked
  as a semantic question, not a date comparison — if your items carry
  timestamps, sort by them in Python first and trust that over the Noul.
- **Supersession is judged within one request.** If the context is large enough
  to be split against `provider.context_tokens`, an item can only be seen as
  superseded by items in the same chunk. Order your context so related items
  stay together.
- **It reads literally and degrades on large noisy state.** Sixty items of
  mostly-irrelevant history makes every judgment worse. Drop the obviously dead
  turns with a cheap rule first, then let this decide the close calls.
- **No lookahead.** It optimises for the current turn. An item that will matter
  in three turns and does not matter now will be dropped, and the agent will
  have to fetch it again.
