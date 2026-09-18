# 🚨 incident-tagger

Tag postmortems by cause class **and** by every contributing factor that
applies — because incidents are never caused by one thing.

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

## The problem

Every incident tool ends with a single "root cause" dropdown, and every
engineer who has written a postmortem knows it is a fiction. Take INC-2041 from
the bundled sample:

- a deploy behaved differently against 60M rows than against a 12k fixture, **and**
- the latency alert had a 15-minute window, so a customer found it first, **and**
- the on-call could not roll back without a second approver who was asleep, **and**
- staging parity had been raised in a previous incident review and left in the backlog.

Pick one. Whichever you pick, the quarterly review loses the other three — and
the other three are the ones that show up again in INC-2046, INC-2060, and
INC-2071. The dropdown destroys exactly the signal the process exists to find.

## Why this needs Jev

**Independent properties are independent Nouls.** Eight of them, each with its
own threshold, each separately countable across the corpus:

```python
FACTORS = {
    "detection_gap": ("Did this system fail to tell anyone it was broken, so that people found out some other way?", ...),
    "no_safe_rollback": ("Was undoing the change harder or slower than making it?", ...),
    "environment_mismatch": ("Did this behave differently in production than it did wherever it was tested?", ...),
    "known_risk_accepted": ("Had this risk been identified before the incident and left unaddressed?", ...),
    ...
}
```

This is the structural property the whole app rests on. A taxonomy — one
Choice, or a severity rubric with "monitoring" somewhere on it — would force
the model to decide which factor *wins*, and no threshold you set afterwards
could recover the ones it discarded. Eight Nouls return eight probabilities, so
you can ask "how many of our last forty incidents had a detection gap at
P > 0.6?" and change the 0.6 without re-running anything.

The cause class stays a **Choice**, because an incident does have one trigger,
and `unclear` is there because INC-2068 genuinely never found one.

Ten questions × forty postmortems is a few requests and well under a cent,
which is what makes tagging your whole incident history a Tuesday afternoon
rather than a project.

## How it works

Per incident, batched into one request:

| primitive | question | why |
|---|---|---|
| `choice` | What set this incident off? | One trigger, nine options, `unclear` included |
| `score` | How likely is this to happen again as things stand? | Ordinal, and it drives whether an action item is required |
| `noul` × 8 | One per contributing factor | Independent, co-occurring, separately thresholded |

Then Python does the part `jev-1.13` cannot:

```python
hits = [item for item in tagged if item["factors"][name] >= gate]
```

The top table — *what keeps coming back* — is a count, and counts come from
Python. The model is never shown the corpus or asked to compare incidents.

## Design notes

**Why recurrence is a Score and not a Noul.** "Could this happen again?" is
almost always yes, which makes a Noul useless. The levels describe situations
instead of likelihood words:

```python
"The exact conditions are gone and could not assemble again in this system",
"It could happen again only if somebody repeated an unusual action deliberately",
"The same trigger is likely to recur on an ordinary day, and the same outcome would follow",
"It is recurring already, or nothing at all has changed since it happened",
```

**Why every factor Noul states both sides.** `jev-1.13` reads literally. Left
at "was there a detection gap?", the boundary cases — alerting fired but into
an unstaffed channel (INC-2071), alerting fired correctly and there was simply
nothing to do (INC-2052) — resolve however the wording happens to lean. The
`true`/`false` criteria are where those cases get decided, in writing, once.

**Why the cause class is demoted to `unclear` below a confidence gate.** A
Choice always returns something. INC-2068's write-up establishes nothing, and a
confidently-labelled `latent_defect` in your incident database is worse than an
honest gap.

## Limits

- **It reads the write-up, not the incident.** A postmortem that omits the
  rollback pain produces a low `no_safe_rollback` — the tool measures your
  documentation as much as your systems. That is a real bias and it skews
  towards teams that write thoroughly.
- **Long postmortems are truncated** at 2,500 characters, which usually keeps
  summary, detection, and response and can lose a contributing-factors section
  at the end. Reorder or pre-summarise if yours are long.
- **No timeline reasoning.** Time to detect, time to mitigate, and "has this
  got worse this quarter" all need date arithmetic, which `jev-1.13` cannot do.
  Parse the timestamps in Python and compute them there.
- **Factors are judged per incident, never across incidents.** "This is the
  third time this quarter" is Python's job, in `tally()`.
- **Eight factors is an opinion.** They are the ones that recur in the sample
  corpus; yours will differ. Editing `FACTORS` is the intended way to adapt
  this, and each entry you add costs one question per incident.
- **Thresholds are unvalidated.** 0.6 for a factor was chosen to separate the
  sample. Tag thirty incidents you have already reviewed by hand and move it.
