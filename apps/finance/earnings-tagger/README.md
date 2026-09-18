# 🎙️ Earnings Tagger

Tag every passage of an earnings call for what it does to guidance, how hedged it
is, and whether the question got answered — then let pandas measure how the tone
moves between the scripted half and the unscripted one.

## The problem

An earnings call runs an hour and produces 8,000 words, of which maybe six
sentences move the stock. Sell-side analysts read every transcript because the
six sentences are not marked, and they are rarely in the press release.

The genuinely useful observations are also the easiest to miss:

- The CFO widened the free cash flow range rather than cutting it — *technically*
  not a downgrade, functionally a downgrade.
- A standalone bookings target was quietly withdrawn in the middle of a paragraph
  about disclosure policy.
- The CEO's prepared remarks were unhedged and their Q&A answers were not.

That last one is the real prize, and no per-passage reading finds it, because it
is not *in* any passage.

## Why this needs Jev

**Per-passage tagging is four independent properties, so it is four questions.**
A passage can raise guidance *and* be heavily hedged — that combination is the
tradeable observation, and it is destroyed by any design that produces one label:

```python
questions[f"guidance_{index}"]   = choice(..., GUIDANCE_MOVES)   # 8 options incl. no-match
questions[f"hedging_{index}"]    = noul(...)
questions[f"deflection_{index}"] = noul(...)
questions[f"commitment_{index}"] = score(..., COMMITMENT_LEVELS)
```

All four, for all 22 passages, in one request — 88 judgments for a fraction of a
cent, which is the only reason tagging every passage rather than the six you
already noticed is a sane thing to do.

**The tone shift is arithmetic, and that is exactly why it works.**
`jev-1.13` cannot compare two things, so it is never asked to. It tags each
passage alone, never seeing two side by side, and pandas does the comparison:

```python
pivot["shift"] = pivot["qa"] - pivot["prepared_remarks"]
```

A per-speaker hedging delta between the drafted half of the call and the live
half. Ask a model "did the tone shift?" and you get a fluent paragraph you cannot
backtest. Subtract two measured rates and you get a number you can put in a time
series across sixteen quarters.

**The Choice needs its escape hatch more than most.** Most passages on any call
set no forward expectation whatsoever — they are results commentary. Without
`no_forward_guidance_in_this_passage`, a Choice must nominate `reaffirmed` for a
sentence about last quarter's gross margin, at thoroughly convincing confidence,
and your guidance-change feed fills with noise.

## How it works

```
22 passages  ->  choice + 2 nouls + score each, ONE request
             ->  notable()       flag the softened-and-hedged combination
             ->  section_shift() prepared vs Q&A, per speaker, in pandas
             ->  confidence gate low-confidence guidance labels out of the feed
```

`notable()` encodes the one pairing worth surfacing first: guidance that softened
*and* was delivered with hedging. Each half is unremarkable alone. A raise is
never "softened" no matter how hedged the delivery — that asymmetry is policy,
lives in Python, and is tested.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: a synthetic Q2 call for "Aldergate Systems" — 22 passages, two speakers,
both halves of the call. Planted in it: a genuine raise stated flatly, a free cash
flow range widened with a reason, a withdrawn bookings target, a refusal to guide,
and a CEO whose Q&A hedging rate is visibly above their prepared-remarks rate
while the CFO's is not.

## Design notes

- **`range_widened` and `withdrawn` are their own options**, not folded into
  `lowered`. They are the moves companies make precisely *because* they are not
  technically a cut, and a taxonomy that cannot express them is a taxonomy that
  misses them.
- **`declined_to_guide` is a guidance event.** "I am not going to give you a
  number today" is information. Treating it as `no_guidance` loses it.
- **Score levels are stances, not intensities.** Distancing, room-to-move,
  credibility-on-the-line are three things a listener does different things with.
  `["low","medium","high"]` would fail review, correctly.
- **Deflection and hedging are separate Nouls.** A crisp non-answer ("I'm not
  giving you that number, here's why") is deflection without hedging, and reads
  completely differently from a hedged non-answer. Collapsing them loses the
  distinction that makes a CFO credible.
- **The low-confidence guidance labels get their own table.** Not hidden, not
  silently trusted — on a transcript, low confidence usually means the passage is
  genuinely ambiguous about whether it is guidance, which is itself worth seeing.

## Limits

- **No memory of last quarter.** "Raised" means the passage *presents itself* as
  raising something. The app has no prior guidance to compare against, because
  comparing two numbers across two documents is arithmetic and is not wired in.
  Feeding it the prior quarter's guidance as state and doing the comparison in
  pandas is the obvious extension.
- **Passage segmentation is yours.** The app takes pre-split passages. Splitting a
  raw transcript by speaker turn is the upstream job, and bad splits produce bad
  tags — a passage containing both a raise and a caveat gets tagged for one of
  them.
- **Hedging is a style, not a signal, for some executives.** Some CFOs hedge
  everything. The absolute rate is nearly meaningless; the *shift* — across
  sections, and across quarters if you store the output — is the part that
  carries information.
- **`jev-1.13` cannot count or do arithmetic**, so nothing here reads the numbers
  *in* the passages. "Revenue grew 14 percent to 412 million" is tagged as
  commentary; extracting 412 is a parser's job.
- **This is not a trading signal.** It is a reading aid that turns a transcript
  into a table. Everything about whether the table predicts anything is
  unvalidated.

## Privacy

Transcript passages, speaker names and company identifiers are sent to whichever
provider `providers.toml` selects. Published transcripts are public, but if you
point this at a draft script, an internal call recording, or anything before
release, you are sending material non-public financial information to a third
party. Read the provider's retention policy first, and check your own compliance
rules before doing it.
