<div align="center">

# Awesome Jev Apps

### 100 ways to use [Jev](https://typesafe.ai) — a model that can't talk, and is more useful for it.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Provider: TypeSafe or OpenRouter](https://img.shields.io/badge/provider-TypeSafe%20%7C%20OpenRouter-green.svg)](docs/openrouter-decisions-api.md)
[![Runnable apps](https://img.shields.io/badge/apps-100%20planned-orange.svg)](#the-catalog)

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

> **$0.042 per million input tokens. Output tokens are free. ~150ms.**

That is roughly three orders of magnitude under a frontier chat model, which
changes *which programs are worth writing*. Scoring every clause in a contract,
every story on a feed, every transaction in five years of statements, every
function in a repo, or every abstract in a systematic review stops being a
budget conversation.

**This repo is 100 of those programs.**

---

## The catalog

<!-- CATALOG:START -->
**100 of 100 built** &nbsp; `████████████████████████████`

| Category | Apps | Built | What it covers |
|---|---:|---:|---|
| 🛠️ **[Developer Tools](apps/developer-tools)** | 11 | 11 | Semantic checks over code, CI, and repo history. Cheap enough to run on every commit. |
| 📄 **[Documents & Contracts](apps/documents)** | 11 | 11 | Ask documents questions, diff them, and check them against requirements. Nothing is ever paraphrased. |
| 🗄️ **[Data Engineering](apps/data)** | 12 | 12 | Judgment applied per-row, per-pair, and per-field, at a price that makes whole-dataset passes ordinary. |
| 🧠 **[AI Infrastructure · Jev + LLM](apps/ai-infrastructure)** | 13 | 13 | Jev sitting in front of, behind, and around a generative model. Guarding every call costs a rounding error. |
| 🏦 **[Finance](apps/finance)** | 10 | 10 | Every number computed in Python. The model only ever supplies judgment. |
| 📊 **[Business & Operations](apps/business-ops)** | 11 | 11 | Routing, qualification, and risk detection over the text a business already generates. |
| ⚡ **[Personal Productivity](apps/productivity)** | 10 | 10 | Your own inbox, feeds, notes, and calendar — sorted by what you actually care about. |
| 🛡️ **[Trust & Safety](apps/trust-safety)** | 7 | 7 | Company-specific, nuanced policy — not a generic toxicity score. |
| 🔬 **[Research & Science](apps/research)** | 8 | 8 | Screening, extraction, and verification over literature and field data. |
| ⏱️ **[Realtime & Interactive](apps/realtime)** | 7 | 7 | ~150ms is under human perception, so judgment can sit inside a render loop or a keystroke. |

<details>
<summary><b>All 100, by category</b></summary>

#### 🛠️ Developer Tools

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Semantic grep](apps/developer-tools/repo-grep) | Find code by describing it: 'network call with no timeout' | ast finds candidates, Jev judges; cheap enough for CI |
| ✅ [CI failure triage](apps/developer-tools/ci-triage) | Real bug / flake / infra / dependency / config across CI history | newly affordable over 20k failures |
| ✅ [PR risk scorer](apps/developer-tools/pr-risk-scorer) | Score a diff for blast radius and review depth needed | routes review effort; score once, reweight free |
| ✅ [Flaky test finder](apps/developer-tools/flaky-test-finder) | Separate genuinely flaky tests from consistently failing ones | per-test judgment, rates computed in Python |
| ✅ [Issue deduplicator](apps/developer-tools/issue-deduper) | Find the three issues that are the same bug | pairwise Score whose levels are the actions |
| ✅ [Breaking change detector](apps/developer-tools/api-breaking-change) | Flag diffs that break downstream consumers | diff in code, materiality in Jev |
| ✅ [Changelog classifier](apps/developer-tools/changelog-classifier) | Sort merged PRs into user-facing / internal / breaking | selection not generation; code assembles the notes |
| ✅ [Log pattern miner](apps/developer-tools/log-pattern-miner) | Cluster millions of log lines into named failure modes | map-reduce at $0.042/Mtok |
| ✅ [Dependency auditor](apps/developer-tools/dependency-audit) | Judge whether a changelog entry affects your usage | avoids upgrading blind on every release |
| ✅ [Test gap finder](apps/developer-tools/test-gap-finder) | Which behaviours in this module have no test | parser lists behaviours, Jev matches coverage |
| ✅ [Incident tagger](apps/developer-tools/incident-tagger) | Tag postmortems by cause class and contributing factors | independent Nouls, not one taxonomy |

#### 📄 Documents & Contracts

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Semantic Ctrl-F](apps/documents/semantic-ctrl-f) | Ask a lease or ToS a plain-English question, get the exact clause | whole document scored in one request; returns verbatim text |
| ✅ [Contract diff](apps/documents/contract-diff) | 'Our terms have changed' — what actually got worse for you | difflib computes the diff, Jev judges materiality |
| ✅ [RFP compliance matrix](apps/documents/rfp-compliance) | Check a proposal covers all 60 requirements in a tender | one Noul per requirement, conjunctive coverage |
| ✅ [Policy gap finder](apps/documents/policy-gap-finder) | Which controls in a standard are unaddressed by your policy | requirement-by-requirement veto rules |
| ✅ [Invoice extractor](apps/documents/invoice-extractor) | Pull totals, dates, and line items from messy invoices | regex finds candidates, Jev selects; verbatim guarantee |
| ✅ [Meeting action extractor](apps/documents/meeting-actions) | Commitments, owners, and deadlines from a transcript | selection over generation; no invented owners |
| ✅ [Spec ambiguity finder](apps/documents/spec-ambiguity) | Sentences in a spec that two engineers would read differently | the failure mode Jev itself has, turned into a tool |
| ✅ [Doc freshness auditor](apps/documents/doc-freshness) | Which docs contradict the current codebase | cross-source consistency judgment |
| ✅ [Clause library matcher](apps/documents/clause-library) | Match incoming contract clauses to your approved library | rerank against a known-good set |
| ✅ [Redaction checker](apps/documents/redaction-checker) | Find PII and confidential detail before a document goes out | one Noul per hazard class, thresholded separately |
| ✅ [Translation QA](apps/documents/translation-qa) | Flag translations that changed meaning, not just wording | two states compared, judgment on divergence |

#### 🗄️ Data Engineering

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Entity resolver](apps/data/entity-resolver) | Dedupe two merged CRM or product catalogs | Score levels ARE the actions; blocking in code |
| ✅ [Semantic data linter](apps/data/data-linter) | Find rows whose fields contradict each other | cross-field consistency, not classification |
| ✅ [Deep taxonomy classifier](apps/data/deep-classifier) | File items into a 3-level taxonomy | beam search over Choice probabilities |
| ✅ [ML feature extractor](apps/data/ml-features) | Turn text into features, train a model, measure the lift | judgments as features; output is a trained model |
| ✅ [Schema mapper](apps/data/schema-mapper) | Map one system's columns onto another's | pairwise field matching with a curator tier |
| ✅ [Survey response coder](apps/data/survey-coder) | Code thousands of free-text answers against a codebook | replaces weeks of qualitative coding |
| ✅ [Address normalizer](apps/data/address-normalizer) | Reconcile inconsistent address formats to one record | selection from parsed candidates |
| ✅ [Product catalog normalizer](apps/data/catalog-normalizer) | Unify attributes across inconsistent supplier feeds | extraction plus taxonomy in one pass |
| ✅ [Column type inferrer](apps/data/column-inferrer) | What IS this column, semantically, given its values | code samples values, Jev names the concept |
| ✅ [Anomaly explainer](apps/data/anomaly-narrator) | Which detected outliers are real problems vs expected | statistics in code, plausibility in Jev |
| ✅ [PII column scanner](apps/data/pii-scanner) | Which columns in a warehouse carry personal data | whole-warehouse scan is now affordable |
| ✅ [Training label auditor](apps/data/label-auditor) | Find mislabelled rows in an existing labelled dataset | disagreement between label and content |

#### 🧠 AI Infrastructure · Jev + LLM

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Agent tool router](apps/ai-infrastructure/agent-tool-router) | Pick from a 42-tool MCP catalog without stuffing schemas into context | two-stage cascade: summaries wide, schemas narrow |
| ✅ [LLM guardrails](apps/ai-infrastructure/llm-guardrails) | Screen every input, output, and tool call for hazards | cheap enough to guard EVERY call, which nobody does today |
| ✅ [Injection detector](apps/ai-infrastructure/prompt-injection-detector) | Catch instructions hidden in retrieved or user content | runs inline at 150ms |
| ✅ [RAG reranker](apps/ai-infrastructure/rag-reranker) | Drop-in for a cross-encoder or Cohere Rerank | cross-encoding quality at embedding-tier cost |
| ✅ [RAG passage filter](apps/ai-infrastructure/rag-passage-filter) | Drop passages that contradict, mislead, or carry injections | one judgment per retrieved passage |
| ✅ [Model router](apps/ai-infrastructure/model-router) | Send easy turns to a cheap model, hard ones to a frontier one | calibrated difficulty plus an abstain path |
| ✅ [Citation verifier](apps/ai-infrastructure/citation-verifier) | Check each claim against the source it cites | affordable per-claim verification |
| ✅ [Grounding checker](apps/ai-infrastructure/hallucination-detector) | Is this generated answer supported by the retrieved context | per-sentence grounding at scale |
| ✅ [Agent trace classifier](apps/ai-infrastructure/agent-trace-classifier) | Classify why agent runs failed, across thousands of traces | map-reduce over run history |
| ✅ [Eval judge](apps/ai-infrastructure/eval-judge) | Score model outputs against a rubric, reproducibly | calibrated and deterministic where an LLM judge drifts |
| ✅ [Agent memory filter](apps/ai-infrastructure/memory-filter) | Which stored memories are relevant to this turn | rank the whole memory store in one request |
| ✅ [Context compressor](apps/ai-infrastructure/context-compressor) | Select which context to keep when the window is tight | selection, so nothing is paraphrased away |
| ✅ [Tool output validator](apps/ai-infrastructure/tool-output-validator) | Did this tool actually return what was asked for | catches silent tool failures agents ignore |

#### 🏦 Finance

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Statement categorizer](apps/finance/statement-categorizer) | Categorize years of bank and card transactions | confidence gate: auto-file or ask me |
| ✅ [Subscription auditor](apps/finance/subscription-auditor) | Find every recurring charge, including the forgotten ones | recurrence judged, totals computed in pandas |
| ✅ [Expense policy checker](apps/finance/expense-policy-checker) | Which claims breach the written expense policy | one Noul per policy rule, veto semantics |
| ✅ [Receipt triage](apps/finance/receipt-triage) | Match receipts to card transactions and flag the gaps | pairwise matching with a curator tier |
| ✅ [Invoice matcher](apps/finance/invoice-matcher) | Three-way match: PO, invoice, delivery note | pairwise agreement judgment, arithmetic in code |
| ✅ [Vendor risk screener](apps/finance/vendor-risk) | Screen suppliers against risk criteria from their documents | independent risk Nouls, thresholded separately |
| ✅ [Deduction classifier](apps/finance/tax-classifier) | Which expenses are plausibly deductible, with a review queue | confidence decides what a human sees |
| ✅ [Fraud signal extractor](apps/finance/fraud-signals) | Turn transaction narratives into risk features | features for a downstream fraud model |
| ✅ [Earnings call tagger](apps/finance/earnings-tagger) | Tag guidance, hedging, and tone shifts across transcripts | whole-transcript scan per company |
| ✅ [Variance explainer](apps/finance/budget-variance) | Classify why each line missed budget, from the notes | numbers in code, causes in Jev |

#### 📊 Business & Operations

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Lead qualifier](apps/business-ops/lead-qualifier) | Score inbound leads against your ICP, written as a paragraph | score once, reweight as the ICP changes |
| ✅ [Support ticket triage](apps/business-ops/ticket-triage) | Route by issue, product area, urgency, and churn risk | independent judgments, one request |
| ✅ [Churn signal extractor](apps/business-ops/churn-signals) | Early warning from support and sales conversations | features feeding a churn model |
| ✅ [Escalation detector](apps/business-ops/escalation-detector) | Which threads need a manager before they blow up | calibrated urgency plus abstention |
| ✅ [NPS comment coder](apps/business-ops/nps-coder) | Theme thousands of open-ended survey comments | replaces manual coding |
| ✅ [Sales call tagger](apps/business-ops/sales-call-tagger) | Objections, competitors, and next steps from call notes | independent Nouls per signal |
| ✅ [Renewal risk scorer](apps/business-ops/renewal-risk) | Which accounts are quietly drifting toward non-renewal | composite scoring from raw judgments |
| ✅ [Application screener](apps/business-ops/candidate-screener) | Screen applications against explicit, job-related criteria | criterion-by-criterion, auditable, with a human tier |
| ✅ [RFP response scorer](apps/business-ops/vendor-rfp-scorer) | Score supplier responses against weighted criteria | weights tuned without re-running inference |
| ✅ [Onboarding gap finder](apps/business-ops/onboarding-gaps) | Which steps a new hire's docs never actually explain | coverage checking against a checklist |
| ✅ [Competitor monitor](apps/business-ops/competitor-monitor) | Which competitor updates actually matter to your roadmap | relevance filtering over a firehose |

#### ⚡ Personal Productivity

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Inbox triage](apps/productivity/inbox-triage) | What in my mail actually needs me today | 5 independent judgments per message; sorts, never rewrites |
| ✅ [Feed ranker](apps/productivity/feed-ranker) | Rank Hacker News against your interests, in one sentence | score once, reweight for $0.00 |
| ✅ [Calendar triage](apps/productivity/calendar-triage) | Which meetings you could skip or shorten | judged against your stated priorities |
| ✅ [Reading queue ranker](apps/productivity/reading-queue) | Order a 400-item read-later pile by what you need now | whole queue ranked in one request |
| ✅ [Note linker](apps/productivity/note-linker) | Find which existing notes a new note should link to | pairwise relevance over a vault |
| ✅ [Channel digest](apps/productivity/slack-digest) | What happened in 12 channels that concerns you | relevance filter, not a summary |
| ✅ [Task prioritizer](apps/productivity/task-prioritizer) | Re-rank a task list against this week's actual goal | goal changes, judgments are reused |
| ✅ [Newsletter filter](apps/productivity/newsletter-filter) | Keep the three items in a digest you'd have read | per-item judgment at negligible cost |
| ✅ [Bookmark organizer](apps/productivity/bookmark-organizer) | File years of bookmarks into a taxonomy you describe | hierarchical classification |
| ✅ [Standup assembler](apps/productivity/standup-synth) | Pull your actual week from commits, PRs and tickets | selection; code assembles the summary |

#### 🛡️ Trust & Safety

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Community moderator](apps/trust-safety/community-moderator) | Apply YOUR written community rules, not a generic policy | severity and confidence combine to allow/warn/review/block |
| ✅ [Marketplace listing checker](apps/trust-safety/listing-policy) | Prohibited items, counterfeit signals, policy breaches | one Noul per policy, thresholded separately |
| ✅ [Review authenticity](apps/trust-safety/review-authenticity) | Flag incentivised, templated, or fake-looking reviews | pattern judgment across a corpus |
| ✅ [Brand safety checker](apps/trust-safety/ad-brand-safety) | Is this placement context safe for this creative | two states judged for compatibility |
| ✅ [Scam pattern detector](apps/trust-safety/scam-detector) | Advance-fee, impersonation, and phishing patterns in messages | independent hazard Nouls |
| ✅ [Age suitability rater](apps/trust-safety/age-appropriateness) | Rate content against an age-band rubric you define | ordered rubric with concrete levels |
| ✅ [Moderation appeal router](apps/trust-safety/appeal-router) | Which appeals plausibly overturn the original decision | second-opinion judgment with abstention |

#### 🔬 Research & Science

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Paper screener](apps/research/paper-screener) | Screen abstracts against a systematic review protocol | conjunctive veto rules, not a weighted score |
| ✅ [Study data extractor](apps/research/data-extractor) | Pull population, intervention, and outcomes from methods sections | selection from parsed candidates |
| ✅ [Interview coder](apps/research/interview-coder) | Apply a thematic codebook to transcripts | one Noul per theme; several can apply |
| ✅ [Methods gap finder](apps/research/method-gap-finder) | Missing controls, sample sizes, or ethics statements | checklist as independent conditions |
| ✅ [Reproducibility auditor](apps/research/reproducibility-auditor) | Does this paper give enough detail to reproduce it | criterion-by-criterion with a human tier |
| ✅ [Dataset license checker](apps/research/dataset-license) | Can we legally use this dataset the way we intend | intent judged against licence text |
| ✅ [Grant fit scorer](apps/research/grant-fit) | Which calls actually fit this proposal | relevance ranking over a call catalog |
| ✅ [Reviewer matcher](apps/research/peer-review-router) | Match submissions to reviewers by expertise | pairwise relevance at conference scale |

#### ⏱️ Realtime & Interactive

| App | What it does | Why it needs Jev |
|---|---|---|
| ✅ [Semantic command palette](apps/realtime/intent-palette) | Type what you want in English, get the right action | runs per keystroke at typing speed |
| ✅ [Live chat moderation](apps/realtime/live-moderation) | Moderate a live stream chat as it arrives | inline latency, per-message cost |
| ✅ [Semantic form validator](apps/realtime/form-validator) | 'That doesn't look like a job title' — as they type | validation regex cannot express |
| ✅ [Text adventure referee](apps/realtime/text-adventure) | An NPC that judges whether your improvised action works | programmable common sense in a game loop |
| ✅ [Live meeting copilot](apps/realtime/meeting-copilot) | Flag commitments and dodged questions during the call | streaming transcript, judged per utterance |
| ✅ [Suggestion ranker](apps/realtime/autocomplete-ranker) | Rank completions by what the user actually means | whole candidate set ranked in one call |
| ✅ [Live alert router](apps/realtime/alert-router) | Page, ticket, or ignore — decided at alert time | decision inside the alerting path |

</details>
<!-- CATALOG:END -->

---

## Quickstart

You need [`uv`](https://docs.astral.sh/uv/) and Python 3.11+. Nothing else —
there is no repo-wide virtualenv and nothing to `pip install`.

```bash
git clone https://github.com/Justmalhar/awesome-jev-apps
cd awesome-jev-apps
cp .env.example .env            # add ONE key
```

**1. Check your key works** (costs a fraction of a cent):

```bash
uv run --with httpx python scripts/smoke_test.py
```

**2. Learn the three primitives** — five minutes, one file:

```bash
uv run --with httpx python 00-primitives/tour.py
```

**3. Run any app:**

```bash
cd apps/documents/semantic-ctrl-f
uv run streamlit run app.py
```

Every app ships sample data, so it runs before you upload anything of your own.

---

## Repository layout

```
awesome-jev-apps/
├── catalog.json        ← the 100, as data. Indexes are generated from it.
├── providers.toml      ← which provider + which key every app uses
├── .env.example        ← copy to .env, add ONE key
│
├── 00-primitives/      ← START HERE. Noul, Choice, Score in one request.
├── apps/<category>/    ← the applications, each standalone
├── _shared/            ← source of truth for the client (apps get copies)
├── scripts/            ← smoke test, sync, catalog build, offline checks
├── evals/              ← reproducible benchmarks (see its README)
└── docs/               ← the app spec, and the OpenRouter finding
```

| Directory | Read it for |
|---|---|
| [`00-primitives/`](00-primitives) | What Noul / Choice / Score are and when to use each |
| [`apps/`](apps) | How to run any app, and the patterns they all share |
| [`_shared/`](_shared) | Provider resolution, and why the client is duplicated |
| [`scripts/`](scripts) | What each script does and what CI runs |
| [`evals/`](evals) | The bar for a benchmark that belongs here |
| [`docs/`](docs) | [**The app spec**](docs/APP_SPEC.md) · [calling Jev via OpenRouter](docs/openrouter-decisions-api.md) |

Also [`CONTRIBUTING.md`](CONTRIBUTING.md) for the editorial bar, and
[`AGENTS.md`](AGENTS.md) if you're an AI agent working in this repo.

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

Both providers speak the **identical** wire format, so no app contains a
provider branch. The OpenRouter path is undocumented upstream — Jev is missing
from the public model catalog and rejected by `/chat/completions`; it is served
from a separate decisions endpoint. We wrote down how it works:
**[docs/openrouter-decisions-api.md](docs/openrouter-decisions-api.md)**.

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

Six rules every app in this repo follows. They are enforced by
[`docs/APP_SPEC.md`](docs/APP_SPEC.md) and, where machine-checkable, by CI.

1. **Batch aggressively.** Independent questions over the same state go in one
   request, running in parallel against state ingested once. TypeSafe measured
   12.2× cheaper and 10× faster versus looping, with identical answers.
2. **Every Choice gets a no-match option.** A Choice *must* return something.
   Without `none`/`unclear` it nominates the least-wrong option at entirely
   plausible confidence.
3. **Independent properties are separate Nouls** — never levels on one rubric.
   Collapsing them destroys separate thresholding.
4. **Score levels describe concrete situations.** `["low","medium","high"]` is
   not a rubric.
5. **Policy lives in code.** Thresholds and weights in Python, so tuning costs
   nothing and re-runs no inference.
6. **Confidence is an abstain signal**, not a correctness score. A Noul at 0.5
   means genuinely split, *not* "medium".

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
- **Cannot generate.** No summaries, no rewriting. Pair it with a generative
  model when you need prose.

No app here does arithmetic, counting, or date maths in the model — and
**CI fails the build** if a question tries to. That is not an accident.

---

## Verifying a change

No API key needed, so it works on forks:

```bash
python scripts/sync_provider.py --check                  # app client copies current
python scripts/build_catalog.py --check                  # indexes match reality
uv run --with httpx python _shared/jev_provider.py       # client self-check
uv run --with httpx --with streamlit --with pandas \
  python scripts/test_apps.py                            # every app's logic
```

That is exactly what [CI](.github/workflows/ci.yml) runs. It verifies **logic**,
not live responses — `scripts/smoke_test.py` is the only thing that touches the API.

## Contributing

Unbuilt rows in the catalog are open. The bar: a **workflow someone actually
runs**, not a benchmark, exploiting something structural about Jev — parallel
questions over one state, calibrated abstention, the cost delta, 150ms latency,
or the fact that a model which cannot generate cannot invent a value.

"An LLM app, but with Jev" is the thing this repo exists *not* to be. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/APP_SPEC.md](docs/APP_SPEC.md).

## License

MIT — see [LICENSE](LICENSE).

## Links

- [TypeSafe docs](https://docs.typesafe.ai) · [Primitives](https://docs.typesafe.ai/primitives) · [Cookbooks](https://docs.typesafe.ai/llms.txt)
- [Jev on OpenRouter](https://openrouter.ai/typesafe/jev-1.13)

<div align="center">
<sub>Built by <a href="https://github.com/Justmalhar">@justmalhar</a></sub>
</div>
