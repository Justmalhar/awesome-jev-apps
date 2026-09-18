# Wiring Jev into your coding agent

*Verified 2026-09-19 against [docs.typesafe.ai](https://docs.typesafe.ai/llms.txt),
[typesafe-ai/skills](https://github.com/typesafe-ai/skills), and Vercel's AI Gateway
docs and live model catalog. Commands below are copied from upstream, not
reconstructed. Anything unconfirmed is labelled.*

## Why bother installing a skill

An agent with no Jev context does not fail loudly — it fails plausibly. The
failure mode is always the same: it reaches for the only LLM shape it knows and
writes a chat call.

```bash
curl https://openrouter.ai/api/v1/chat/completions \
  -d '{"model":"typesafe/jev-1.13","messages":[{"role":"user","content":"..."}]}'
```

```json
{"error":{"message":"typesafe/jev-1.13 is a decisions model and cannot be used with the chat/completions endpoint. Use the /api/alpha/decisions endpoint instead.","code":400}}
```

There are no messages, no roles, and no generated text to parse. Every provider
rejects this shape on purpose (see
[`openrouter-decisions-api.md`](openrouter-decisions-api.md)). The skill replaces
the guess with the real contract — the three question types, the answer shapes,
and the [documented failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
that no amount of prompt-rewording fixes.

## Claude Code

Two commands, straight from [the installation docs](https://docs.typesafe.ai/agent-skill#installation):

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```

The names are not interchangeable and are easy to transpose. From
`.claude-plugin/marketplace.json` in that repo: the **marketplace** is
`typesafe-ai`, the **plugin** inside it is `typesafe`. Hence `typesafe@typesafe-ai`.

Verify it loaded:

```bash
claude plugin list          # expect: typesafe (typesafe-ai)
```

Then invoke it. Either works:

| How | When |
|---|---|
| `/typesafe:typesafe-ai` | Explicit. Use this when the agent is ignoring the skill. |
| "using the TypeSafe skill, ..." | Works in any agent, including Claude Code. |

Updating — the skill carries live doc links, so a stale copy is the documented
cause of an agent inventing request fields:

```bash
claude plugin marketplace update typesafe-ai
claude plugin update typesafe@typesafe-ai
```

Restart Claude Code or run `/reload-plugins` afterwards. `/plugin` →
**Marketplaces → typesafe-ai → Enable auto-update** makes this automatic.

## Codex, Cursor, and everything else

```bash
npx skills add typesafe-ai/skills --skill typesafe-ai
```

Choose your agent when prompted. Project-local by default; add `-g` for global.

Update with `npx skills update`.

Manual install, if you would rather not run an installer: copy the whole
[`skills/typesafe-ai/`](https://github.com/typesafe-ai/skills/tree/main/skills/typesafe-ai)
directory into your agent's skills directory. The
[raw SKILL.md](https://raw.githubusercontent.com/typesafe-ai/skills/main/skills/typesafe-ai/SKILL.md)
alone is also useful to paste into a context window.

**Pick one method.** Two installations means two copies of the skill, and
upstream explicitly warns against it.

## Getting a key

Three routes reach the same model. They are not equivalent.

| | TypeSafe direct | OpenRouter | Vercel AI Gateway |
|---|---|---|---|
| Model | `jev-latest` → `jev-1.13.0` | `typesafe/jev-1.13` | `typesafe-ai/jev` |
| Env var | `TYPESAFE_API_KEY` | `OPENROUTER_API_KEY` | `AI_GATEWAY_API_KEY` |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` | `POST https://openrouter.ai/api/alpha/decisions` | `POST https://ai-gateway.vercel.sh/v4/ai/evaluation-model` |
| Context | 64k (32k state + longest question) | 32k advertised | not published |
| Price | $0.042 / Mtok in, $0 out | same | same |
| Get one | [console.typesafe.ai/settings/keys](https://console.typesafe.ai/settings/keys), behind a waitlist | [openrouter.ai/settings/credits](https://openrouter.ai/settings/credits) | Vercel dashboard → AI Gateway → API keys |
| Works with this repo | yes | yes | **no** — see below |

**TypeSafe direct** is the primary service: double the context, and the only
route TypeSafe itself documents. [typesafe.ai](https://typesafe.ai) currently
fronts a waitlist, so access is not instant.

**OpenRouter** is the fast path if you already have credits. The catch is
context — halved — and that Jev is absent from OpenRouter's public
`/api/v1/models` catalog, so you can call it by exact ID but cannot discover it.
Full detail in [`openrouter-decisions-api.md`](openrouter-decisions-api.md).

### The Vercel AI Gateway route, precisely

Confirmed from the live catalog (`GET https://ai-gateway.vercel.sh/v1/models`):

```json
{"id":"typesafe-ai/jev","owned_by":"typesafe-ai","name":"Jev","type":"evaluation",
 "context_window":0,"max_tokens":0,
 "pricing":{"input":"0.000000042","output":"0"}}
```

Confirmed:

- Model id is **`typesafe-ai/jev`** — a third spelling, distinct from both
  `jev-latest` and `typesafe/jev-1.13`.
- Env var is **`AI_GATEWAY_API_KEY`**, named by the gateway's own 401 body.
- Pricing matches everywhere else: `$0.042` / Mtok in, output free.
- Vercel's [Evaluation docs](https://vercel.com/docs/ai-gateway/modalities/evaluation)
  say it is *"not supported through the OpenAI-compatible, Anthropic-compatible,
  or Cohere-compatible endpoints"* and *"requires AI SDK 7 or later"*. That is
  true of the **`/v1`** surface — `/v1/decisions` and `/v1/systemone` 404, and
  `/v1/chat/completions` demands `messages`.
- **But there IS a plain HTTP endpoint**, on the `v4` surface. Read out of
  `@ai-sdk/gateway@4.0.87`, which builds `${baseURL}/evaluation-model` from a
  default baseURL of `https://ai-gateway.vercel.sh/v4/ai`:

  ```
  POST https://ai-gateway.vercel.sh/v4/ai/evaluation-model
  Authorization: Bearer $AI_GATEWAY_API_KEY
  ai-gateway-protocol-version: 0.0.1
  ai-evaluation-model-specification-version: 4
  ai-model-id: typesafe-ai/jev

  {"state": ..., "questions": {...}}      # model id is NOT in the body
  ```

  Verified by probe: without a key it returns `401 Missing Authorization
  header`, and with a bogus bearer `401 Authentication failed ... set
  AI_GATEWAY_API_KEY`. A 401 rather than a 404 is what tells you the route is
  real. "AI SDK only" describes what Vercel documents, not what the protocol
  permits.

```typescript
import { experimental_evaluate as evaluate } from 'ai';

const result = await evaluate({
  model: 'typesafe-ai/jev',           // or gateway.evaluationModel('typesafe-ai/jev')
  state: 'The support agent issued a full refund to the customer.',
  questions: {
    refunded: { type: 'boolean', instructions: 'Was a refund issued?' },
  },
});
// result.answers.refunded -> { type: 'boolean', probability: 0.99 }
```

Not confirmed, and do not assume:

- **Context length.** The catalog reports `context_window: 0` and
  `max_tokens: 0`, i.e. unpublished — not zero. `providers.toml` assumes
  TypeSafe's 64k for this route; treat that as an assumption, not a fact.
- **A live round trip.** Everything above is from the SDK source plus
  unauthenticated probes. Nobody has run this with a real key yet.

Naming differs on this route: Vercel calls a Noul a **`boolean`** and returns
`probability`. `choice` and `score` keep their names.

**This route IS wired up**, as the third entry in `providers.toml`. It is not a
plain proxy like OpenRouter, so `jev_provider.py` translates it behind a
`wire = "vercel"` flag:

| | native (TypeSafe / OpenRouter) | Vercel gateway v4 |
|---|---|---|
| Model id | in the request body | `ai-model-id` header |
| Yes/no question | `type: "noul"` | `type: "boolean"` |
| Yes/no answer | `{"noul": 0.92}` | `{"probability": 0.92}` |
| `confidence` | returned on choice/score | **not returned** — derived locally |
| Score `legend` | returned | **not returned** |
| Usage keys | `input_tokens` | `inputTokens` |

The client normalises all of that back to the native shape, so **no app in this
repo changes**: `JEV_PROVIDER=vercel` and everything keeps working.

One caveat you must respect. Since the gateway omits `confidence`, the client
computes it from the answer's distribution (normalised entropy) and records the
affected question ids in `Answers.confidence_is_derived`. It is our
approximation, not TypeSafe's formula — **confidence thresholds tuned on the
native API will not transfer exactly to this route.**

## Setting the key for local dev

```bash
cp .env.example .env
$EDITOR .env          # fill in ONE key, matching the provider you picked
```

`.env` is gitignored; `.env.example` is not. The loader reads `.env` from the
working directory and from the directory holding `providers.toml`. **Existing
environment variables always win** over the file.

Provider selection resolves in this order — first hit wins:

| Priority | Source | Example |
|---|---|---|
| 1 | `load_provider()` argument | `load_provider("openrouter")` |
| 2 | `JEV_PROVIDER` env var | `export JEV_PROVIDER=openrouter` |
| 3 | `provider = ` in nearest `providers.toml` | `provider = "typesafe"` |
| 4 | built-in default | `typesafe` |

`providers.toml` is looked up by walking **up** from the app folder, so an
app-local copy pins that one app while the repo-root file governs the rest.

Each provider block supplies `base_url`, `model`, `api_key_env`, `signup`, and
`context_tokens`. Read the limit, never hardcode it:

```python
provider = load_provider()
if estimated_tokens > provider.context_tokens:
    ...
```

Switching provider is a config change and never a code change — both providers
speak byte-identical request and response schemas.

```bash
# offline, no key, no cost — always passes before you touch a key
uv run --no-project --with httpx python _shared/jev_provider.py

# live, needs a key, costs a fraction of a cent
uv run --with httpx python scripts/smoke_test.py
```

## Prompt your agent like this

Name the skill explicitly. Every upstream example does, because it works in any
agent.

**Find where Jev belongs before writing anything:**

```text
Using the TypeSafe skill, read docs/APP_SPEC.md and the existing apps, then
propose three apps for this repo that Jev does better than an LLM. For each,
name the primitive (noul/choice/score), what goes in state, and what code owns
instead of the model.
```

**Build one, against this repo's contract:**

```text
Using the TypeSafe skill, build apps/triage/support-router per docs/APP_SPEC.md.
One choice for the destination team including a no-match option, separate nouls
for independent flags, all in ONE request. Thresholds as module constants, not
baked into instructions. Then run the Tier 1 offline checks in AGENTS.md.
```

**Fix questions that are already wrong:**

```text
Using the TypeSafe skill, review the questions in apps/*/app.py against
https://docs.typesafe.ai/concepts/how-to-build-with-system-one and
https://docs.typesafe.ai/model-jaggedness/jev-1.13. Flag every Choice missing a
no-match option, every Score whose levels are bare labels, and anything asking
the model to count or compare dates.
```

Then review what comes back. Agents write weak questions — expect to edit the
instructions and criteria yourself, and keep them in one file so that is cheap.

## What the skill stops your agent doing

Each of these is a real failure the skill's context prevents. The first is fatal;
the rest merely produce a bad app.

| Mistake | Why it breaks |
|---|---|
| Calling `/chat/completions` | Rejected with a 400. Jev is a decisions model — no messages, no roles, no text to parse. Use the decisions endpoint for your provider. |
| Budgeting for output tokens | Output is free. Cost is input-only. A "total tokens" figure overstates every number in this repo. |
| Asking the model to count, sum, or compare dates | Documented jaggedness, not a prompt problem. `scripts/test_apps.py` walks the AST and **fails the build** on "how many", "count the", "sum of", "days between". Move the arithmetic into Python. |
| One request per item | Jev ingests `state` once and answers every question against it in parallel. TypeSafe's own [parallel-questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions.md) measures batching at 12.2× cheaper and 10.0× faster with identical answers. A second request is justified only when an earlier answer decides what to fetch next. |
| A Choice with no no-match option | A Choice *must* return something. Without `none`/`other`/`unclear` it nominates the least-wrong option with entirely plausible confidence. |
| Collapsing independent labels into one Choice | If two labels can both be true they are two Nouls. Merging them destroys the separate thresholds you wanted to tune. |
| Score levels like `["low","medium","high"]` | Levels must describe concrete, self-standing situations. Bare labels fail review here. |
| A Noul used as an intensity dial | `0.5` means yes and no are equally likely, not "medium". If you want degree, use a Score. |
| Thresholds inside instructions | Policy belongs in Python, so tuning re-runs no inference. |

## Next

| You want | Read |
|---|---|
| The contract every app here must satisfy | [`APP_SPEC.md`](APP_SPEC.md) |
| Repo rules for agents — generated files, the two verification tiers, question review | [`AGENTS.md`](../AGENTS.md) |
| The OpenRouter route in full | [`openrouter-decisions-api.md`](openrouter-decisions-api.md) |
