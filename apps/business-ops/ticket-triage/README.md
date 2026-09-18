# 🎫 Ticket Triage

Route a support queue on four independent axes — issue type, product area,
urgency, relationship risk — plus two safety flags. One request per batch.

## The problem

Support triage is done either by a rota of humans reading everything, or by a
keyword rule set that has been accreting since 2019 and that nobody dares
delete. Both fail the same way: they produce one label, usually `priority`, and
then everyone argues about it.

But triage is not one question. "It's a bug" and "it's about the API" and "they
are about to churn" are four separate facts, and different people need
different ones. Engineering wants the area. The CSM wants the churn signal. The
duty manager wants the urgency. Collapsing them into `P1/P2/P3` destroys
exactly the information each of them came for.

The classic ML answer — a multi-class classifier per axis — means four models,
four training sets, and four retraining cycles when the taxonomy changes. The
taxonomy changes every quarter.

## Why this needs Jev

**Independent judgments that stay independent, batched into one request.** Six
questions per ticket run in parallel against a state ingested once. They cannot
see each other's answers, which is the point: the urgency judgment is not
contaminated by the churn judgment.

And because the judgments are raw, the *routing* is code:

```python
if judgment["security"] >= security_gate:      queue = "security"
elif judgment["issue"] in QUEUES:              queue = QUEUES[judgment["issue"]]
```

Rewriting the routing table, adding a queue, or moving the P1 line costs
nothing — no re-inference, no retraining. That is what a keyword rule set was
trying to give you, without the keyword rule set.

## How it works

Two Choices, two Scores, two Nouls, per ticket:

```python
questions[f"issue__{index}"] = choice(
    {"task": "What kind of issue is this ticket?", "ticket": reference}, ISSUE_TYPES)
questions[f"urgency__{index}"] = score(
    {"task": "How much is blocked for this customer right now?", "ticket": reference},
    URGENCY_LEVELS)
questions[f"security__{index}"] = noul(
    {"task": "Does this ticket report a security or privacy problem...", "ticket": reference},
    true="They describe exposed data, wrong-account access, or a credential concern",
    false="No security or privacy exposure is described")
```

12 tickets × 6 judgments = 72 questions in one round trip. The footer prints
the measured cost.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve synthetic tickets ship with it, including the ones that break naive
triage: a cross-tenant data exposure filed as a billing question, a third
repeat of an unresolved integration bug with an explicit renewal threat, and a
two-word ticket that says nothing at all.

## Design notes

**Why two Choices and not one.** Issue type and product area are orthogonal —
a billing question about the mobile app is a real ticket. One combined
taxonomy would be the Cartesian product of both, which is how taxonomies grow
to 60 labels nobody can pick between.

**Every Choice carries `other` / `unclear`.** A Choice must return something.
`SUP-4423` ("hi / it doesnt work") has no product area, and without the escape
hatch the model would name one at entirely believable confidence.

**Why security and data loss are Nouls, not a third Choice.** Both can be true
at once, and each wants its own threshold. Making them options in one Choice
would force them to compete.

**Why urgency and churn risk are Scores.** They are degrees. Each level names
a concrete situation ("their customers or their revenue are being affected
right now") — a level has to be usable without reading the one above it.

**Confidence is captured but not acted on.** `issue_conf` is stored for
auditing. It measures how concentrated the distribution is, not whether the
routing was right.

## Limits

- **One ticket in isolation.** It does not see the conversation history, the
  account's other open tickets, or the CSM's notes. `SUP-4416` only reads as a
  repeat because the customer says so in the body.
- **No arithmetic, no dates.** The model is never asked how long a ticket has
  been open or how many times something happened. Queue loads and ticket counts
  are pandas.
- **Thresholds are guesses until you calibrate them.** 0.55 for the security
  queue is deliberately low — over-routing to security is cheap, under-routing
  is not. Set these against your own labelled month.
- **`jev-1.13` reads literally.** It will not infer that "18 months of history
  is gone" is a data-loss event unless the body says data is missing. Put the
  boundary cases in the `true`/`false` criteria.
- **Not an auto-responder.** This orders and routes a queue. A human still
  answers the ticket.
