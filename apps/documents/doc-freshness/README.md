# 📉 Doc Freshness

Which statements in your documentation the current code no longer backs up —
claim by claim, against evidence you supply.

> *"`DELETE /v1/jobs/{id}` cancels a running job."* → documents something gone,
> P = 0.93, change: `renamed`

## The problem

Documentation does not rot loudly. Nobody opens a PR titled *"docs now wrong"*.
A route gets renamed in release 3.0, a default drops from 30000 ms to 10000 ms,
an optional config field quietly becomes required — and the guide keeps saying
what it said in v2.1, confidently, for another eighteen months, until a customer
follows it and their integration fails at startup.

The usual answers do not work. Link checkers only see 404s. A docs audit means a
human holding two documents in their head and asking, for each of twenty-three
claims, *"is this still true?"* — which is tedious in a way that guarantees it
is done once and then never again. And the failure that matters most is the
quietest one: the claim that is still perfectly readable and no longer correct.

## Why this needs Jev

This is a **two-state comparison** where the interesting answer is not a
summary. Hand a chat model both documents and it produces prose: *"several
endpoints appear to have changed."* You cannot diff that, gate CI on it, or sort
it. And a chat model asked to list wrong claims will happily list a claim the
evidence never mentioned, because it knows how APIs usually work.

Here, each claim gets a **probability vector**, and the three failure modes are
independent properties rather than levels on one scale:

| Noul | Fires when |
|---|---|
| `contradicted` | The evidence asserts something incompatible with the claim as written |
| `unsupported` | The evidence is silent — neither confirms nor refutes |
| `describes_removed` | The thing documented is shown to be gone |

They must be separate because they demand different actions. *Wrong* is a docs
bug you fix today. *Unverifiable* usually means **the evidence you pasted was
too narrow** — a gap in your input, not in your docs. Collapsing them into one
"staleness score" would merge a defect with a measurement failure, and you would
never see which you had. Separate Nouls also mean separate sliders: this app
ships with `unsupported` defaulted to **0.80**, much stricter than the other
two, precisely because it is the weakest signal.

The other structural property: 23 claims × 4 questions = **92 judgments in one
request**, all against the evidence ingested once. Every claim is judged with
the entire route table, config schema and changelog in view — which is what lets
`describes_removed` mean *"absent from the set the evidence enumerates"* rather
than *"not mentioned in this one line"*.

## How it works

Python splits the doc. Markdown is one claim per bullet, under the heading that
gives it context:

```python
def split_claims(doc: str) -> list[dict[str, str]]:
    claims, section = [], ""
    for raw in doc.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            section = line.lstrip("#").strip()
        elif line.startswith(("- ", "* ", "+ ")):
            claims.append({"section": section, "text": line[2:].strip()})
        ...
```

Then three Nouls and one Choice per claim, batched into a single `ask()`:

```python
for key, spec in HAZARDS.items():
    questions[f"{key}_{index}"] = noul(
        {"task": spec["task"],
         "documented_claim": {"section": f"`claims[{index}].section`",
                              "claim":   f"`claims[{index}].text`"},
         "evidence": "`reality`"},
        true=spec["true"], false=spec["false"],
    )
questions[f"change_{index}"] = choice({...}, CHANGE_KINDS)  # includes still_accurate, not_determinable
```

Routing is a pure function. The order is the design:

```python
def classify(probabilities, change, contradicted_at, unsupported_at, removed_at) -> str:
    if probabilities["describes_removed"] >= removed_at:  return "stale"
    if probabilities["contradicted"]      >= contradicted_at: return "wrong"
    if probabilities["unsupported"]       >= unsupported_at:  return "unverifiable"
    if change in ("still_accurate", "not_determinable"):      return "current"
    return "check"
```

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

Both panes are prefilled with a bundled sample: a v2.1 developer guide for a
synthetic "Orbit API", and the reality of its v3.0 source tree — route table,
auth middleware, config schema, package metadata, changelog.

### What the sample is designed to catch

| Claim | Expected |
|---|---|
| `DELETE /v1/jobs/{id}` cancels a job | `stale` — replaced by `POST …/cancel` |
| create returns the job id in the `id` field | `wrong` — renamed to `job_id` in 2.7 |
| `ORBIT_API_URL` sets the base URL | `stale` — renamed to `ORBIT_BASE_URL` |
| `ORBIT_TIMEOUT_MS` defaults to 30000 | `wrong` — `default_changed`, now 10000 |
| `webhook_secret` is optional | `wrong` — `newly_required`, startup fails without it |
| signed with HMAC-SHA1, `X-Orbit-Signature` | `wrong` — SHA256, `-256` header |
| the client exposes a synchronous API only | `wrong` — `AsyncClient` since 2.8 |
| `POST /v1/batch` remains supported | `stale` — removed in 3.0 |
| `pip install orbit-client` | `unverifiable` — the evidence names the package but not the installer |
| `GET /v1/jobs` lists jobs for the workspace | `current` |
| retries defaults to 3 | `current` |

Two rows are the honest ones. `pip install` lands in **unverifiable**, not
wrong — the evidence says the package is on PyPI and nothing about `pip`, and
that distinction is the whole reason `unsupported` is its own Noul. And *"a
token scoped to `jobs:read` may call any GET endpoint under `/v1/jobs`"* is
**correct as written** even though a neighbouring claim about `/v1/usage` is
not; the model has to hold the scope boundary to get that right, and it is the
row most worth watching when you tune.

## Design notes

**Why three Nouls and not a staleness Score.** A Score asserts that its levels
are ordered. "Contradicted" and "not verifiable from what you gave me" are not
two points on one axis — one is about the documentation, the other is about the
evidence. Thresholding them separately is the feature.

**Why the Choice cannot condemn a claim.** `classify()` never returns a failure
verdict on the strength of the Choice alone. A Choice *must* return something,
so without `still_accurate` and `not_determinable` it would nominate the
least-wrong change type for a perfectly current claim at entirely plausible
confidence. When the Choice names a change the Nouls did not corroborate, the
claim goes to `check` — a human, not a verdict.

**Why the evidence is free text, not structured.** Whatever you can `cat` into
the box is what you have: a router dump, a `--help`, a config schema, a
changelog. Requiring structure would mean building the parser for every stack,
and the comparison does not need it.

**Ordering is done in Python.** `rank()` sorts on the returned floats. The model
is never asked which of two claims is more broken.

## Limits

- **`jev-1.13` cannot compare versions, dates, or numbers.** This is the sharp
  edge of this particular app, so it is worth being blunt: the questions ask
  *"does the evidence contradict this claim as written?"*, never *"is 3.11 newer
  than 3.9?"* The `requires-python >=3.11` row therefore relies on the evidence
  stating the constraint plainly, not on any comparison — and *"requires Python
  3.9 or newer"* against `>=3.11` is a case where the honest answer is genuinely
  arguable. If you need version-range logic, parse the versions in Python and
  compare them there.
- **`default_changed` is detected as a textual conflict, not an arithmetic one.**
  30000 versus 10000 is caught because the strings disagree, not because one is
  larger. That works for defaults and fails for ranges.
- **Only as good as the evidence.** Paste a partial route table and honest
  claims land in `unverifiable`. That bucket is a prompt to widen your input.
- **It reads literally.** A claim that is true in spirit but loosely worded will
  be flagged. Splitting a doc that mixes claims with tutorial narrative produces
  noisy "claims" — check the split before trusting the counts.
- **It degrades on large, noisy state.** The evidence must fit in
  `provider.context_tokens` (TypeSafe 64k, OpenRouter 32k — read at runtime,
  never hardcoded) and every irrelevant line in it costs accuracy. Trim to the
  route table and changelog rather than pasting the whole repo.
- **No rewrites.** The model cannot generate, so this tells you which sentences
  are wrong and never what they should say.
