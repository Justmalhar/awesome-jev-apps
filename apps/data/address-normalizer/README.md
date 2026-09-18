# 📮 Address Normalizer

Paste a column of addresses that three systems wrote three different ways. Get
structured fields back, plus an honest queue of the ones nobody should trust.

## The problem

Every company that has ever merged two CRMs owns a column that looks like this:

```
Flat 2 12 High St, London, SW1A 1AA
45 albion str, manchester m1 5jw
350 5th Ave Ste 7500, New York, NY 10118
Rue de la Loi 175, 1048 Bruxelles
n/a
```

Someone is asked to turn that into `unit / house_number / street / locality /
city / region / postcode` so it can be deduplicated, geocoded, or handed to a
carrier. The regex approach works for about 70% of a real column and then dies,
because the remaining 30% are not malformed — they are *ambiguous*. In
`Flat 2 12 High St` the `2` is a flat number and the `12` is a house number, but
`Flat B 2 Elm Court` puts the house number in the same slot the flat number
occupied a line earlier. In `Musterstrasse 12, 10115 Berlin` the postal code
leads the city; in `1 Infinite Loop, Cupertino, California 95014` it trails it.
No amount of pattern is going to settle those. A human settles them in a second.

So the column gets a rota of contractors, or it gets silently half-parsed and
the delivery failures show up in the support queue three weeks later.

## Why this needs Jev

**Jev cannot generate text. It selects.** That is usually described as a
limitation; here it is the whole architecture.

Python *enumerates* — cheaply, exhaustively, deterministically. For every
string it produces 3–6 complete structured readings by varying the split points:
where the premises segment ends, whether the leading number is a unit or a
house, whether a trailing digit block is a postal code or part of a name.
Enumeration is the part a computer is good at, and it costs nothing.

Jev *ranks*. One `choice()` per address over **that address's own candidates**,
plus a `none_correct` option meaning every reading misfiles something. The model
never writes a field value, so it cannot hallucinate a street that was not in
the string — every value on screen is a substring of the input, assigned to a
slot by code. The only thing the model contributes is the judgment that
enumeration cannot supply: *which of these readings did the writer mean?*

That is the shape to steal. `candidates in Python → one Choice → escape hatch`
turns a generation problem into a selection problem, and selection is bounded,
auditable, and per-address cheap.

## How it works

One request carries every address in the batch and three questions each — a
Choice and two Nouls that are *independent properties*, not levels on one dial.
An address can be both incomplete and a PO box, or either alone, so collapsing
them into one rubric would destroy the ability to threshold them separately.

```python
options = {key: describe(parse) for key, parse in item["candidates"].items()}
options["none_correct"] = (
    "Every reading above misfiles something: a part of the string is assigned "
    "to the wrong field, or the string is not an address at all."
)
questions[f"parse_{index}"] = choice(
    {"task": "Which of these structured readings is the one the writer of this address meant?",
     "address": reference,
     "readings": f"`addresses[{index}].candidates`",
     "scope": "Judge only where each part of the string belongs. Do not judge whether the place exists.",
     "ambiguity": "A leading number may be a flat or apartment number or the house number. "
                  "A trailing block of digits may be a postal code or part of the street name."},
    options,
)
questions[f"incomplete_{index}"] = noul(...)   # separate property
questions[f"nonphysical_{index}"] = noul(...)  # separate property
```

Batches are sized against `provider.context_tokens` — never a hardcoded 32k or
64k — because the candidate list appears twice in the payload, once in the state
and once in the option descriptions.

Every number on screen (auto-accept rate, queue sizes, flag counts, cost per
address) is computed by pandas from the returned labels. The model is never
asked a statistic; `jev-1.13` cannot count and this app does not pretend
otherwise.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

A 30-address sample ships with the app and loads by default — UK, US, Brazilian,
German, Japanese, Australian and Indian formats, inconsistent casing, missing
postcodes, units before and after the street, `St`/`Street`/`str`, one PO box,
one `c/o`, and two strings that are not addresses at all. Your own CSV needs a
`raw_address` column.

## Design notes

**Why a Choice and not a Score.** The output is a categorical selection over a
set that differs per address. There is no ordering between "reading p2" and
"reading p4", so a rubric would be a lie.

**Why `none_correct` is not optional.** A Choice always returns something. With
six readings and no escape hatch, `see attached spreadsheet tab 3` gets filed as
`street=see attached spreadsheet, city=tab 3` with a perfectly ordinary-looking
confidence. The escape hatch is what converts "the model was wrong" into "the
model told you it had nothing".

**Why the confidence gate is in the sidebar and not the instruction.** Baking
`0.7` into the prompt means re-running inference to retune it. In the sidebar it
re-derives every table on screen for free, because the raw probabilities are
already in the result frame and in the downloaded CSV.

**Why candidates cap at six.** More readings is more tokens per address and a
flatter distribution — the marginal seventh reading is usually a variant of the
third, and it costs confidence on the good ones. Six is the knob's ceiling, not
a law.

**Why the parse is never edited by the model.** `describe()` renders a candidate
as `unit=Flat 2 · house_number=12 · …` and the app looks the winner back up in
its own dict. Even a wrong pick returns a structurally valid record made only of
input substrings.

## Limits

- **This beats a regex on ambiguity. A real postal reference file beats both on
  validity.** Royal Mail PAF, USPS AMS, and their equivalents know which
  addresses *exist*. This app decides which reading was *meant*. It will
  happily, confidently parse `9 Mill Rd, Springfield` into clean fields for a
  Springfield that has no Mill Rd. If deliverability is the requirement, buy the
  reference file — and then use this in front of it, to turn strings into
  something the reference file can be queried with.
- **Recall is capped by the enumerator, not by the model.** If Python never
  proposes the right reading, the best available answer is `none_correct`. Long
  unpunctuated strings with unfamiliar unit designators are where this shows.
  The curator queue is where they land, and that is the intended behaviour, but
  it is a ceiling: improving this app means improving `candidate_parses`, not
  the prompt.
- **No geocoding, no deduplication, no postcode validation.** `SW1A 9ZZ` and
  `SW1A 1AA` are equally acceptable to the postcode pattern.
- **`jev-1.13` failure modes apply.** It cannot count, sum, or compare dates, so
  nothing here asks it to. It also reads literally: the `scope` line in the
  instruction ("do not judge whether the place exists") is load-bearing, because
  without it the model starts penalising readings of real streets it has not
  heard of.
- **Confidence is distribution concentration, not correctness.** A confident
  pick between six readings that are all subtly wrong is still wrong. The gate
  sorts rows by how much the model was guessing, which is a different and more
  useful thing than sorting them by how right it was.
- **Addresses are personal data.** These strings go to whichever provider
  `providers.toml` selects. Read that provider's retention policy before
  pointing this at a customer table.
