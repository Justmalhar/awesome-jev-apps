# 🗂️ NPS Coder

Theme thousands of open-ended survey comments against your codebook. One Noul
per theme, so several themes can apply to one comment — because they do.

## The problem

The open-ended box is where the value of a survey lives and it is the part that
never gets analysed. The standard process: export 3,000 comments to a
spreadsheet, have two people read and tag every one against a codebook, spot-
check inter-rater agreement, and publish four weeks later. Or, far more often:
skim fifty, quote three in the deck, and delete the file.

The compromise everyone reaches for — keyword search for "price", "slow",
"support" — misses the comment that says *"paying enterprise money for
something that times out"* under both themes it belongs to, and files *"the
renewal increase was 40% with two weeks notice"* under nothing.

## Why this needs Jev

**Themes are independent properties, so they are independent Nouls.** A comment
about paying enterprise money for a product that times out is `price_value` and
`performance` and `reliability`, all at once. A Choice would make them compete
and drop two of the three. A rubric would pretend they are points on one scale.
Ten separate Nouls, each returning its own probability, is the only shape that
matches the data.

That in turn gives you the thing coding teams actually want: **one threshold per
theme, tunable after the fact**. Inter-rater agreement in manual coding is
partly an argument about where each theme's boundary is. Here the boundary is a
number in the sidebar, and moving it re-codes the whole corpus for $0.00.

**And batching is the economics.** 25 comments × 12 judgments = 300 questions
in one request against a state ingested once. At survey scale the app chunks
against `provider.context_tokens` — never a hardcoded window — and sends one
request per chunk.

## How it works

```python
for theme, definition in codebook.items():
    questions[f"{theme}__{index}"] = noul(
        {"task": "Does this survey comment fall under the codebook theme below?",
         "theme": definition,
         "comment": f"`comments[{index}].text`"},
        true="The comment raises this theme, whether positively or negatively",
        false="The comment does not touch this theme")
```

Chunking reads the provider:

```python
def batch_comments(comments, context_tokens, themes):
    budget = max(context_tokens - OVERHEAD_TOKENS - themes * 60, 2000)
    ...
```

NPS itself is arithmetic, so it is Python:

```python
def nps_score(ratings):
    return 100.0 * (promoters - detractors) / len(buckets)
```

The model is never shown a rating and never asked to score anything numeric.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

25 synthetic comments with 0–10 ratings ship with it, including the ones that
break single-label coding: praise and a blocking complaint in one sentence, a
pricing complaint that is really a notice-period complaint, and one comment
that just says `n/a`.

## Design notes

**Why `sentiment` is a Choice and the themes are not.** Overall evaluation is
genuinely exclusive — a comment cannot be both clearly positive overall and
clearly negative overall; that case *is* `mixed`, which is its own option.
`no_content` is the escape hatch, and the `n/a` comment in the sample is there
to prove it earns its place.

**Why `actionable` is separate.** "Support is great" and "bulk editing takes
200 clicks" are both `usability`-adjacent, but only one of them can be put on a
roadmap. Separating prevalence from actionability is what stops a theme table
from being a popularity contest.

**Why the "no theme matched" panel is prominent.** It is the codebook's blind
spot list. In manual coding this shows up as a growing "other" bucket that
nobody reads; here it is a queue you go through before the next survey.

**Why the theme table shows an NPS delta.** Prevalence alone ranks whatever
people mention most, which is usually the obvious. The delta ranks by what
correlates with the score — and it is a pandas `groupby`, not a judgment.

## Limits

- **The codebook is yours and it is the whole ballgame.** A vague theme
  definition produces vague coding. Write definitions the way you would brief a
  new coder, and put boundary cases in them.
- **`P(theme)` is not inter-rater agreement.** It is the model's probability
  that the statement holds of this text. Validate against a human-coded sample
  before you publish a number off it.
- **No counting, no arithmetic, no dates.** NPS, shares, crosstabs and deltas
  are all pandas. `jev-1.13` cannot do them and is never asked.
- **Chunking is estimated, not exact.** Token cost is approximated at 4
  characters per token with a conservative overhead. If a chunk overflows, lower
  the estimate — do not raise the window.
- **Long comments degrade.** The model reads state in front of it; a 2,000-word
  free-text essay dilutes every theme judgment. Split those before coding.
- **This replaces the first pass, not the analyst.** The interesting output is
  the uncoded pile and the deltas, and both need someone to read them.
