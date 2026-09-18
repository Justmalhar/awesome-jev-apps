# 💬 Slack Digest

What happened across your channels that actually concerns you — as a **filter**,
not a summary.

## The problem

You were heads-down for two days. There are 400 unread messages across nine
channels. Three of them change what you are doing today, and you cannot find
them without reading all 400, which is why most people just mark-all-read and
hope the important thing gets repeated.

The tool everyone reaches for is a summariser, and a summariser is the wrong
shape for this. A summary of #payments tells you "the team discussed the
checkout incident and the release date moved" — which loses the exact thing you
needed: *who* said the freeze moved, *what time* the new freeze is, and the fact
that Priya is still blocked on a secret rotation that only you can do. Worse, a
summary is a new text you have to trust, produced by a model that will happily
compress "we are considering moving the date" into "the date moved".

## Why this needs Jev

**Jev cannot generate.** That is the entire architectural argument for this app.
The output is a subset of your Slack, verbatim, with the original authors and
timestamps. There is no paraphrase to audit, no hallucinated decision, no
compressed caveat. The model's whole job is answering yes/no questions about
messages it did not write.

Structurally:

- **24 messages × 6 judgments = 144 questions in one request**, answered in
  parallel. A per-message loop is 144 round trips for identical evidence.
- **Filtering policy is code.** Both gates re-filter the entire backlog for
  $0.00 — which matters because "what concerns me" is different on Monday
  morning and during an incident.
- **@mentions are a regex, not a judgment.** Anything a `re.search` can decide
  is decided in Python and used to *override* the filter.

## How it works

```python
concerns  = noul("Does this message concern work the reader says they own?")
open_ask  = noul("Does this message contain a request or question that the replies have not resolved?")
decision  = noul("Does this message state that something has been decided or changed?")
broken    = noul("Does this message report something broken, degraded, or blocked?")
kind      = choice({question / decision / incident / announcement / coordination / social / other})
relevance = score("How closely does this bear on the reader's own responsibilities?", [4 levels])
```

Four Nouls because these co-occur constantly — "the deploy broke checkout and we
have decided to roll back" is a decision *and* a breakage *and* your area. A
single Choice would force one label and you would lose the other two, which are
precisely the ones you would tune separately.

The filter itself:

```python
def surfaces(j, mentioned, *, relevance_gate, noul_gate):
    if mentioned:                        return True          # a regex beat the model
    if j["relevance"] < relevance_gate:  return False
    return (j["concerns"] >= noul_gate or j["broken"] >= noul_gate
            or j["decision"] >= noul_gate)
```

Note what is *not* in there: `open_ask` alone never surfaces a message. An
unanswered question in someone else's area is their problem. It contributes a
reason tag once something else has already earned the message a place.

Every surfaced message shows *why* it survived ("🔥 something is broken",
"📣 you were named"), so the filter can be argued with rather than trusted.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_slack.json` — 24 messages across 8 channels for
someone who owns checkout and payments and is this month's mobile release
manager. It contains the cases worth watching: a freeze-time change buried in
#mobile-release, a teammate blocked on a secret only the reader can rotate, an
outstanding interview scorecard that names them, and a #design-critique channel
that should vanish entirely.

Real data: Slack's export gives you JSON per channel per day; reshape to
`channel, author, time, text, replies`.

## Privacy

Message text, authors, channel names and thread replies are sent to whichever
provider `providers.toml` selects — this is your colleagues' writing as much as
yours, so check the provider's data policy and your employer's before pointing
it at a real workspace.

## Design notes

- **Why `kind` has `other`.** A Choice must return something. Without a no-match
  option, "The coffee machine on floor 3 is broken again" gets filed as
  `incident` at plausible confidence. It still might — which is why `kind` is
  displayed but never used by `surfaces()`.
- **Why replies are in state.** "Has anyone answered this?" is unanswerable from
  the top-level message alone. The thread is the evidence.
- **Why a direct mention short-circuits everything.** Being named is a fact, not
  a judgment, and no relevance model should be allowed to overrule it.

## Limits

- **No thread reconstruction.** Each message plus its replies is judged as one
  unit. A twelve-message argument surfaces as twelve decisions if you let it;
  grouping by thread before sending is the obvious improvement.
- **No date or time reasoning.** `jev-1.13` cannot compare dates, so "is this
  freeze deadline before my flight?" is not asked. It detects that a time was
  stated; parsing and comparing it is Python's job and is not implemented.
- **Volume.** 400 messages × 6 judgments approaches the context window on the
  32k OpenRouter path. This app does not chunk — see `reading-queue` for the
  batching helper to lift if you need it.
- **It cannot tell you what you missed being told.** Silence in a channel is
  invisible to a filter. If the decision was made in a DM, nothing here helps.
