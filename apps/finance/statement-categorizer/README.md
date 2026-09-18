# 🏦 Statement Categorizer

Point it at a bank or card CSV export. Get every transaction categorized,
subscriptions surfaced, business expenses flagged, and a review queue of the
rows the model was genuinely unsure about.

50 transactions costs well under a cent. Five years of them still costs less
than a coffee, which is the only reason anyone would run this over a whole
account history.

## Why this needs Jev

**Jev does the judgment. Python does every single number.**

`jev-1.13` cannot count and cannot do arithmetic — documented failure modes, not
tuning problems. So the model is never asked *"how much did I spend on
groceries?"*. It is asked, once per row:

- **Choice** — which category is this?
- **Noul** — is it a recurring subscription?
- **Noul** — does it look like a business expense?

Every total, average, and chart on screen is computed by pandas from those
labels. If you ever find yourself putting a sum in a prompt, this is the app to
re-read.

## The confidence gate

A Choice **always** returns a label. That is not optional and it is not a sign
of certainty — it is how the primitive works. `SQ *PMT 4471X` will get a
category with exactly the same confidence-free enthusiasm as `TESCO SUPERSTORE`.

What separates them is the distribution:

```python
out["needs_review"] = out["confidence"] < confidence_gate
```

High confidence means probability piled onto one option. Low confidence means it
was spread — the model was guessing between plausible options. Those rows go to
a human queue instead of being silently filed.

This is what makes the difference between a categorizer people trust and one
they quietly stop using: it tells you which of its answers not to believe.

Note the asymmetry — the two Nouls have no confidence field at all. A Noul's
probability *is* its answer. `subscription = 0.5` means genuinely split, not
"sort of a subscription".

## Run it

```bash
cp ../../.env.example .env
uv run streamlit run app.py
```

A 50-row synthetic statement is bundled. Your own export needs `description` and
`amount` columns; negative amounts are treated as spending.

## Why the categories have descriptions

```python
"groceries": "Supermarkets, food markets, greengrocers. Not restaurants or takeaway."
```

That trailing exclusion is doing real work. `jev-1.13` reads literally, and a
bare label `"groceries"` leaves the boundary with `eating_out` undefined — a
supermarket meal deal could land either side. Put the boundary cases in the
criteria, not in your head.

The `"other"` category matters for the same reason: without an escape hatch, a
Choice must file everything somewhere, so genuine oddities get crammed into the
nearest category with misleading confidence.

## Tuning

The 0.6 thresholds on `subscription` and `business` are starting points. Tune
them against a few weeks of rows you've labelled yourself — and note you can
re-tune freely, because the raw probabilities are already in the output CSV.
Changing a threshold re-derives every number on screen without a single new API
call.

## Privacy

Transaction descriptions are sent to whichever provider `providers.toml`
selects. Read that provider's data policy before pointing this at a real
account. TypeSafe states it does not train on customer requests and offers zero
data retention for enterprise; verify for yourself rather than taking this
README's word for it.

## Limits

`jev-1.13` cannot count, do arithmetic, or compare dates — every figure shown by
this app is computed in Python from the model's judgments. It also reads
literally: if an answer surprises you, the missing half of the instruction is
usually the thing you would have said out loud to explain what you meant. See
the [jaggedness notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

Nothing here has been verified against a live API response yet.
