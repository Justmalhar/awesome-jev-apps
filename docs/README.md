# docs/

Reference material that isn't specific to one app.

## [`openrouter-decisions-api.md`](openrouter-decisions-api.md)

How to call Jev through OpenRouter, which matters because it lets you run this
entire repo on credits you probably already have.

Three things are non-obvious and undocumented upstream:

1. Jev is **absent from OpenRouter's public model catalog** — 446 models listed,
   zero hits for `jev` or `typesafe`. You can call it by exact ID; you cannot
   discover it programmatically.
2. `/chat/completions` **rejects it on purpose**, with an error naming the right
   endpoint. Correct behaviour: Jev has no chat interface to shim.
3. `/api/alpha/decisions` is a **passthrough**, accepting TypeSafe's native
   schema verbatim. Verified by reading the endpoint's own validation errors.

That third point is why `providers.toml` is pure configuration and no app in
this repo contains a provider branch.

It also documents the one difference that bites: **OpenRouter serves 32k context
against TypeSafe's 64k.**

## Where the rest of the documentation lives

Most of what you need is closer to the code, on purpose:

| Question | File |
|---|---|
| How do I run an app? | [`apps/README.md`](../apps/README.md) |
| What are the three primitives? | [`00-primitives/README.md`](../00-primitives/README.md) |
| How does provider selection work? | [`_shared/README.md`](../_shared/README.md) |
| What do the scripts do? | [`scripts/README.md`](../scripts/README.md) |
| What makes a good app here? | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| I'm an AI agent working in this repo | [`AGENTS.md`](../AGENTS.md) |
| Why does this app exist at all? | each app's own `README.md` |

## Upstream documentation

TypeSafe's docs are the source of truth for the model and API. Start at the
[index](https://docs.typesafe.ai/llms.txt) — Mintlify serves Markdown by
appending `.md` to any page path, which is convenient for agents.

Worth reading before writing questions of your own:

- [System One](https://docs.typesafe.ai/concepts/system-one) — the programming model
- [How to build with System One](https://docs.typesafe.ai/concepts/how-to-build-with-system-one) — question design
- [State](https://docs.typesafe.ai/concepts/state) — what to send and how to shape it
- [Confidence](https://docs.typesafe.ai/confidence) — and what it is *not*
- [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) — the documented failure modes. **Read this before debugging a bad answer**; the odds are good that it is already listed.
- [Cookbooks](https://docs.typesafe.ai/llms.txt) — worked examples with real numbers

## A note on numbers in this repo

Figures quoted in these docs and READMEs come from one of three places, and each
is labelled:

- **Measured here** — printed by the app's own cost footer at runtime.
- **From TypeSafe's cookbooks** — e.g. rerank top-1 5%→18%, batching 12.2×
  cheaper. Attributed where cited.
- **Illustrative** — example output showing the shape of a result.

Do not treat illustrative figures as benchmarks. Run the thing and read the
footer.

## Set up your coding agent

[`AGENT_SETUP.md`](AGENT_SETUP.md) wires Jev into Claude Code, Codex, Cursor, or
any other agent, so it stops guessing at the API and inventing a chat call.

It covers the TypeSafe skill install for each agent, all three provider routes
and their trade-offs — including what is and isn't confirmed about the Vercel AI
Gateway path — and the question-design mistakes the skill prevents.
