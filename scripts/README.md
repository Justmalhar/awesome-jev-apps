# scripts/

Three scripts. Two run offline; one costs a fraction of a cent.

## `smoke_test.py` — run this first

One live call. Verifies your key, the endpoint, and all three primitives end to
end, then prints what it actually cost.

```bash
uv run --with httpx python scripts/smoke_test.py
JEV_PROVIDER=openrouter uv run --with httpx python scripts/smoke_test.py
```

```
provider : typesafe
endpoint : https://api.typesafe.ai/v1/systemone
model    : jev-latest
context  : 64,000 tokens

  is_urgent   P(yes) = 0.94
  team        = billing  (confidence 0.81)
  frustration = 1.72 / 2.00

  312 input tokens
  $0.00001310  (180 ms, 3 questions in 1 call)

smoke test passed
```

**If it fails**, the error tells you which of the three things is wrong:

| Message | Fix |
|---|---|
| `needs TYPESAFE_API_KEY, which is not set` | `cp .env.example .env` and fill it in |
| `401 Unauthorized` | key is set but wrong for this provider — check you didn't put a TypeSafe key in `OPENROUTER_API_KEY` |
| `402 Payment Required` | no credits; the message includes the top-up URL |
| `Unknown provider 'x'` | `provider` in `providers.toml` doesn't match a section |

## `sync_provider.py` — regenerate the per-app client copies

Every app carries its own `jev_provider.py` and `providers.toml` so the folder
is independently forkable. This generates them from
[`_shared/jev_provider.py`](../_shared).

```bash
python scripts/sync_provider.py          # write the copies
python scripts/sync_provider.py --check  # fail if any copy is stale (CI)
```

Run it after editing `_shared/jev_provider.py`, after changing the root
`providers.toml`, and after adding a new app folder. `--check` is what stops
someone editing a copy and having their change silently overwritten later.

## `test_apps.py` — offline checks for every app

No API key, no network. Covers the parts that can be wrong without the API ever
being reached.

```bash
uv run --with httpx --with streamlit --with pandas --with scikit-learn python scripts/test_apps.py
```

```
offline app checks:
  semantic-ctrl-f ok
  feed-ranker ok
  paper-screener ok
  repo-grep ok (21 functions, 7 batches)
  statement-categorizer ok
  ci-triage ok
  agent-tool-router ok (42 tools)
  no arithmetic asked of the model ok
```

What it actually asserts, per app: that every item is addressable as a Choice
option, that a no-match escape hatch exists, that Score levels are descriptions
rather than bare labels, that `paper-screener`'s veto rule cannot be outvoted by
strong inclusions, that `repo-grep`'s batching drops no chunks and reports real
line numbers, and that tool names are unique (duplicates would collide as Choice
keys).

The last check is the most useful one in the repo. It walks the AST for every
`noul()`, `choice()` and `score()` call and **fails the build** if any of them
asks the model to count or do arithmetic — documented `jev-1.13` failure modes.
It is scoped to strings that genuinely reach the model, so docstrings and CLI
help text don't trip it.

## What CI runs

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), on every push and PR,
with **no API key** — so it works on forks:

1. `sync_provider.py --check` — copies are current
2. `_shared/jev_provider.py` — client self-check
3. `compileall` — everything parses
4. `test_apps.py` — app logic

`smoke_test.py` is not in CI, because it needs a key and costs money. Run it
locally.
