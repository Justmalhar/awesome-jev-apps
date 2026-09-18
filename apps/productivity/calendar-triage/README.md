# 📅 Calendar Triage

Which meetings you could skip, shorten, or delegate — judged against priorities
you write in one sentence.

## The problem

Everyone knows their calendar is 40% waste. Nobody knows *which* 40%. Declining
a meeting costs political capital, so the rational move is to accept everything
and resent it, which is how a week disappears into 60-minute status rounds.

The tools that exist measure the wrong thing. Calendar analytics tell you that
you spent 14 hours in meetings, which you already knew and cannot act on.
"Focus time" blockers defend empty slots without ever asking whether the
meetings around them earned theirs. Nothing reads the invite and asks the only
question that matters: *would anything actually break if I were not there?*

That question is unanswerable in general and trivial once you state your
priorities. This app makes you state them, then applies them to every invite.

## Why this needs Jev

Three structural properties, not "an LLM but cheaper":

1. **Whole week, one request.** 14 meetings × 6 judgments = 84 questions in a
   single call, answered in parallel against state ingested once. A loop would
   be 84 round trips for the same evidence.
2. **Judge once, re-decide free.** `needs_me`, `delegable` and cost-of-absence
   are properties of the invite against your priorities. What *counts* as
   "delegable enough" is policy, and policy lives in Python — every slider
   re-triages the week for $0.00.
3. **It selects, it never writes.** Jev cannot generate, so it cannot invent an
   agenda item or paraphrase a decision out of your invite. Every line of text
   on screen is your own calendar. An app that drafts your decline email is a
   different, riskier app.

## How it works

The interesting design decision is that "should I go?" is **not one question**.
It is four independent properties plus one degree:

```python
needs_me  = noul("Does this meeting need this specific person's input, decision, or authority?")
advances  = noul("Does this meeting move the stated priorities forward?")
delegable = noul("Could a teammate attend in this person's place and report back without loss?")
agenda    = noul("Does this invite state a concrete agenda or a decision to be reached?")
kind      = choice({... decision / working / status / one_on_one / external / ceremony / unclear})
value     = score("What would it cost if this person simply did not attend?", [4 concrete levels])
```

They are separate Nouls because they vary independently and you threshold them
independently. A meeting can be squarely on your priorities *and* fully
delegable (send the deputy). It can need you *and* have no agenda (accept, but
ask for one first). Collapse those into a single "importance" and you lose the
only two levers worth having.

The verdict itself is code:

```python
def decide(j, *, need_gate, delegate_gate, value_gate):
    needed, valuable = j["needs_me"] >= need_gate, j["value"] >= value_gate
    if needed and valuable:              return "attend"
    if j["delegable"] >= delegate_gate:  return "delegate"
    if needed or valuable:               return "shorten"
    return "skip"
```

Hours reclaimed are arithmetic, so Python does them. The model is never shown a
duration or asked to compare one.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

It runs cold against `sample_calendar.json` — a realistic week containing the
cases worth watching: a go/no-go you own, a 41-person status sync, a vendor call
where procurement leads, an agenda-less "Quick sync", and an all-hands that gets
recorded anyway.

For your own calendar, export to JSON with the fields
`title, day, minutes, organizer, attendees, recurring, agenda` and point the
sidebar at the file.

## Privacy

Meeting titles, organizers, attendee lists and full invite descriptions are sent
to whichever provider `providers.toml` selects — read that provider's data
policy before pointing this at a work calendar.

## Design notes

- **Why `kind` is a Choice with `unclear`.** Meeting types are mutually
  exclusive, so a Choice fits — but a Choice *must* return something. Without
  `unclear`, a bare "Quick sync" with no description gets confidently filed as a
  `one_on_one`. With it, low confidence surfaces as "invite too thin to
  classify", which is the honest answer and also actionable: go ask.
- **Why cost-of-absence is a Score, not a Noul.** It is a degree, and the four
  levels are concrete situations ("their absence would slow a decision by a day
  or two while someone chases them"), not `low/medium/high`. A Noul at 0.5 would
  mean "equally likely to matter and not matter", which is a different claim.
- **Why the gates are sliders.** Someone with four reports and someone with
  twenty want different delegation thresholds against identical evidence.

## Limits

- **No date or duration reasoning.** `jev-1.13` cannot compare dates or do
  arithmetic, so it is never asked whether two meetings collide or whether your
  Thursday is overloaded. Conflicts and totals are Python's job; overlap
  detection is not implemented and would be a good addition.
- **Each invite is judged alone.** It cannot see that Tuesday's workshop already
  covers Wednesday's review, so it will not recommend merging them.
- **Thin invites judge thin.** An empty description gives the model almost
  nothing; the `agenda` Noul exists to tell you that is what happened rather
  than let it guess. Garbage calendar hygiene in, hedged judgments out.
- **"Shorten" is a blunt half.** Reclaimed time assumes a shortened meeting runs
  half as long. It is a planning number, not a promise.
