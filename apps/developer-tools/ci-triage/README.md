# 🧪 CI Triage

Real bug, flake, infra, dependency, or config — across your whole failure
history, not just today's run.

## Why nobody does this already

Every team with a flaky suite develops the same reflex: **re-run it and see.**
That reflex is expensive, and it hides genuine regressions inside noise nobody
reads any more. The fix everyone wants is a classifier over failure history,
and the reason it doesn't exist is arithmetic: running a frontier model over
20,000 historical failures is a budget conversation with your manager.

At **$0.042 per million input tokens** it is a rounding error. That is the whole
unlock. This app is not clever — it is just newly affordable.

## Three judgments per failure

```python
class      = choice("What caused this failure?", {real_bug, flaky_test,
                     infrastructure, dependency, config, unclear})
retry_safe = noul("Would re-running this plausibly pass, with no code change?")
blast      = score("If this is a real defect, how much would it affect users?")
```

`retry_safe` is deliberately **not** folded into `class`. Both a flaky test and
a dead runner are retry-safe, while a real bug never is — so retry-safety cuts
across the taxonomy rather than sitting inside it. Collapsing them would destroy
your ability to auto-retry one dimension while alerting on the other.

`unclear` exists because a Choice must return something. Without that option,
a truncated log with no useful content gets confidently filed as `flaky_test`,
which is precisely the failure you can least afford in a triage tool.

## The model never counts

This is the app's other lesson. `jev-1.13` cannot count and cannot compute rates
— documented failure modes. So it is never asked:

- ❌ "how many times has this test failed?"
- ❌ "what's this test's flake rate?"
- ❌ "are failures trending up this week?"

It is asked one question per failure. Python computes every count, percentage,
and chart from the labels. If you want per-test flake rates, group the output by
`test` and divide in pandas — free, exact, and auditable.

## Run it

```bash
cp ../../.env.example .env
uv run streamlit run app.py
```

15 realistic failures are bundled — a discount bug, a Playwright timeout that
passes on re-run, DNS failure reaching S3, a missing secret on forked PRs, an
unpinnable dependency version, a float-arithmetic VAT bug, and a test that only
fails under parallel execution.

Input format is a JSON list of `{test, message, log}`. Logs are truncated to
1,500 characters, which is enough for the stack trace and the error that
matters — sending the whole log is worse, since large state full of irrelevant
detail measurably degrades accuracy.

## Wiring it to real CI

Feed it from JUnit XML, the GitHub Actions API, or your test reporter's JSON.
Useful automations once labels exist:

- auto-retry only when `retry_safe > 0.7`, instead of blanket retries
- open an issue when `class == "real_bug"` and `blast > 2.0`
- alert the platform team, not the author, when `class == "infrastructure"`
- weekly report of tests most often labelled `flaky_test` — the quarantine list

## Tuning

The confidence gate defaults to 0.55 and demotes anything below it to a human
pile. Run the tool over a few hundred failures you have already triaged by hand,
compare, and move the gate. Treat the defaults here as a starting point to
measure against — not as validated thresholds for your codebase.
