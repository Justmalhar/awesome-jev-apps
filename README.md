<div align="center">

# Awesome Jev Apps

**Real applications built on [Jev](https://typesafe.ai) — a model that cannot talk, and is more useful for it.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Provider: TypeSafe or OpenRouter](https://img.shields.io/badge/provider-TypeSafe%20%7C%20OpenRouter-green.svg)](docs/openrouter-decisions-api.md)

</div>

---

Jev is a **System One model**. It does not generate text. You hand it state and
typed questions; it returns calibrated probabilities your code consumes directly.

```python
answers = client.ask(support_ticket, {
    "churn_risk": noul("Is this customer threatening to leave?"),
    "team":       choice("Who owns this?", {"payments": ..., "platform": ...}),
    "severity":   score("How bad is the impact?", ["Cosmetic", "Degraded", "Blocked"]),
})

answers.noul("churn_risk")     # 0.91
answers.choice("team")         # "payments"
answers.confidence("team")     # 0.84
```

No prompt engineering. No JSON coaxing. No parser. No retry-because-it-
apologised-instead-of-answering.

**$0.042 per million input tokens. Output tokens are free. ~150ms.**

That is roughly three orders of magnitude under a frontier chat model, which
changes *which programs are worth writing*. Scoring every line of a contract,
every story on a feed, every transaction in five years of statements, or every
file in a repo stops being a budget conversation.

---

## The apps

Every app is **standalone** — clone one folder, add a key, run it. No repo-root
imports, no framework to learn.

| App | What it actually does | Why it needs Jev |
|---|---|---|
| [🔎 semantic-ctrl-f](apps/semantic-ctrl-f) | Ask a lease or contract a plain-English question, get the exact clause | 300 clauses scored in **one** request; cannot paraphrase what it returns |
| [📰 feed-ranker](apps/feed-ranker) | Rank today's Hacker News against your interests, in one sentence | 120 judgments in one call; sliders re-rank for **$0.00** |
| [📥 inbox-triage](apps/inbox-triage) | What in my mail actually needs me today | 5 independent judgments per message; sorts, never rewrites |
| [📄 paper-screener](apps/paper-screener) | Screen abstracts against a systematic review protocol | conjunctive veto rules, not a weighted score |
| [🔬 repo-grep](apps/repo-grep) | "Find every network call with no timeout" across a whole codebase | `ast` finds candidates, Jev judges; cheap enough for CI |
| [🏦 statement-categorizer](apps/statement-categorizer) | Categorize years of bank/card transactions | confidence gate: auto-file or ask me |
| [🧪 ci-triage](apps/ci-triage) | Real bug / flake / infra / dependency / config, across CI history | newly affordable at 20k failures |
| [🧰 agent-tool-router](apps/agent-tool-router) | Pick from a 42-tool MCP catalog without stuffing schemas into context | two-stage cascade: summaries wide, schemas narrow |

> **Status:** all eight are built. Logic is covered by offline checks in CI
> (`scripts/test_apps.py`) — question construction, decision rules, chunking,
> batching. **Live responses are not yet verified against a real API key**; run
> `scripts/smoke_test.py` first and open an issue if reality disagrees.

---

## Quickstart

```bash
git clone https://github.com/Justmalhar/awesome-jev-apps
cd awesome-jev-apps
cp .env.example .env            # add ONE key

uv run --with httpx python scripts/smoke_test.py   # verify in ~1 second
uv run --with httpx python 00-primitives/tour.py   # learn all 3 primitives
```

Then run any app:

```bash
cd apps/semantic-ctrl-f
uv run streamlit run app.py
```

---

## Pick your provider

One file decides which service — and which key — every app uses:
[`providers.toml`](providers.toml).

```toml
provider = "typesafe"       # or "openrouter"

[typesafe]
base_url    = "https://api.typesafe.ai/v1/systemone"
model       = "jev-latest"
api_key_env = "TYPESAFE_API_KEY"
context_tokens = 64000

[openrouter]
base_url    = "https://openrouter.ai/api/alpha/decisions"
model       = "typesafe/jev-1.13"
api_key_env = "OPENROUTER_API_KEY"
context_tokens = 32000
```

Override without editing it:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

Both providers speak the **identical** wire format, so no app in this repo
contains a provider branch. The OpenRouter path is undocumented upstream —
Jev is missing from the public model catalog and is rejected by
`/chat/completions`; it is served from a separate decisions endpoint. We wrote
down how it works: **[docs/openrouter-decisions-api.md](docs/openrouter-decisions-api.md)**.

⚠️ OpenRouter advertises **32k** context against TypeSafe's **64k**. Apps read
`context_tokens` from config rather than assuming.

---

## The pattern

```
┌─────────────┐   state + typed questions   ┌──────────┐
│  your code  │ ──────────────────────────▶ │   Jev    │
│             │ ◀────────────────────────── │          │
└─────────────┘   calibrated probabilities  └──────────┘
      │
      └──▶ thresholds, weights, routing, escalation  ← policy lives HERE
```

Three rules every app in this repo follows:

**1. Batch aggressively.** Independent questions over the same state go in one
request. They run in parallel against a state ingested once. TypeSafe measured
12.2× cheaper and 10× faster versus looping, with identical answers.

**2. Keep judgments raw, keep policy in code.** Jev returns evidence; your code
decides what to do. Changing a weight or a threshold then costs nothing and
re-runs no inference. Bake policy into the question and every tuning change is
a new API bill.

**3. Confidence is an abstain signal, not a correctness score.** It measures how
concentrated the distribution is — not whether the workflow is right, and not
permission to act. A Noul at 0.5 means genuinely split, *not* "medium".

---

## What Jev is bad at

Stated plainly, because a repo that only lists wins is marketing. From
TypeSafe's own [jaggedness notes](https://docs.typesafe.ai/model-jaggedness/jev-1.13):

- **Cannot count or do arithmetic.** Count in code; ask one question per item and sum.
- **Cannot compare dates.** Extract components, compare in Python.
- **Reads literally.** It answers the question you wrote, not the one you meant.
  If you find yourself explaining what you really meant, that explanation is the
  missing half of your instruction.
- **Degrades on indirection** and on large state full of irrelevant detail. Filter first.
- **Cannot generate.** Obviously — but it also means no summaries, no rewriting,
  no explanations. Pair it with a generative model when you need prose.

No app here does arithmetic, counting, or date math in the model. That is not an
accident.

---

## Contributing

New apps welcome. The bar: it must be a **workflow someone actually runs**, not a
benchmark, and it must exploit something structural about Jev — parallel
questions over one state, calibrated abstention, the cost delta, 150ms latency,
or the fact that a model which cannot generate cannot invent a value.

"An LLM app, but with Jev" is not interesting. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

## Links

- [TypeSafe docs](https://docs.typesafe.ai) · [Primitives](https://docs.typesafe.ai/primitives) · [Cookbooks](https://docs.typesafe.ai/llms.txt)
- [Jev on OpenRouter](https://openrouter.ai/typesafe/jev-1.13)

<div align="center">
<sub>Built by <a href="https://github.com/Justmalhar">@justmalhar</a></sub>
</div>
