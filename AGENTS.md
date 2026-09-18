# AGENTS.md

Instructions for AI agents working in this repo. Things that are not obvious
from the code and do not belong in a human-facing README.

## The one rule that breaks builds

**Never edit `apps/*/jev_provider.py` or `apps/*/providers.toml`.** They are
generated copies. Your edit will be silently overwritten and CI will fail.

```bash
# edit the source, then regenerate:
vim _shared/jev_provider.py
python scripts/sync_provider.py
```

Same for `apps/*/providers.toml` — change the root `providers.toml` and re-sync.

## Before you claim anything works

There are two tiers of verification here, and conflating them produces false
claims:

```bash
# Tier 1 — offline. No key, no network, no cost. Always run these.
uv run --no-project python scripts/sync_provider.py --check
uv run --no-project --with httpx python _shared/jev_provider.py
uv run --no-project --with httpx --with streamlit --with pandas --with scikit-learn python scripts/test_apps.py

# Tier 2 — live. Needs a key, costs a fraction of a cent.
uv run --with httpx python scripts/smoke_test.py
```

Tier 1 passing means **the logic is right**. It does not mean the app works —
it never touches the API. Say "offline checks pass" and not "verified" unless
Tier 2 also ran.

## Repo conventions

- **`uv`, never `pip`.** Each app has its own `pyproject.toml`; there is no
  repo-wide virtualenv. Use `--no-project` for repo-root scripts.
- **Apps are standalone.** No imports from the repo root, ever. A person must be
  able to copy one folder out and have it work.
- **One file per app** unless there is a real reason not to.
- **Sample data is committed**, so every app runs before the user supplies
  anything.
- **Cost footers print measured values**, never estimates. If you did not run it,
  label the number illustrative.

## Designing questions — the mistakes that get caught in review

Read [TypeSafe's building guide](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
first. Then, in rough order of how often these appear:

**Every Choice needs a no-match option.** A Choice *must* return something. Without
`none`/`other`/`unclear` it nominates the least-wrong option with entirely
plausible confidence. `scripts/test_apps.py` asserts this for several apps; add
the assertion when you add an app.

**Independent properties are separate Nouls.** If two labels can be true at once,
they are two Nouls, not one Choice and not levels on a rubric. Collapsing them
destroys separate thresholding, which is usually the thing the user wants to tune.

**Score levels describe situations, not labels.** `["low","medium","high"]` fails
review. Each level must stand alone and describe something concrete.

**A Noul is not an intensity dial.** `0.5` means yes and no are equally likely.
If you want degree, use a Score.

**Policy belongs in Python.** Thresholds, weights, and routing go in code so
tuning re-runs no inference. Never bake a threshold into an instruction.

**Batch by default.** Independent questions over the same state go in ONE
request. A second request is warranted *only* when an earlier answer is needed
to fetch evidence or decide what state to send next —
`apps/agent-tool-router/router.py` is the one legitimate case in this repo.

## Never ask the model to do these

`jev-1.13` cannot count, cannot do arithmetic, and cannot compare dates. These
are documented failure modes, not prompt problems.

`scripts/test_apps.py` walks the AST of every `noul()`, `choice()` and `score()`
call and **fails the build** on phrases like "how many", "count the", "sum of",
"days between". If you trip it, the fix is to move the computation into Python —
not to reword the question past the check.

Also: it reads literally (state the exact condition; put boundary cases in
`criteria`), it degrades on indirection (point at the relevant field), and it
degrades on large state full of irrelevant detail (filter first).

## Verified facts, so you don't re-derive them

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`, body
  `{model, state, questions}` → `{model, answers, usage}`.
- OpenRouter: `POST https://openrouter.ai/api/alpha/decisions`, same schema.
  Jev is **not** in OpenRouter's public `/api/v1/models` catalog and
  `/chat/completions` rejects it. See [`docs/openrouter-decisions-api.md`](docs/openrouter-decisions-api.md).
- Context: TypeSafe **64k**, OpenRouter **32k**. Read `provider.context_tokens`;
  never hardcode.
- Pricing: `$0.042` per million input tokens, output free. Cost is input-only.
- Noul answers carry **no** `confidence` field. `Answers.confidence()` raises on
  one, deliberately.

## Git

- Personal identity on this repo: `Malhar Ujawane <malharujawane@gmail.com>`.
  Set per-repo, not globally.
- Branches: `cc-feature/*`, `cc-fix/*`, `cc-ui/*`, `cc-docs/*`.
- One app per PR, with the real cost footer visible in the description.

## Current state

All eight apps are built and pass offline checks. **No app has been verified
against a live API response** — parsing is written against TypeSafe's published
contract. If you obtain a key, run `smoke_test.py` first and fix whatever
reality disagrees with before touching anything else.

`evals/` is intentionally empty; see [`evals/README.md`](evals/README.md) for
the bar.
