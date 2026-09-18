# 🗒️ Standup Synth

Your actual week, assembled from commits, PRs, and tickets — **Jev selects,
Python writes**.

## The problem

Monday's standup asks what you did last week and you cannot remember Tuesday.
The evidence exists — 22 commits, 6 PRs, 5 tickets — but it is the wrong shape:
it is a log, and a log is not an update. Nineteen of those items are "fix typo"
and "bump lint config", and the three that matter are buried in the middle.

So people either read their git log aloud (unlistenable) or write from memory
(inaccurate, and the blocker they meant to raise is the thing they forget).

The tempting fix is "summarise my week with an LLM", and the output is always
the same: confident prose that says you "continued work on improvements to the
payment flow", which is technically true, contains nothing, and occasionally
claims you shipped something that is still in review.

## Why this needs Jev

The division of labour is the whole design:

- **Jev selects.** For each item: would a teammate who was away want to hear
  about this? Is it user-visible? Is the author waiting on someone? Does it
  touch something expensive to get wrong? 22 items × 5 judgments = 110 questions
  in one request.
- **Python assembles.** Sections, ordering, flags, counts and markdown are
  string operations over your own commit messages. Jev *cannot* generate, so no
  sentence in the output was invented — which means it cannot claim you shipped
  something you did not.
- **Whether something is finished is a fact, not a judgment.** It comes from the
  tracker's `state` field, in Python:

```python
DONE_STATES = {"merged", "closed", "done", "released", "committed"}
def is_done(item): return str(item.get("state", "")).lower() in DONE_STATES
```

Asking a model "is this merged?" when the field says `merged` is how you get a
standup that is wrong about the one thing you had ground truth for.

## How it works

```python
notable = noul("Would a teammate who was away this week want to be told about this item?")
visible = noul("Does this change something a user or another team can observe?")
blocked = noul("Does this item say the author is waiting on somebody or something else?")
risky   = noul("Does this item touch an area where a mistake is expensive?")
theme   = choice({feature / fix / refactor / infra / docs / review / other})
```

Four Nouls, because a single item is routinely several of these at once — the
idempotency-key PR is notable *and* user-visible *and* risky, and each of those
drives a different piece of the output. The routing:

```python
def section(item, j, *, notable_gate, noul_gate):
    if j["blocked"] >= noul_gate and not is_done(item):  return "blocked"
    if j["notable"] <  notable_gate:                     return "dropped"
    return "shipped" if is_done(item) else "in_progress"
```

**A blocker bypasses the notability gate.** "Waiting on credentials only the
payments admin can rotate" might read as unremarkable, and it is the single most
useful sentence you can say in a standup. It is also why `blocked` is checked
against `is_done()` — a ticket whose body mentions an old blocker but which the
tracker says is closed is *shipped*, not blocked.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_activity.json` — a realistic week: a duplicate-charge
fix that shipped, a latency improvement with numbers in it, a blocked 3DS ticket,
a production backfill script that touches the ledger, a review that unblocked a
colleague, and seven commits ("fix typo", "reformat the changelog") that should
never reach an update.

Real data: `gh pr list --author @me --json`, `git log --author`, and your
tracker's API, reshaped to `kind, id, repo, state, when, title, body`.

## Privacy

Commit messages, PR and ticket titles and bodies, and repo names are sent to
whichever provider `providers.toml` selects — internal repository content, so
check the provider's data policy against your employer's rules first.

## Design notes

- **Why `notable` and `visible` are separate Nouls.** A dependency pin is
  notable to your team and invisible to users. An error-message format change is
  invisible to your team and breaks the mobile client's parser. Collapsing them
  loses the second case, which is the one that causes an incident.
- **Why `theme` has `other`.** A Choice must return something, and a one-off
  production backfill script is genuinely none of the six. `theme` drives the
  breakdown panel only, never the update itself.
- **Why the gate is a slider.** A daily standup wants a high bar; a weekly
  written update to a manager wants a lower one. Same evidence, $0.00 to switch.

## Limits

- **No time reasoning.** `jev-1.13` cannot compare dates, so `when` is displayed
  and never judged. Grouping by day, or restricting to the last five working
  days, is Python's job and is left to your exporter's query.
- **No item counting asked of the model.** Every number on screen came from
  `len()`. If you are tempted to ask "how many PRs did I merge", do not.
- **Items are judged alone.** It cannot see that six commits and one PR are the
  same piece of work, so a busy branch can crowd the update. Grouping commits
  under their PR before sending is the obvious improvement.
- **Garbage messages in, garbage update out.** "Fix stuff" carries no signal, and
  the app will correctly decide a teammate does not need to hear about it — which
  may be wrong if "fix stuff" was the migration.
- **Nothing is written for you.** If your commit messages are not sentences you
  would say out loud, the output will not be either. That is a deliberate
  trade: no generation means no fabrication.
