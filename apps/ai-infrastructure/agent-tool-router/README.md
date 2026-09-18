# 🧰 agent-tool-router

Pick the right tool from a large MCP catalog without stuffing every schema into
the model's context on every turn.

```bash
uv run --with httpx python router.py "what's broken in production right now?"
```

```
turn    : "what's broken in production right now?"
catalog : 42 tools via typesafe

── stage 1: ranked 42 summaries in 1 request ──
  needs a tool at all?  P = 0.964
    pagerduty_list_incidents      0.412  ████████████████
    sentry_list_issues            0.268  ██████████
    datadog_list_monitors         0.201  ████████
    kubernetes_get_pods           0.061  ██

── stage 2: full schemas for 3 candidates ──
    pagerduty_list_incidents      0.611
    sentry_list_issues            0.279
    datadog_list_monitors         0.096

→ call pagerduty_list_incidents()  (confidence 0.71)
```

## The problem

Tool catalogs got big. An agent wired to a handful of MCP servers can easily
carry 200–400 tool definitions, and every one of them is re-sent on **every
turn**, before the model reasons about anything. Worse, selection accuracy
*degrades* as the catalog grows — more near-duplicates to confuse.

The usual escapes both have a catch. Embedding search over tool descriptions is
cheap but misses on intent and negation ("*don't* email them, just draft it").
A smaller router LLM understands intent but adds a second frontier call per
turn, which is the cost you were trying to avoid.

## Two stages, and why the second one is legitimate

**Stage 1 — one request, whole catalog.** Every tool becomes an option in a
single Choice, keyed by name with its one-line summary as the description. 42
tools ranked in one pass. A companion Noul asks whether the turn needs a tool
*at all*, which is the question a Choice structurally cannot answer — it must
nominate something even for "hello".

**Stage 2 — one request, top-k full schemas.** The survivors are re-examined
with their complete parameter schemas, and `__none__` remains available so the
router can reject all of them after seeing the detail.

This repo's general advice is *batch everything into one request*. Stage 2 is
the documented exception: it needs stage 1's answer to decide **what state to
send**. You cannot pack it into the first call, because the first call's job is
to determine what the second call should even look at. That is the bar for a
second round trip — not "it feels like a separate step".

## Why summaries first, schemas second

Full schemas for 42 tools is a few thousand tokens. Summaries are a few hundred.
Stage 1 therefore reads cheaply and broadly; stage 2 reads expensively and
narrowly. You pay for detail only on the three candidates that survived, which
is the same reason a database uses an index before a full scan.

The footer prints what stuffing every schema into a frontier model's context
would have cost on that single turn — the number this app exists to avoid.

## Run it

```bash
cp ../../.env.example .env
uv run --with httpx python router.py "remind me what we agreed in the kickoff"
uv run --with httpx python router.py "thanks, that's all for now"     # no tool needed
uv run --with httpx python router.py "refund the last charge for that customer"
uv run --with httpx python router.py --top 5 "why is the staging deploy failing"
```

Try the second one. `needs_tool` should collapse and the router should answer
directly rather than inventing a reason to call something — the failure mode
that makes tool-happy agents exhausting to use.

## Wiring it into a real agent

Swap `tool_catalog.json` for your live MCP catalog — `{name, summary,
parameters}` per tool is all it needs. Then put only the winner's full schema
into your agent's system prompt for that turn.

The router picks the tool; **your agent still fills the arguments and makes the
call.** Jev selects, it does not execute, and it does not generate JSON. For
argument filling with closed-set values, see TypeSafe's
[function calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling).

## Tuning

`--needs-tool-threshold` defaults to 0.5. Raise it if your agent is trigger-happy,
lower it if it is answering from memory when it should be checking. `--top`
trades stage-2 cost against the chance the right tool was ranked 4th — measure
on your own catalog and turns rather than trusting these defaults.
