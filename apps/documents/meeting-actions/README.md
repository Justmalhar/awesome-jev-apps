# 📝 Meeting Actions

Commitments, owners and deadlines out of a meeting transcript — where the owner
is **selected from the people who actually spoke**, never invented.

## The problem

Somebody has to turn an hour of transcript into a list of "who is doing what by
when". It is nobody's favourite job, so it gets done badly or not at all, and
the things that were genuinely agreed leak out of the week.

The automated version is worse than it looks, because transcripts contain four
things that all *sound* like action items and are not:

- **"Then someone should instrument the 3DS handoff properly."** Real work.
  No owner. The meeting agreed it should happen and nobody picked it up.
- **"I should probably look at the build times at some point."** Sounds like a
  commitment; the speaker clarifies two lines later that it is a complaint.
- **"I'll have the loyalty mock ready for you by Thursday."** — followed, eight
  turns later, by *"Scrap that. Don't do the mock, Mei."* A summariser that
  reads the transcript once and emits a list will ship a cancelled task.
- **"Rahul will add the 3DS handoff events to the schema."** Rahul is not in
  this meeting and has agreed to nothing. This needs escalating, not assigning.

Ask an LLM for "action items with owners" and every one of these comes back
with a confident name against it, because the shape of the requested output has
a slot for a name and something has to go in it. The name may be a person who
never spoke, or one who explicitly refused the work.

## Why this needs Jev

**Jev cannot generate text. It can only select among options you supply.** So
the owner question is not "who owns this?" but "which of *these* options":

```python
def owner_options(names: list[str]) -> dict[str, str]:
    options = {name: f"{name} was made responsible for doing the work." for name in names}
    options[UNASSIGNED] = ("The turn says the work should happen but makes no specific "
                           "person responsible for it, for example 'someone should do this'.")
    options[EXTERNAL] = ("Responsibility is placed on a named person who does not speak "
                         "anywhere in this transcript.")
    return options
```

`names` comes from `roster()`, which is nothing more than the distinct speaker
labels Python found in the file. Rahul and Jonas appear in the sample transcript
by name and are therefore visible to the model — but they are not options, so
they cannot be returned. Those turns come back `external`, which is the answer a
project manager actually needs.

Same trick for the deadline: the options are the deadline-shaped spans Python
regexed out of *that one turn*, plus `none`. Nothing can be returned that is not
a literal slice of what was said.

And the same trick for the action text itself: the "action" is the turn,
verbatim. Nothing gets rewritten, so nothing gets rewritten *wrongly*.

**The other structural property is independent thresholding.** Three things are
separately true about a turn, so they are three Nouls, not one rubric:

| Noul | Question |
|---|---|
| `commit_{i}` | Was a specific piece of work actually taken on in this turn? |
| `stands_{i}` | Reading the whole transcript, does it still hold at the close? |
| `timed_{i}` | Was a time stated at all? |

Collapsing those into one "is this an action item" score would destroy exactly
the distinctions the app exists to make. A turn can be an unambiguous
commitment *and* be retracted later; those are not points on a scale.

The policy that consumes them lives in Python, so re-tuning costs nothing:

```python
if commit_p < commit_threshold:   return "discarded", ...
if stands_p < stands_threshold:   return "discarded", "called off later in the meeting"
if owner == UNASSIGNED:           return "needs owner", "nobody was put on it"
if owner == EXTERNAL:             return "needs owner", "placed on somebody not in the room"
if owner_confidence < owner_threshold: return "needs owner", "owner is ambiguous"
return "action", ...
```

The retraction veto is applied **before** the owner test on purpose: a
cancelled task is not an action item that merely lacks an owner.

## How it works

Every judgment about every turn goes in **one** `ask()`. The bundled 49-turn
transcript yields 164 questions in a single request; one request per turn would
be 39 round trips, and one per judgment would be 164.

The third Noul, `timed_`, exists to catch the regex's own failures. The sample
contains *"drop it in the Figma file before the RC cuts"* — a real deadline that
no date pattern will ever match. The Choice correctly returns `none`, the Noul
fires, and the row is labelled **"stated, no span matched"** rather than being
silently marked undated. The model is never given the opportunity to write a
deadline to fill that gap.

Relative deadlines stay relative. `is_relative()` is a regex in Python:
`14 March` is absolute, `end of next sprint` is not. Resolving *"end of next
sprint"* to a date needs your sprint calendar, and `jev-1.13` cannot compare or
compute dates in any case — so it is never asked to.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

The sample transcript is loaded by default. Things to watch in the output:

| Turn | What should happen |
|---|---|
| *"I'll have the guard patched … by Friday"* | **action**, owner Tomas, deadline `Friday` (relative) |
| *"14 March"* | `absolute`, because Python said so, not the model |
| *"Then someone should instrument the 3DS handoff"* | **needs owner** → `unassigned` |
| *"Rahul will add the 3DS handoff events"* | **needs owner** → `external` |
| *"I'll have the loyalty mock ready … by Thursday"* | **discarded** — `stands_` collapses on *"Scrap that"* |
| *"I should probably look at the build times"* | **discarded** — the speaker calls it a complaint two turns later |
| *"…before the RC cuts"* | deadline `stated, no span matched` |

Switch providers without touching code:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

## Design notes

**Why the owner is a Choice and commitment is a Noul.** Exactly one person owns
a given piece of work, so ownership is mutually exclusive — a Choice. Whether
work was taken on, whether it survives, and whether timing was given are three
separate yes/no facts that can hold in any combination — three Nouls.

**Why `unassigned` and `external` are separate options and not one "other".**
They lead to different actions. `unassigned` means the meeting needs to pick
somebody; `external` means somebody outside the room has to be asked and may
say no. Merging them would put the escape hatch in place but throw away the
only useful thing about it.

**Why `stands_` reads the whole transcript.** Retraction is not a property of a
turn, it is a property of the turn *plus everything after it*. The Noul is
asked per turn but answered against the full transcript, which is in state.

**Why the commitment threshold defaults low (0.60).** A false action item costs
someone ten seconds to delete. A missed commitment costs a sprint. The sliders
exist so you can move that trade-off; the defaults lean toward over-capturing.

**Why short turns are dropped.** `MIN_TURN_CHARS = 20` skips *"Go ahead."*,
*"And?"*, *"A complaint."* — acknowledgements that cannot carry a commitment.
This is a cost optimisation with a known ceiling, called out in the source: if
your team says *"I'll do it by Friday."* and nothing else, lower it.

## Limits

- **`Speaker: text` lines or nothing.** Diarised output from most
  transcription tools looks like this; raw Zoom/Teams exports with timestamps
  on their own lines will need a different splitter. Text before the first
  speaker label is dropped.
- **Speaker labels are the identity model.** "Dan" and "Daniel Okoro" in the
  same transcript are two people as far as this is concerned. There is no
  entity resolution, deliberately — inventing one would reintroduce exactly the
  guessing this app removes.
- **If the regex misses a deadline span, the model cannot supply it.** That is
  the price of the verbatim guarantee. You get a flag rather than a wrong date,
  which is the right failure, but it is still a failure.
- **Relative deadlines are not resolved.** No meeting date is used and none is
  asked for. "Friday" stays "Friday". Resolving it is your calendar's job.
- **`jev-1.13` cannot count, do arithmetic, or compare dates.** Nothing here
  asks it to, and `scripts/test_apps.py` fails the build if a future edit does.
- **It reads literally.** Sarcasm, a commitment made by a shrug, and *"sure,
  I guess"* are not reliably read as commitments. Put the boundary cases you
  care about into the `true`/`false` criteria text.
- **Long transcripts degrade.** Every turn's questions are answered against the
  whole transcript, and `jev-1.13` degrades on large state full of irrelevant
  detail. A two-hour all-hands is better split by agenda item, which also keeps
  `stands_` honest — a retraction can only be found inside the state you send.
- **Confidence is not correctness.** The owner confidence slider gates on
  distribution concentration, which is not a probability that the owner is
  right. It is a filter for ambiguity, not a guarantee.
