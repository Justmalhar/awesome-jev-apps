# 🔗 Entity Resolver

Two systems got merged. Now "Acme Corp", "ACME Corporation" and "Acme Corp." are
three separate customers, your ARR is wrong, and someone is about to send the
same renewal email three times.

Point this at the merged export. It returns duplicate clusters you can merge
automatically, and a shortlist of pairs a person should actually look at.

## The problem

Deduplication is the least glamorous and most expensive data job there is.
The standard options both fail in the same predictable way:

- **Fuzzy string matching** (Levenshtein, Jaro-Winkler, trigram). Fast and free,
  but it has no idea what a company is. `Stark Industries UK Ltd` and
  `Stark Industries Deutschland GmbH` score high and are *different legal
  entities*. `Globex GmbH` and `GLOBEX GMBH` in `Munich` vs `Munchen` score
  lower and are *the same company*. Tune the threshold to catch the second and
  you merge the first.
- **An LLM on every pair.** It understands the difference — and 500 records is
  124,750 pairs, so it costs more than the data is worth and takes a day.

The failure mode in production is not "the tool missed a duplicate". It is a
**bad auto-merge**, because merging two customers is very hard to undo.

## Why this needs Jev

Two structural properties, and neither is "it's an LLM but cheaper".

**1. Judgment is cheap enough to be a filter stage.** At $0.042/M input tokens
with output free, a judged pair costs a fraction of a cent. That changes the
architecture: blocking no longer has to be precise, only *recall-oriented*.
Python's `difflib` throws away the 99% that are obviously unrelated — cheaply,
with a deliberately generous cutoff — and Jev judges everything that survives.
The app prints the reduction it achieved. Blocking is where the money is saved;
the model is where the correctness comes from.

**2. A Score returns a calibrated position, not a sentence.** This is what lets
the middle tier exist at all. An LLM asked "are these the same?" returns "Yes,
these appear to be the same company" for both the true duplicate and the pair of
subsidiaries. A Score returns `1.9` and `0.3` — and, crucially, `1.1` for the
genuinely uncertain ones, which is the answer you most need.

## How it works

The distinctive bit: **the Score levels are the three things you can do**, so
there is no similarity threshold to fit.

```python
LEVELS = [
    "Clearly different real-world entities. Keeping them separate is correct.",
    "Possibly the same entity, but the records conflict or are too thin to be sure. A person should look.",
    "Confidently the same real-world entity, written differently. Safe to merge automatically.",
]

score(
    {"task": "Do these two records describe the same real-world entity?",
     "record_a": f"`pairs[{index}].a`",
     "record_b": f"`pairs[{index}].b`",
     "note": "Different branches, subsidiaries, or product variants are DIFFERENT entities."},
    LEVELS,
)
```

Read those levels as instructions to an operator. `>= 1.5` merges, `<= 0.5`
separates, and the band between them *is* the curator queue — not because 1.0 is
a low similarity score, but because level 1 literally says "a person should
look". The rubric encodes the decision, so the only thing left in Python is
where to cut, and that is a slider.

That trailing `note` is doing real work. `jev-1.13` reads literally, and without
it the subsidiary trap is genuinely ambiguous: two Stark Industries entities at
different addresses in different countries *are* "the same company" in the sense
a human means it casually. Boundary cases belong in the question, not in your
head.

Pair decisions then compose transitively through union-find in Python, so
A=B and B=C produces one cluster of three — the model is never asked to reason
about a cluster, only about a pair.

Every pair in a batch goes into **one** `ask()`. They are independent questions
over one ingested state, which is exactly the shape `ask()` is built for.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

24 records from two merged systems are bundled — five real duplicate clusters
(one of them three-way), three near-miss traps, and an honestly ambiguous pair.
Your own CSV can have any columns; every field is shown to the model.

## Design notes

**Why a Score and not a Noul.** "Same entity?" looks like a yes/no. It isn't:
the useful third state is *"the records don't contain enough to tell"*, and a
Noul at 0.5 cannot distinguish "genuinely 50/50" from "conflicting evidence" in
a way you can route on. Three named actions can.

**Why blocking stays generous.** The default cutoff is 0.45, which is loose.
Blocking is recall-oriented on purpose — a junk pair costs a fraction of a cent
and gets scored 0; a missed pair is invisible forever. Tighten it only when the
cost footer says you should.

**Why the whole record goes in, not just the name.** Address, domain and phone
are what separate `Wayne Enterprises Ltd` from `Wayne Enterprises Foundation`
(same building, different domains) and what join `Globex GmbH` to `GLOBEX GMBH`
(`Munich`/`Munchen` differ, the phone number does not). Name-only matching
throws away the evidence that makes the call.

## Limits

- **Blocking is the recall ceiling.** If two records for the same company share
  no lexical overlap at all — `IBM` and `International Business Machines`, or a
  rebrand — `difflib` never proposes the pair and Jev never sees it. Nothing
  downstream can recover it. A phonetic or embedding-based blocker would help;
  this one is deliberately the simplest thing that works.
- **O(n²) blocking.** The prefilter itself is quadratic in Python, which is fine
  for a few thousand records and not fine for a million. At that size you need
  real blocking keys (sorted-neighbourhood, min-hash), not a slider.
- **`jev-1.13` cannot compare dates or do arithmetic.** So "the record created
  later is the survivor" is not a question you can ask it. Pick the surviving
  record in Python.
- **Transitive merges can over-cluster.** Union-find will happily chain A=B=C
  into one cluster from two high-confidence pairs even when A and C would have
  scored 0. That is a property of the algorithm, not of the model. If it bites,
  score the cluster's own pairs rather than trusting the closure.
- **Confidence is not correctness.** A concentrated distribution means the model
  found the question easy, not that the merge is safe. Sample the auto-merge
  tier before you trust it on a customer table.
