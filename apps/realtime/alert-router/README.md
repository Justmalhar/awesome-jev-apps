# 🚨 alert-router

Page, ticket, or ignore — decided **at alert time**, inside the alerting path,
against a stated latency budget.

```bash
uv run --with httpx python router.py
```

```
routing 12 alerts via typesafe, budget 500 ms/alert
  page   checkout-api     HTTP 5xx rate above threshold        impact 0.94  healed 0.03   146 ms
ticket   checkout-api     HTTP 5xx rate above threshold        impact 0.08  healed 0.21   139 ms
ignore   checkout-api     Pod restarted                        impact 0.05  healed 0.91   142 ms
  page   auth             Login success rate dropped           impact 0.97  healed 0.02   151 ms
ticket   data-warehouse   Disk usage above 85 percent          impact 0.06  healed 0.11   144 ms
ignore   edge-cdn         Origin latency spike                 impact 0.09  healed 0.88   138 ms

12 alerts · 3 questions each · median 143 ms · p95 151 ms · slowest 151 ms
Latency budget added to the alerting path: p95 151 ms against 500 ms allowed — within budget.
paged 4 · ticketed 5 · ignored 3
```

## The problem

Alert routing is written in YAML, and YAML cannot read. `severity: critical` and
`env: prod` route the checkout 5xx alert and the staging load-test that fires the
*identical rule* to the same pager. So on-call gets woken for a pod that restarted
and rejoined the pool, and for a CRM sync that has failed intermittently for
months and self-heals on the next run.

Teams respond by muting things, which is how the one alert that mattered ends up
inside a silence somebody added in March.

The obvious fix — read the alert text and decide — is the thing nobody puts in
the alerting path, because the alerting path is the one code path you cannot make
slow or flaky. An LLM call there adds a second or two of latency and a new
dependency between "production broke" and "somebody found out".

## Why this needs Jev

This app is about the **budget**, not the accuracy. The question is not "can a
model classify alerts" — it obviously can. It is "what delay does putting the
judgment *inside* the alerting path actually add, and is that delay acceptable".

At roughly 150 ms measured, the answer is: less than the jitter already in your
notification pipeline. Slack's own delivery variance is larger. The footer states
the p95 explicitly and **the process exits 1 when it exceeds `--budget-ms`**, so
this can gate CI rather than being a claim in a README.

## How it works

One request per alert, three questions:

```python
"route": choice(
    {"task": "How should this alert be handled right now?",
     "service": "`alert.service`", "environment": "`alert.env`", "alert": "`alert.summary`",
     "note": "Choose 'unclear' when the alert text does not say enough to decide."},
    {"page":   "Someone has to be woken up: it is degrading now and waiting until morning makes it worse.",
     "ticket": "Real work that a human should do, but it can wait for the next working day.",
     "ignore": "Nothing here needs a human at all; recording it is enough.",
     "unclear": "The alert text does not say enough to place it in any of these."}),
"customer_impact": noul(..., true="People using the product are currently failing to do something they came to do",
                             false="Internal tooling, a test environment, or a problem with no user-visible effect yet"),
"self_healing":    noul(..., true="It resolved by itself, retries on a schedule, or a replica absorbed it",
                             false="It is ongoing and will stay broken until somebody acts"),
```

`decide()` then **overrides the Choice** in Python:

```python
if customer_impact >= impact_at and self_healing < healed_at:
    return "page"                       # live impact outranks the model's route
if self_healing >= healed_at and customer_impact < impact_at:
    return "ignore" if route in {"ignore", "unclear"} else "ticket"
if route == "unclear":
    return "ticket"                     # an unreadable alert is a human's problem, not a pager's
return route
```

That ordering is the safety argument. A Choice landing on `ticket` while
customers are locked out is the failure that costs you an incident, so the
customer-impact Noul is given veto power in code — where it is testable offline
and reviewable in a PR, instead of inside a prompt.

**Per-call state is one alert** — service, environment, and a two-sentence
summary. No dashboards, no metric series, no runbook, no incident history.
That is what holds the call at ~150 ms and a few hundred tokens, and it is also
the honest boundary of what this can decide: it routes on what the alert *says*.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx python router.py
uv run --with httpx python router.py --budget-ms 200          # tighter gate
uv run --with httpx python router.py --json | jq '.summary'
```

Exit code is 0 within budget, 1 over it. Wire it into CI to catch the day the
latency you are relying on stops being true.

## Design notes

- **Choice for the route, Nouls for the overrides.** An alert goes exactly one
  way, so routing is a Choice. "Customers affected" and "already recovered" are
  independent facts that can both be true, so they are Nouls with their own
  thresholds.
- **`unclear` is load-bearing.** Half of real alerts are a metric name and a
  number. Without a no-match option the Choice would nominate `page` or `ignore`
  at plausible confidence on text that says nothing — which is the exact failure
  that makes people distrust automated routing forever.
- **`unclear` degrades to a ticket.** Not a page (you have not earned the right to
  wake someone on no information) and not silence (you have not earned the right
  to drop it either).
- **Percentiles are Python.** Nearest-rank, in `percentile()`, tested at the
  boundaries. The model is never asked to compute anything.

## Limits

- It routes on the alert text. A superbly informative summary routes well; a
  summary reading `disk_usage_pct{host="db-3"} > 85` routes to `unclear` and
  becomes a ticket. Improving your alert annotations improves this more than any
  threshold change.
- Single-alert scope means no storm correlation. Forty alerts from one failing
  dependency are forty independent `page` decisions. Deduplication stays in your
  alert manager, upstream of this.
- Adding any network call to the alerting path adds a failure mode. Run this in
  parallel with your existing route, not in series, until you trust it — and keep
  the YAML rule as the fallback when the call errors or times out.
- `jev-1.13` cannot compare dates or do arithmetic, so "has this fired three
  times this week" and "is this within the maintenance window" are Python
  questions answered from your own data before the call.
- The measured latency is the client's, including the network path from wherever
  you ran it. Measure from the host that will actually run it in production;
  that number is the one the budget gate should use.
