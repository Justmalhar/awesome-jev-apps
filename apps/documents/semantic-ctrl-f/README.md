# 🔎 Semantic Ctrl-F

Ask a lease, contract, or Terms of Service a plain-English question and get the
exact clause back — not a summary, not a paraphrase, **the clause**.

> *"Can I keep a cat without asking the landlord?"* → clause 7, P = 0.94

## Why this needs Jev

Ordinary Ctrl-F needs you to guess the word the lawyer used. Embedding search
returns "vibes-similar" paragraphs and quietly misses negations — *"pets are
permitted"* and *"no pet may be kept"* sit close together in embedding space.
An LLM will read the document and then **paraphrase** the clause, which is
exactly what you cannot show a landlord.

This app scores **every block in the document in a single request**:

```python
options = {str(i): block for i, block in enumerate(blocks)}
options["none"] = "No block in this document addresses the question."

questions = {
    "best_block": choice("Which block answers this question?", options),
    "has_answer": noul("Does this document answer the question at all?"),
}
```

The option *keys* are block ids; the option *descriptions* are the block text.
Jev ingests the document once and ranks all of them in one pass, returning a
probability per block. Because the model cannot generate, the text you see is
copied verbatim out of your document — it is structurally incapable of
inventing a clause that isn't there.

The `has_answer` Noul is the part most people skip. A Choice **must** pick
something, so without it the app confidently highlights the least-wrong
paragraph when the document is simply silent. Asking both in the same request
costs one extra question and no extra round trip.

## Run it

```bash
cp ../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

A sample synthetic tenancy agreement is included, so it works before you
upload anything.

## Try these against the sample

| Question | Expected |
|---|---|
| Can I keep a cat without asking? | Clause 7 — consent needed, but not unreasonably withheld |
| What happens if I pay rent on the 9th? | Clause 4 — 4% late fee, 8% on a repeat |
| Can my landlord walk in unannounced? | Clause 11 — 24h notice, except emergencies |
| How much can rent go up? | Clause 15 — max 6% per review |
| **Does it cover my bike being stolen?** | `has_answer` should drop — clause 16 covers contents insurance but not this |

That last row is the interesting one. Watch `has_answer` fall while the Choice
still nominates a block, which is precisely the failure the Noul is there to catch.

## Cost

Scoring a 37-clause document is ~1 request and a fraction of a cent. The naive
version — one request per clause — is 37 requests for the same answer.

## Known limits

`jev-1.13` reads literally and does not do arithmetic. "Is my rent more than
30% of my income?" will not work; extract the numbers and compare them in
Python. See the [jaggedness notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
