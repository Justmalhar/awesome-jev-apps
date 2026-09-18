# Calling Jev through OpenRouter's decisions endpoint

*Verified 2026-09-19. Undocumented at time of writing — if OpenRouter publishes
official docs, trust those over this page.*

Jev is available on OpenRouter as `typesafe/jev-1.13`, which matters because it
lets you run everything in this repo on credits you probably already have,
without signing up for a second service.

Three things are non-obvious about it.

## 1. It is not in the public model catalog

```bash
curl -s https://openrouter.ai/api/v1/models | jq '[.data[] | select(.id|test("jev"))] | length'
# 0
```

446 models listed, zero hits. The model page at
<https://openrouter.ai/typesafe/jev-1.13> returns 200 and is fully populated,
but the catalog endpoint does not include it. You can call it by exact ID; you
cannot discover it programmatically.

## 2. `/chat/completions` rejects it on purpose

```bash
curl https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{"model":"typesafe/jev-1.13","messages":[{"role":"user","content":"Is the sky blue?"}]}'
```

```json
{"error":{"message":"typesafe/jev-1.13 is a decisions model and cannot be used with the chat/completions endpoint. Use the /api/alpha/decisions endpoint instead.","code":400}}
```

This is the correct behaviour, not a bug. Jev has no chat interface to shim —
there are no messages, no roles, and no generated text. Anything that tried to
squeeze it into the chat shape would have to invent a response format.

## 3. The decisions endpoint is a passthrough, not a translation layer

`POST https://openrouter.ai/api/alpha/decisions` accepts **TypeSafe's native
schema verbatim**. Sending a deliberately wrong body makes it say so:

```json
{"code":"invalid_union","path":["state"],
 "errors":[[{"expected":"string"}],[{"expected":"record"}],[{"expected":"array"}]]}
{"expected":"record","path":["questions"]}
```

`state` accepts `string | record | array` and `questions` is a record — exactly
TypeSafe's contract. So the only difference between providers is the URL, the
model string, and which key you send:

| | TypeSafe | OpenRouter |
|---|---|---|
| Endpoint | `https://api.typesafe.ai/v1/systemone` | `https://openrouter.ai/api/alpha/decisions` |
| Model | `jev-latest` | `typesafe/jev-1.13` |
| Key | `TYPESAFE_API_KEY` | `OPENROUTER_API_KEY` |
| Context | 64k (32k state + longest question) | **32k advertised** |
| Price | $0.042 / Mtok in, $0 out | same |

That is why `providers.toml` in this repo is pure config and no app contains a
provider branch.

## The one difference that can bite you

**Context length is halved.** TypeSafe advertises 64k; OpenRouter's page says
32k. Apps that pack large state — `semantic-ctrl-f` on a long contract,
`feed-ranker` at 50 stories — can fit on TypeSafe and fail on OpenRouter.

Read the limit instead of assuming it:

```python
provider = load_provider()
if estimated_tokens > provider.context_tokens:
    ...
```

`providers.toml` records `context_tokens` per provider for exactly this reason.

## Errors you will actually hit

| Status | Meaning |
|---|---|
| `400` with "is a decisions model" | You hit `/chat/completions`. Use `/api/alpha/decisions`. |
| `402` | No OpenRouter credits. Top up at <https://openrouter.ai/settings/credits>. |
| `401` | Key is valid-looking but wrong service — check you did not put a TypeSafe key in `OPENROUTER_API_KEY`. |
| `429` / `529` | Rate limited or overloaded. `jev_provider.py` retries these with backoff and honours `Retry-After`. |

## Which should you use?

Use **TypeSafe directly** for anything with large state or production traffic —
double the context and it is the primary service.

Use **OpenRouter** to try this repo without a second signup, or if you are
already routing spend through it and want one bill.
