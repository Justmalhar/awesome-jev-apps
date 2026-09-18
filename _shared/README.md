# _shared/

The **source of truth** for `jev_provider.py`. Every app carries a copy; this is
the original.

```
_shared/jev_provider.py  ──[ scripts/sync_provider.py ]──▶  apps/*/jev_provider.py
```

Edit this file. Never edit a copy — it will be overwritten, and CI fails the
build when copies drift.

```bash
python scripts/sync_provider.py          # regenerate all copies
python scripts/sync_provider.py --check  # what CI runs
```

## Why duplicate instead of import?

Because the alternative costs adoption. People fork **one folder** out of a repo
like this — they do not adopt a framework. A shared package at the repo root
would mean any copied app arrives broken, with an import error pointing at a
directory the person doesn't have.

So the client is duplicated **on purpose**, and generated rather than
hand-maintained, which removes the usual reason duplication is bad: the copies
cannot silently diverge.

This is a deliberate trade, not an oversight. If this repo ever grows a real
runtime shared between apps, that is the point to revisit it.

## What's in it

Roughly 300 lines, no dependency beyond `httpx`.

| Piece | Does |
|---|---|
| `load_provider()` | Resolves which provider to use: argument → `JEV_PROVIDER` env → `providers.toml` → default. Walks up from the app folder, so an app-local `providers.toml` beats the repo-root one. |
| `_load_dotenv()` | ~10 lines instead of a `python-dotenv` dependency. Existing env vars always win. |
| `ProviderConfig` | Frozen config. `.api_key` raises a `MissingKeyError` that tells you exactly which variable to set and where to get one. |
| `JevClient.ask()` | One round trip: state + questions → answers. Tracks cumulative cost and token count. |
| `Answers` | Typed accessors: `.noul()`, `.choice()`, `.score()`, `.probabilities()`, `.confidence()`, `.cost_usd`. |
| `noul()` / `choice()` / `score()` | Question builders, thin enough that app code reads like the docs. |

## Deliberate design choices

**Typed accessors raise instead of returning `None`.** `answers.noul("typo")`
raises `KeyError` listing the ids that do exist; `answers.noul("a_choice_id")`
raises `TypeError`. A mistyped question id should fail loudly at the call site,
not silently become a falsy value three lines later.

**`confidence()` raises on a Noul.** Nouls have no confidence field — the
probability *is* the answer. Returning `0.0` would invite treating a
perfectly-decided Noul as uncertain.

**Retries cover 429/529/500/502/503 and network errors**, with exponential
backoff and `Retry-After` honoured. `401` and `402` are *not* retried — they
raise immediately with a message naming the key variable or the top-up URL,
because retrying a billing problem five times helps nobody.

**Cost is input-only.** Output tokens are free on Jev; a "total tokens"
calculation would overstate every figure in the repo.

## Self-check

Runs offline, needs no key or network:

```bash
uv run --with httpx python _shared/jev_provider.py
# jev_provider self-check passed
```

It covers config resolution and precedence, the question builders and their
validation, answer parsing for all three types, the error behaviour above, and
that 1M input tokens costs exactly $0.042.

## Changing the wire format

If TypeSafe changes the API, this is the only file to touch. The apps never
construct a request or parse a response themselves — that is the whole reason
this file exists.
