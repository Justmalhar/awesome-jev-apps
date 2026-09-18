# apps/

Eight runnable applications. Each folder is **self-contained** — clone any one
of them out of this repo on its own and it still works.

## Running any app

Every app follows the same three steps:

```bash
cd apps/<app-name>
cp ../../.env.example .env     # add ONE key: TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py    # or: uv run --with httpx python <script>.py
```

`uv` reads each app's own `pyproject.toml`, so dependencies are installed per
app. There is no repo-wide virtualenv to create and nothing to `pip install`.

## The catalog

<!-- CATALOG:START -->
**21 of 100 built.**

### 🛠️ [Developer Tools](developer-tools) — 4/11

Semantic checks over code, CI, and repo history. Cheap enough to run on every commit.

- **[Semantic grep](developer-tools/repo-grep)** — Find code by describing it: 'network call with no timeout'
- **[CI failure triage](developer-tools/ci-triage)** — Real bug / flake / infra / dependency / config across CI history
- **[PR risk scorer](developer-tools/pr-risk-scorer)** — Score a diff for blast radius and review depth needed
- **[Flaky test finder](developer-tools/flaky-test-finder)** — Separate genuinely flaky tests from consistently failing ones
- Issue deduplicator *(planned)* — Find the three issues that are the same bug
- Breaking change detector *(planned)* — Flag diffs that break downstream consumers
- Changelog classifier *(planned)* — Sort merged PRs into user-facing / internal / breaking
- Log pattern miner *(planned)* — Cluster millions of log lines into named failure modes
- Dependency auditor *(planned)* — Judge whether a changelog entry affects your usage
- Test gap finder *(planned)* — Which behaviours in this module have no test
- Incident tagger *(planned)* — Tag postmortems by cause class and contributing factors

### 📄 [Documents & Contracts](documents) — 4/11

Ask documents questions, diff them, and check them against requirements. Nothing is ever paraphrased.

- **[Semantic Ctrl-F](documents/semantic-ctrl-f)** — Ask a lease or ToS a plain-English question, get the exact clause
- **[Contract diff](documents/contract-diff)** — 'Our terms have changed' — what actually got worse for you
- **[RFP compliance matrix](documents/rfp-compliance)** — Check a proposal covers all 60 requirements in a tender
- Policy gap finder *(planned)* — Which controls in a standard are unaddressed by your policy
- Invoice extractor *(planned)* — Pull totals, dates, and line items from messy invoices
- Meeting action extractor *(planned)* — Commitments, owners, and deadlines from a transcript
- **[Spec ambiguity finder](documents/spec-ambiguity)** — Sentences in a spec that two engineers would read differently
- Doc freshness auditor *(planned)* — Which docs contradict the current codebase
- Clause library matcher *(planned)* — Match incoming contract clauses to your approved library
- Redaction checker *(planned)* — Find PII and confidential detail before a document goes out
- Translation QA *(planned)* — Flag translations that changed meaning, not just wording

### 🗄️ [Data Engineering](data) — 1/12

Judgment applied per-row, per-pair, and per-field, at a price that makes whole-dataset passes ordinary.

- **[Entity resolver](data/entity-resolver)** — Dedupe two merged CRM or product catalogs
- Semantic data linter *(planned)* — Find rows whose fields contradict each other
- Deep taxonomy classifier *(planned)* — File items into a 3-level taxonomy
- ML feature extractor *(planned)* — Turn text into features, train a model, measure the lift
- Schema mapper *(planned)* — Map one system's columns onto another's
- Survey response coder *(planned)* — Code thousands of free-text answers against a codebook
- Address normalizer *(planned)* — Reconcile inconsistent address formats to one record
- Product catalog normalizer *(planned)* — Unify attributes across inconsistent supplier feeds
- Column type inferrer *(planned)* — What IS this column, semantically, given its values
- Anomaly explainer *(planned)* — Which detected outliers are real problems vs expected
- PII column scanner *(planned)* — Which columns in a warehouse carry personal data
- Training label auditor *(planned)* — Find mislabelled rows in an existing labelled dataset

### 🧠 [AI Infrastructure · Jev + LLM](ai-infrastructure) — 2/13

Jev sitting in front of, behind, and around a generative model. Guarding every call costs a rounding error.

- **[Agent tool router](ai-infrastructure/agent-tool-router)** — Pick from a 42-tool MCP catalog without stuffing schemas into context
- **[LLM guardrails](ai-infrastructure/llm-guardrails)** — Screen every input, output, and tool call for hazards
- Injection detector *(planned)* — Catch instructions hidden in retrieved or user content
- RAG reranker *(planned)* — Drop-in for a cross-encoder or Cohere Rerank
- RAG passage filter *(planned)* — Drop passages that contradict, mislead, or carry injections
- Model router *(planned)* — Send easy turns to a cheap model, hard ones to a frontier one
- Citation verifier *(planned)* — Check each claim against the source it cites
- Grounding checker *(planned)* — Is this generated answer supported by the retrieved context
- Agent trace classifier *(planned)* — Classify why agent runs failed, across thousands of traces
- Eval judge *(planned)* — Score model outputs against a rubric, reproducibly
- Agent memory filter *(planned)* — Which stored memories are relevant to this turn
- Context compressor *(planned)* — Select which context to keep when the window is tight
- Tool output validator *(planned)* — Did this tool actually return what was asked for

### 🏦 [Finance](finance) — 2/10

Every number computed in Python. The model only ever supplies judgment.

- **[Statement categorizer](finance/statement-categorizer)** — Categorize years of bank and card transactions
- **[Subscription auditor](finance/subscription-auditor)** — Find every recurring charge, including the forgotten ones
- Expense policy checker *(planned)* — Which claims breach the written expense policy
- Receipt triage *(planned)* — Match receipts to card transactions and flag the gaps
- Invoice matcher *(planned)* — Three-way match: PO, invoice, delivery note
- Vendor risk screener *(planned)* — Screen suppliers against risk criteria from their documents
- Deduction classifier *(planned)* — Which expenses are plausibly deductible, with a review queue
- Fraud signal extractor *(planned)* — Turn transaction narratives into risk features
- Earnings call tagger *(planned)* — Tag guidance, hedging, and tone shifts across transcripts
- Variance explainer *(planned)* — Classify why each line missed budget, from the notes

### 📊 [Business & Operations](business-ops) — 1/11

Routing, qualification, and risk detection over the text a business already generates.

- **[Lead qualifier](business-ops/lead-qualifier)** — Score inbound leads against your ICP, written as a paragraph
- Support ticket triage *(planned)* — Route by issue, product area, urgency, and churn risk
- Churn signal extractor *(planned)* — Early warning from support and sales conversations
- Escalation detector *(planned)* — Which threads need a manager before they blow up
- NPS comment coder *(planned)* — Theme thousands of open-ended survey comments
- Sales call tagger *(planned)* — Objections, competitors, and next steps from call notes
- Renewal risk scorer *(planned)* — Which accounts are quietly drifting toward non-renewal
- Application screener *(planned)* — Screen applications against explicit, job-related criteria
- RFP response scorer *(planned)* — Score supplier responses against weighted criteria
- Onboarding gap finder *(planned)* — Which steps a new hire's docs never actually explain
- Competitor monitor *(planned)* — Which competitor updates actually matter to your roadmap

### ⚡ [Personal Productivity](productivity) — 2/10

Your own inbox, feeds, notes, and calendar — sorted by what you actually care about.

- Inbox triage *(planned)* — What in my mail actually needs me today
- **[Feed ranker](productivity/feed-ranker)** — Rank Hacker News against your interests, in one sentence
- **[Calendar triage](productivity/calendar-triage)** — Which meetings you could skip or shorten
- Reading queue ranker *(planned)* — Order a 400-item read-later pile by what you need now
- Note linker *(planned)* — Find which existing notes a new note should link to
- Channel digest *(planned)* — What happened in 12 channels that concerns you
- Task prioritizer *(planned)* — Re-rank a task list against this week's actual goal
- Newsletter filter *(planned)* — Keep the three items in a digest you'd have read
- Bookmark organizer *(planned)* — File years of bookmarks into a taxonomy you describe
- Standup assembler *(planned)* — Pull your actual week from commits, PRs and tickets

### 🛡️ [Trust & Safety](trust-safety) — 1/7

Company-specific, nuanced policy — not a generic toxicity score.

- **[Community moderator](trust-safety/community-moderator)** — Apply YOUR written community rules, not a generic policy
- Marketplace listing checker *(planned)* — Prohibited items, counterfeit signals, policy breaches
- Review authenticity *(planned)* — Flag incentivised, templated, or fake-looking reviews
- Brand safety checker *(planned)* — Is this placement context safe for this creative
- Scam pattern detector *(planned)* — Advance-fee, impersonation, and phishing patterns in messages
- Age suitability rater *(planned)* — Rate content against an age-band rubric you define
- Moderation appeal router *(planned)* — Which appeals plausibly overturn the original decision

### 🔬 [Research & Science](research) — 2/8

Screening, extraction, and verification over literature and field data.

- **[Paper screener](research/paper-screener)** — Screen abstracts against a systematic review protocol
- **[Study data extractor](research/data-extractor)** — Pull population, intervention, and outcomes from methods sections
- Interview coder *(planned)* — Apply a thematic codebook to transcripts
- Methods gap finder *(planned)* — Missing controls, sample sizes, or ethics statements
- Reproducibility auditor *(planned)* — Does this paper give enough detail to reproduce it
- Dataset license checker *(planned)* — Can we legally use this dataset the way we intend
- Grant fit scorer *(planned)* — Which calls actually fit this proposal
- Reviewer matcher *(planned)* — Match submissions to reviewers by expertise

### ⏱️ [Realtime & Interactive](realtime) — 2/7

~150ms is under human perception, so judgment can sit inside a render loop or a keystroke.

- **[Semantic command palette](realtime/intent-palette)** — Type what you want in English, get the right action
- **[Live chat moderation](realtime/live-moderation)** — Moderate a live stream chat as it arrives
- Semantic form validator *(planned)* — 'That doesn't look like a job title' — as they type
- Text adventure referee *(planned)* — An NPC that judges whether your improvised action works
- Live meeting copilot *(planned)* — Flag commitments and dodged questions during the call
- Suggestion ranker *(planned)* — Rank completions by what the user actually means
- Live alert router *(planned)* — Page, ticket, or ignore — decided at alert time
<!-- CATALOG:END -->

Two apps are CLIs rather than Streamlit apps, and more will be. That is
deliberate: `repo-grep` belongs in a pipe and in CI next to ripgrep, and
`agent-tool-router` is a component you wire into an agent, not something you sit
and click.

Every app ships sample data, so it runs before you upload anything of your own.

## Switching provider

Nothing app-specific. Either edit `provider` in the app's `providers.toml`, or:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

⚠️ OpenRouter serves **32k** context against TypeSafe's **64k**. Apps that pack
large state — `semantic-ctrl-f` on a long contract, `feed-ranker` at 50 stories,
`repo-grep` on a big tree — will need more requests, or will refuse, on
OpenRouter. Each app reads `context_tokens` from config rather than assuming.

## Anatomy of an app folder

```
apps/<name>/
├── app.py / <name>.py   # the whole app, usually one file
├── jev_provider.py      # GENERATED — do not edit
├── providers.toml       # GENERATED — do not edit
├── pyproject.toml       # this app's dependencies alone
├── README.md            # what it does, why it needs Jev, design notes, limits
└── sample_*.{json,csv,txt}
```

`jev_provider.py` and `providers.toml` are copies, written by
[`scripts/sync_provider.py`](../scripts). Editing a copy will be overwritten and
will fail CI. Edit [`_shared/jev_provider.py`](../_shared) and re-sync.

The duplication is the price of every folder being independently forkable, and
it is generated rather than hand-maintained. See [`_shared/README.md`](../_shared/README.md).

## Patterns these apps share

Reading any two of them will show you the house style. In short:

- **One request, many questions.** Independent questions over the same state go
  in a single call. They run in parallel against state ingested once.
- **Every Choice has an escape hatch.** `none`, `other`, `unclear`. A Choice must
  return something, so without one it nominates the least-wrong option with
  perfectly plausible confidence.
- **Independent properties are separate Nouls**, never levels on one rubric.
  Collapsing them destroys your ability to threshold them separately.
- **Score levels describe concrete situations.** `["low","medium","high"]` is not
  a rubric.
- **Policy lives in Python.** Thresholds, weights, and routing sit in code, so
  tuning costs nothing and re-runs no inference.
- **No arithmetic in the model.** `jev-1.13` cannot count or add. Every total on
  every screen is computed in Python — and CI fails the build if a question
  asks otherwise.

## Adding an app

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the editorial bar. Then:

```bash
mkdir apps/my-app
# write app.py, pyproject.toml, README.md, sample data
python scripts/sync_provider.py     # generates jev_provider.py + providers.toml
python scripts/test_apps.py         # add a check for your app first
```
