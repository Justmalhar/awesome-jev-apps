# 🎣 Scam Detector

Screens inbound messages for advance-fee, impersonation, and phishing hazards —
against the patterns **your** fraud team wrote down.

## The problem

Scam text is boring and it mutates weekly. Keyword rules catch last month's
wording and nothing else, which is why the rule list at every platform is
thousands of lines long and still misses.

A general "is this spam?" model does not help either, because the tell is
platform-specific. On a rental marketplace, *asking for a deposit before a
viewing* is the single strongest signal there is. On a holiday-lettings site,
taking a deposit before anyone views anything is how the entire industry works.
No vendor knows which one you are.

Meanwhile a fraud team already keeps a written list of the patterns they see —
in a wiki, in a spreadsheet, in someone's head. It is never a model input,
because turning prose into a classifier used to be a project.

## Why this needs Jev

1. **The patterns are state.** One Noul per pattern, pointing at
   `` `patterns[j].text` ``. A fraud analyst adds a line after a bad weekend and
   the screen changes on the next run — no labelling round, no retraining.
2. **Six hazards, six Nouls.** Advance fee, impersonation, credential request,
   urgency, off-platform move, irreversible payment. They co-occur freely and in
   a different combination in every family: romance fraud is urgency plus
   irreversible payment with no impersonation; support phishing is impersonation
   plus credential request with no payment at all. One "scam score" would erase
   exactly the structure the fraud team reasons with, and one Choice would
   force a message to pick a single hazard when four are true.
3. **The ask is a Score, and it gates quarantine.** Urgency alone describes most
   legitimate messages ever sent. `decide()` will not quarantine anything unless
   several hazards agree *and* the message actually moves the recipient toward
   money or identity. That asymmetry needs degree and flags as separate answers.
4. **Price.** Screening every inbound message rather than only reported ones is
   the whole point, and at $0.042/Mtok it is affordable to do continuously.

## How it works

```python
for position, pattern in enumerate(patterns):
    questions[f"pattern_{index}_{position}"] = noul(
        {"task": "Does this inbound message match the scam pattern described below?",
         "pattern": f"`patterns[{position}].text`", "message": reference}, ...)

questions[f"advance_fee_{index}"]          = noul("... pay something first to receive something later?")
questions[f"impersonation_{index}"]        = noul("... claims to be someone they are unlikely to be?")
questions[f"credential_request_{index}"]   = noul("... tries to obtain a password or one-time code?")
questions[f"urgency_{index}"]              = noul("... time pressure to stop them checking with anyone?")
questions[f"off_platform_{index}"]         = noul("... move the conversation off this platform?")
questions[f"irreversible_payment_{index}"] = noul("... steer toward a payment that cannot be reversed?")
questions[f"ask_{index}"]                  = score(..., ASK_LEVELS)
questions[f"pretext_{index}"]              = choice(..., PRETEXT_KINDS)   # incl. no_pretext
```

```python
def decide(pattern_hit, hazards, ask, gates):
    flagged = [n for n, p in hazards.items() if p >= gates["hazard"]]
    if len(flagged) >= gates["quarantine"] and ask >= gates["ask"]: return "quarantine"
    if pattern_hit >= gates["pattern"]:                             return "human_review"
    if len(flagged) >= gates["quarantine"]:                         return "human_review"
    if len(flagged) >= gates["warn"]:                               return "warn"
    return "deliver"
```

Counting the flagged hazards is done in **Python**, from the Nouls. The model is
never asked for a tally — `jev-1.13` cannot count, and that is a documented
failure mode rather than a prompting problem.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Six sample patterns and ten synthetic messages are bundled: a holding deposit
demanded before a viewing, a fake support request for a verification code, a
prize that costs a gift-card fee to release, a job that requires buying a
licence, a "new number" family message, and five entirely ordinary messages —
including a genuine platform sign-in notice — that must be delivered clean. It
runs cold.

## Design notes

- **Warning beats blocking.** The default `warn` gate is one hazard, because a
  banner on a real message costs a moment of annoyance and a quarantined real
  message costs a tenancy, a job, or a sale.
- **`no_pretext` is mandatory.** Most inbound messages tell no story at all.
  Without the escape hatch an ordinary invoice gets filed as `prize_or_refund`
  at entirely plausible confidence.
- **Score levels are about the ask, not the tone.** The nastiest-sounding message
  on your platform is usually a furious customer, not a fraudster.
- **Every gate is in Python.** Tuning re-runs no inference.

## Limits

- **This assists a human fraud team. It does not replace it.** Suspending an
  account, freezing a payout, or telling a user their contact is a criminal are
  all decisions that need a person.
- **The thresholds are placeholders**, chosen to make the sample exercise every
  route. Tune them on messages your own team has already adjudicated. Measure
  the false-quarantine rate specifically: a scam missed is a loss, a legitimate
  message quarantined is a user who never comes back.
- **A message is the weakest evidence you own.** Account age, device and IP
  reuse, payout destinations, graph links to known-bad accounts, and velocity
  are all stronger. Join this to them; never substitute it.
- **Sophisticated fraud reads clean.** A long-running romance or business-email
  approach spends weeks with no hazard present at all, and the extraction
  message that finally arrives looks like ordinary business.
- `jev-1.13` reads literally: irony, a user *quoting* a scam they received to
  ask about it, and security-awareness training material will all trip hazards.
  That is exactly the kind of thing the human tier is for.
- It degrades on indirection. A pattern that depends on conversation history,
  attachments, or a linked page cannot be applied, because none of those are in
  the state — messages are truncated to 1,500 characters deliberately.
