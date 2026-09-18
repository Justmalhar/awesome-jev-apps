# 🧪 hallucination-detector

Is this generated answer actually grounded in the retrieved context? Judged
**per sentence**, so you find out *which* sentence went wrong.

```bash
uv run --with httpx python app.py
```

```
── a-fabricated (invents a specific) → FAIL ──
  ❔ unsupported   harm 2.20  The migration slipped because of a defect in the vendor's tax engine...
  🚩 fabricated    harm 2.81  The delay cost approximately 180,000 dollars in contractor time...
       grounded=0.07 contradicts=0.31 invented_specific=0.93 hedge=0.02
  ❌ contradicted  harm 2.10  Anita Reyes was moved off the project to cover the reconciliation.
  ✅ grounded      harm 0.90  The new target is 14 November.
```

## The problem

RAG answers fail at sentence granularity, and every tool measures them at answer
granularity. A four-sentence answer where three sentences are perfect and one
invents a dollar figure scores 0.75 on a faithfulness metric — a number that
tells you nothing about what to do, because the thing you need is the one
sentence.

The three ways teams check this today all have the same hole. A second frontier
model returns a paragraph of critique you then have to parse and threshold.
NLI/entailment models are cheap but were trained on short premise-hypothesis
pairs and fall apart against four retrieved passages. Human review does not
scale past the sample.

And all three habitually punish the sentence you most want the model to write:
*"I don't have a figure for the direct cost in money."* That is the honest,
correct behaviour, and a naive grounding score marks it as the least-grounded
sentence in the answer.

## Why this needs Jev

Grounding is a **per-sentence, multi-property** judgment, and there are a lot of
sentences. A production RAG system generating a few hundred thousand answers a
month is millions of sentences. At $0.042 per million input tokens with output
free, checking every sentence of every answer costs less than the retrieval step
that produced the context.

The structural property is that **each property comes back as its own calibrated
number**. `grounded=0.07, contradicts=0.31, invented_specific=0.93` is a
fabrication. `grounded=0.07, contradicts=0.85, invented_specific=0.10` is a
contradiction. `grounded=0.07, hedge=0.91` is the model being appropriately
honest. A single faithfulness score maps all three onto "about 0.1" and loses
the only distinction that changes what you do next.

## How it works

Sentences are split **in Python** — `jev-1.13` cannot count or enumerate, and
doing the segmentation first means the indices in the output point at text a
reviewer can actually read:

```python
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
```

Then four Nouls and one Score per sentence, against the shared context:

```python
PROPERTIES = {
    "grounded":          "Is this sentence supported by the provided context passages?",
    "contradicts":       "Does this sentence conflict with what the provided context passages say?",
    "invented_specific": "Does this sentence introduce a name, figure, date, identifier or "
                         "quotation that is absent from the provided context passages?",
    "hedge":             "Is this sentence the assistant talking about its own knowledge "
                         "rather than making a claim about the subject?",
}
```

`hedge` exists to protect the honest refusal. It is checked first in `classify`,
so "I don't have a figure for that" is labelled `hedge` and excluded from the
grade entirely rather than counted as the worst sentence in the answer.

The Score asks what happens to a reader who *relies* on the sentence, which is
what separates a fabricated ticket number from a fabricated dollar figure. The
roll-up uses it:

```python
if any(label in {"contradicted", "fabricated"} and harm >= harm_gate for label, harm in bad):
    return "fail"
if len(bad) / len(scored) > bad_sentence_fraction:
    return "fail"
```

Every sentence of every candidate answer goes in **one request** (split only if
the state exceeds `provider.context_tokens`).

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python app.py
uv run --with httpx python app.py --harm-gate 0.5      # fail on any fabrication at all
uv run --with httpx python app.py --json | jq '.verdicts'
echo $?                                                 # 1 if any answer fails
```

The bundled sample ships three answers to the same question — one faithful, one
that invents specifics, one that quietly swaps who did what. Nothing calls an
LLM to produce them; they are data, so the app runs cold with only a Jev key.

## Design notes

- **Why `grounded` and `contradicts` are separate Nouls.** Most ungrounded
  sentences are not contradicted — the context is just silent. Those two
  situations need different handling (one is a retrieval gap, the other is a
  model error) and one dial cannot express both.
- **Why `invented_specific` is its own question.** It is the highest-precision
  hallucination signal there is. A sentence that introduces a proper noun or a
  figure absent from the context is wrong far more often than a sentence that is
  merely unsupported, and it is the one a reader is most likely to quote.
- **Why the harm Score is per sentence, not per answer.** An answer's risk is
  driven by its worst sentence, not its average one. Scoring at the answer level
  lets three safe sentences mask one dangerous one.
- **Why the grade has a `review` state.** A harmless fabrication is not a
  production incident. Forcing every check into pass/fail is how gates get
  switched off.

## Limits

- **Grounded is not true.** This measures faithfulness to the retrieved context.
  If retrieval returned a wrong document, a perfectly grounded answer is still
  wrong. Pair it with `rag-passage-filter` upstream.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** Sentence
  splitting, the bad-sentence fraction, and every tally on screen are Python. A
  sentence whose correctness *is* an arithmetic claim ("that is a 23% increase")
  cannot be verified here — compute it in Python and check the computed
  statement.
- **Regex sentence splitting is approximate.** Abbreviations, decimals inside
  prose and bulleted output will occasionally split badly. Swap `split_sentences`
  for a real segmenter if your outputs are messy; nothing else changes.
- **Sentence-level judgment loses discourse.** "It was down for six days." is
  unresolvable without the previous sentence. The shared context carries the
  question and the passages, but not the preceding sentence, so pronoun-heavy
  answers will over-flag. Widening the unit to a clause window is the fix, at
  more tokens.
- **It degrades on large noisy context.** Twenty passages of mostly-irrelevant
  retrieval makes every judgment worse. Filter before you check.
- **Calibrated is not correct.** These are probabilities. The gate decides what
  to do with them; keep the review queue for the middle.
