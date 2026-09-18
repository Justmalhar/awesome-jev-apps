# 🗑️ rag-passage-filter

Drop the retrieved passages that would make the answer *worse* — which is a
different question from which ones are most relevant.

```bash
uv run --with httpx python app.py
```

```
▸ How do we switch Okta single sign-on back on for our Starter workspace after it stopped working in January?

🗑️  p-03  drop  damage 2.84/3  archive/release-notes/2024-03.md
     superseded_by_another_passage P=0.91, damage 2.84
     New in this release: SSO is now available to every paid plan, including Starter...
🗑️  p-07  drop  damage 3.00/3  scraped/partner-site/integrations
     carries_instructions_for_the_reader P=0.94
⚠️  p-06  warn  damage 1.90/3  community/forum/thread-9912
     misleading_without_context P=0.58
✅ p-04  keep  damage 0.12/3  docs/plans/comparison.md#superseding-note
```

## The problem

Retrieval hands the generator its top-k and the generator treats all of it as
true. That is the whole failure mode. The passages that hurt most are not the
irrelevant ones — those get ignored — they are the ones that look exactly like
good evidence:

- a release note from 2024 announcing a feature that was moved out of the plan
  two years later,
- a forum post where a customer confidently describes a setup that never
  existed,
- a blog post about how the company uses Okta *internally*, true in every
  sentence and wrong as an answer,
- a scraped partner page with an HTML comment telling the assistant what to say.

A reranker cannot help you here, because a reranker only orders by relevance and
every one of those scores as highly relevant. They *are* on topic. That is the
problem. Meanwhile the question itself carries a false premise — the workspace
never had SSO — and the only passages that can reveal that are the ones a
relevance ranking is happiest to include alongside the passages that contradict
them.

The usual mitigations are a recency filter (drops good evergreen docs, keeps
confidently wrong new ones), a source allowlist (a maintenance burden that
becomes a stale list), or an LLM pass over the context (works, and costs enough
that it runs on nothing).

## Why this needs Jev

Four different kinds of damage, **four separate Nouls, four separate
probabilities**. That separation is the point:

- `superseded_by_another_passage` at 0.9 is a **documentation bug** — someone
  should delete that release note.
- `carries_instructions_for_the_reader` at 0.9 is a **security incident** — there
  is an injection sitting in your index.
- `misleading_without_context` at 0.5 is a **corpus quality** signal — worth a
  warning label, not a deletion.

One blended "quality score" cannot route those to three different teams, and a
generative judge returns a paragraph that someone then has to turn back into
numbers. Jev returns the numbers, and the routing is four `if` statements you
own.

At **$0.042 per million input tokens, output free**, filtering ten passages
through five judgments costs a small fraction of a cent, so this runs on **every
retrieval** — including the ones that looked fine, which is where the confidently
wrong answers come from.

## How it works

Five questions per passage, all in one request. The question the user asked
travels with every candidate, because a defect is only a defect relative to what
was asked:

```python
for name, spec in DEFECTS.items():
    questions[f"{name}_{index}"] = noul(
        {"task": spec["task"], "candidate": reference},
        true=spec["true"], false=spec["false"],
    )
questions[f"damage_{index}"] = score(
    {"task": "If this passage went into the context used to answer the question, "
             "what would it do to the answer?", "candidate": reference},
    DAMAGE_LEVELS,
)
```

`superseded_by_another_passage` is the interesting one: it is answered **against
the rest of the retrieval**, which is why every passage in a group is in the
state. The model is not asked which passage is newer — it is asked whether
another passage here announces a change that overrides this one. Dates are
Python's problem, and this repo's model cannot compare them anyway.

The policy is Python, and the security defect deliberately jumps the queue:

```python
if defects[SECURITY_DEFECT] >= warn_p:
    return "drop", f"{SECURITY_DEFECT} P={defects[SECURITY_DEFECT]:.2f}"
if peak >= drop_p or damage >= drop_damage:
    return "drop", f"{worst} P={peak:.2f}, damage {damage:.2f}"
```

An embedded instruction drops at the *warn* threshold. It must not have to clear
the quality bar first — a mild-looking injection is still an injection.

## Run it

```bash
cp ../../../.env.example .env        # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run --with httpx python app.py
uv run --with httpx python app.py --drop-p 0.5 --drop-damage 1.8     # stricter
uv run --with httpx python app.py --retrieval mine.json --json > filtered.json
echo $?                              # 1 on an injection, or if nothing survived
```

Exit code 1 on two distinct conditions, both of which the generator must not be
allowed to paper over: a passage carrying reader-directed instructions, or an
empty survivor set. The second one means the answer is about to be unsupported,
and "no passage survived" is a far better outcome than a fluent answer built
from four bad ones.

## Design notes

- **Why this is not the reranker.** Relevance ordering and damage filtering are
  orthogonal. Run the filter first, then rank what is left — a passage that
  should not be in the context at all does not deserve a rank.
- **Why `damage` is a Score.** It is a degree, with four concrete rungs from "the
  answer reads the same with or without it" to "the answer becomes confidently
  wrong". A Noul cannot express that, and `["low","medium","high"]` would not
  mean the same thing to two readers.
- **Why `warn` exists.** Dropping every imperfect passage strips the corpus down
  to the docs pages and loses genuine signal. `warn` keeps the passage and labels
  it, so the generator's prompt can say which evidence is soft.
- **Why the reason string comes back from `decide`.** A dropped passage someone
  cannot audit is a dropped passage someone will stop trusting.
- **Batching, with an honest cost.** Groups are sized against
  `provider.context_tokens` (TypeSafe 64k, OpenRouter 32k). A split means
  `superseded_by_another_passage` can only see its own group, so a supersession
  across the boundary is missed. Larger groups are better here, and the budget is
  spent accordingly.

## Limits

- **`superseded_by_another_passage` only sees the retrieval.** If the passage
  that supersedes a claim was never retrieved, nothing here can know. That is a
  retrieval problem and it stays one.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** "Which of these
  is more recent" is never asked; sort `retrieved_at` in Python if you want
  recency, and treat it as a separate signal from supersession — they are not the
  same thing.
- **It reads literally.** Each defect is judged exactly as its criteria are
  written. The boundary cases belong in those strings.
- **It degrades on indirection and on large noisy state.** Each question points
  at one passage's text, but the supersession question needs the whole group in
  context, so very long passages make that judgment measurably worse. Chunk
  first.
- **Dropping is destructive.** A false-positive drop removes evidence the
  generator needed, and the user sees a worse answer with no indication why. Run
  with `--json` and log the drops before you switch this on in a live path.
- **Premise contradiction is judged, not proved.** The model is deciding whether
  a passage denies an assumption, not verifying it against ground truth. On a
  corpus that genuinely disagrees with itself, this surfaces the disagreement —
  it does not resolve it.
- **Calibrated is not correct.** These are confident numbers, not true ones.
