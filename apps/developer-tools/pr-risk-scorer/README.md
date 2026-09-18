# 🎯 pr-risk-scorer

Score a diff for blast radius, then route the review — and reweight the policy
afterwards for free.

```bash
uv run --with httpx python pr_risk.py --diff sample_diff.patch
```

```
  4.85  shipyard/migrations/0007_drop_legacy_jobs.py  depth=2.71/3  irreversible
  4.12  shipyard/auth.py                              depth=2.30/3  security
  2.30  shipyard/worker.py                            depth=1.80/3  concurrency
  0.21  shipyard/format.py                            depth=0.20/3
  0.05  README.md                                     depth=0.02/3

verdict: careful review by a maintainer

20 question(s) over 5 file(s) in 1 request(s) · 3,904 tokens · $0.000164 · 1.8s
```

*(Numbers above are illustrative — the app prints its own measured footer.)*

## The problem

Review effort is allocated by diff size, which is the one signal that reliably
lies. A 400-line lockfile bump needs a glance; a nine-line change to
`verify_token` that swaps `hmac.compare_digest` for `!=` and quietly drops the
expiry check needs the security-minded reviewer, today, before it merges.

Teams know this. The reason they don't have a risk router is that every
existing one is either a regex on file paths — brittle, gamed within a week —
or a frontier-model call per PR, which nobody wants in the merge path on cost
and latency.

## Why this needs Jev

**Score once, reweight free.** The four judgments are stored as floats. The
policy that turns them into a routing decision is ordinary Python arithmetic,
so every "what if we weighted security higher?" costs nothing:

```bash
uv run --with httpx python pr_risk.py --diff sample_diff.patch --save scores.json
uv run --no-project python pr_risk.py --from-scores scores.json --w-security 3.0   # no inference
```

A prompt-and-parse pipeline that returns `"risk": "high"` cannot do this. The
weighting was baked into the generated string, so changing it means re-running
the model over every PR you want to compare against. Structured outputs is the
structural difference, not the price.

The price is what makes it viable in CI at all: at $0.042/Mtok a 40-file PR is
a fraction of a cent, so this runs on every push rather than on demand.

## How it works

Python splits the unified diff per file — it knows exactly where `diff --git`
starts, and asking a model to find file boundaries would be slower and worse.
Then four judgments per file, batched into **one** request:

```python
questions[f"depth_{i}"] = score(
    {"task": "How deep a review does this file's change deserve?", "file": ref},
    DEPTH_LEVELS,
)
questions[f"irreversible_{i}"] = noul(
    {"task": "If this change is wrong, would reverting the code be insufficient to undo the damage?",
     "file": ref},
    true="It writes, drops, or rewrites persisted data, sends external side effects, or migrates a schema in place",
    false="A revert restores the previous behaviour completely",
)
```

## Run it

```bash
cp ../../../.env.example .env      # add your key
uv run --with httpx python pr_risk.py --diff sample_diff.patch
uv run --with httpx python pr_risk.py --diff sample_diff.patch --json
```

The bundled `sample_diff.patch` holds five files chosen to separate: a prose
typo, an HMAC comparison downgraded to `!=` with the expiry check deleted, a
destructive migration with a `NotImplementedError` downgrade, a worker moved
onto a thread pool while still writing into a shared dict, and a whitespace
change to a duration formatter.

Exits `1` when the highest-risk file reaches `--gate` (default 5.0), so it
works as a required check.

## Design notes

**Why one Score and three Nouls, not one rubric.** Review depth is a genuine
ordinal: each level subsumes the one below. Security, irreversibility, and
concurrency are not ordered against each other and can all be true at once. A
migration can be trivial to read and still unrecoverable; a race condition can
hide inside a diff that looks cosmetic. Folding them into levels on one scale
would force the model to pick which property "wins" and would destroy your
ability to threshold them separately — which is exactly what routing to a
security reviewer requires.

**Why the thresholds are in `ROUTES` and not in the instructions.** Anything
baked into a question can only be changed by re-running inference. `route()` is
five lines of Python with no model in it; tune it against your own merge
history.

## Limits

- **Per file, not per PR.** A change that is dangerous only because of how two
  files interact scores low on both. Cross-file reasoning would need the whole
  diff as one state, which stops fitting fast.
- **Diff-only context.** The model sees the hunks, not the surrounding
  function or the call sites. A deleted line that was load-bearing three
  modules away reads as innocuous.
- **Hunks are truncated** at 6,000 characters. Generated files and lockfiles
  are scored on their first page, which is usually enough to recognise them and
  never enough to review them.
- **`jev-1.13` cannot count**, so nothing here depends on lines changed, files
  touched, or age of the branch. Python computes all of that if you want it in
  the weighting.
- **Weights are unvalidated defaults.** `security=1.5, irreversible=2.0` are a
  starting point from which to measure, not a calibrated policy.
