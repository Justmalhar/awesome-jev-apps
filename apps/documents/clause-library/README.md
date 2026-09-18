# 📚 Clause Library

Rerank a counterparty's clauses against your approved clause library — and find
the ones that match **nothing**.

> *8.9 Non-Solicitation of Personnel* → `none`, P = 0.81 → legal review

## The problem

Every legal team that reviews more than a handful of contracts keeps a clause
library: the approved position on liability, governing law, IP, data protection.
A counterparty sends back their own paper, and the reviewer's real job is three
questions per clause:

1. Which of our approved clauses is this *supposed* to be?
2. Is it actually our text, or has something operative moved?
3. Is there anything in here we have **never had a position on**?

Question 3 is the one that costs money, and it is the one that gets missed. A
reviewer working down a 60-clause document against a 40-clause library is doing
a mental join under time pressure. Clauses without a counterpart don't announce
themselves — a most-favoured-nation pricing clause reads like a pricing clause,
sits where a pricing clause sits, and slides past. Clause-comparison tools make
this worse by always returning a nearest match, because a ranking function
always ranks.

## Why this needs Jev

Two structural properties, neither of which is "an LLM but cheaper".

**The `none` option is the product.** A Choice must return exactly one option, so
the escape hatch is what converts a nearest-neighbour ranker into a detector:

```python
options = {entry["id"]: f"{entry['title']} -- {entry['text']}" for entry in library}
options["none"] = (
    "No approved clause in the library covers this subject matter. "
    "This is a non-standard clause with no counterpart on our paper."
)
```

Without that key, `none` is not an available answer, and the non-solicitation
clause gets filed under Confidentiality at an entirely plausible 0.4 — a wrong
answer that looks exactly like a right one. With it, "no approved counterpart"
becomes a first-class outcome that competes for probability mass on equal terms.

**Matching is selection, not generation.** The matched text is copied out of
*your* library, because the model picks an option key; it never writes one. It
is structurally incapable of returning an approved position you never approved
— which is the failure mode that makes a generative clause tool unusable in a
legal workflow.

**Embeddings can't do question 2.** Our approved liability clause and the
counterparty's inverted one are near-identical in embedding space: same
vocabulary, same structure, one cap swapped and one reciprocity removed. Cosine
similarity ranks that as an excellent match, which it is — and that is exactly
why similarity alone tells you nothing about whether to sign it.

## How it works

Per incoming clause: one Choice over the library, plus **three separate Nouls**.
They are separate because they are separate facts. A clause can match the
library subject and still be on worse terms. A clause can be novel without being
adverse. Collapsed into one rubric, none of them could be thresholded alone:

```python
questions[f"deviates_{index}"] = noul(
    {"task": "Does this incoming clause set out different substantive terms "
             "from our approved library position on the same subject?",
     "incoming_clause": clause_ref, "approved_library": "`approved_library`"},
    true="The clause covers a subject in the library but changes an operative "
         "term: a cap, a notice period, a carve-out, a remedy, a forum, or who "
         "bears an obligation",
    false="The clause is our approved position, or differs only in wording, "
          "ordering, or formatting without changing an operative term",
)
```

All of it — 10 clauses × (1 Choice + 3 Nouls) = 40 judgments — goes in **one**
`ask()`. The library is ingested once and every clause is scored against it in
parallel.

Routing is ordinary Python, ordered by escalation:

| Condition | Route |
|---|---|
| `P(novel) ≥ novel_threshold` | legal review |
| Choice returned `none` | legal review |
| Match probability below threshold | legal review |
| `P(deviates) ≥ threshold` and `P(worse) ≥ threshold` | redline queue, flagged adverse |
| `P(deviates) ≥ threshold` | redline queue |
| otherwise | auto-approve |

Novel is checked first on purpose: a redline generated against the wrong
baseline is worse than no redline at all.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

A 10-clause approved library and a 10-clause counterparty redline are bundled,
so it runs before you supply anything. The sample is built to exercise all three
routes: clauses 8.3, 8.7 and 8.8 are our text back verbatim; 8.1, 8.4, 8.5 and
8.6 map to a library entry on materially worse terms; 8.9 (non-solicitation) and
8.10 (most-favoured-nation pricing) have no counterpart at all.

## Design notes

**Why `worse_for_us` is its own Noul and not a level on `deviates`.** A rubric
would force an ordering — "deviates a bit" < "deviates a lot" < "deviates badly
for us" — that does not exist. Governing law moving from Delaware to New York is
a real deviation and roughly neutral. Payment terms moving from 45 to 90 days is
a smaller edit and much worse. Two independent facts, two Nouls, two sliders.

**Why the match threshold exists at all.** The Choice returns a winner even when
the distribution is flat. `probabilities()` is what tells you it was a coin
flip, and a flat distribution over library entries means the same thing as
`none`: nobody on your side has written this down. Both escalate.

**Why cost scales as clauses × library.** Each Choice repeats the full library
in its `criteria`, so the token estimate multiplies. The app checks that against
`provider.context_tokens` before sending and tells you to split the run rather
than letting the provider reject it. For a large library, filter to plausible
candidates in Python first.

**Thresholds default to 0.45 / 0.60 / 0.60 and should not stay there.** Tune
them against redlines your team has already done, where you know the answer.

## Limits

- **Paragraph splitting is a length floor, not a parser.** `MIN_CLAUSE_CHARS`
  drops headings and preambles. Paper with genuinely short clauses, or clauses
  split across bullet lists, needs the floor lowered or a real parser. The app
  tells you when nothing cleared the bar rather than silently analysing nothing.
- **One clause at a time, no cross-references.** A liability clause that is
  benign until you read the indemnity it points at will be judged benign. Jev
  degrades on indirection; it is reading the clause in front of it.
- **`jev-1.13` cannot count, cannot do arithmetic, and cannot compare dates.**
  "Is 90 days worse than 45 days?" is not asked of the model, and must not be —
  it is a comparison, and it would be wrong at an entirely plausible confidence.
  `worse_for_us` asks about reciprocity and allocation, which is a reading task.
  If you need numeric thresholds on notice periods or caps, extract the numbers
  with a regex and compare them in Python.
- **It reads literally.** "Worse for us" is anchored to Supplier in the question
  text. If you are on the Customer side, edit that — the app will otherwise
  cheerfully route your own favourable terms into the redline queue.
- **It degrades on large noisy state.** A 200-clause document in one run is
  worse than four runs of 50, independent of the context limit.
- **This is triage, not advice.** Auto-approve means "matches our approved text
  closely enough that a reviewer's time is better spent elsewhere". It does not
  mean signed off, and the routing thresholds are yours to defend.
