# 🧒 Age Appropriateness

Rates content against an age-band rubric **the operator writes**, with their own
named concerns as independent flags.

## The problem

An age rating is not a fact about content. It is a fact about a jurisdiction, a
platform, and a promise made to parents. A pre-school video service, a games
storefront, and a general social feed looking at the same eleven-minute cartoon
will legitimately land on three different bands — and every one of them has its
band definitions written down in a policy document that no classifier has ever
read.

So content review teams do it by hand, inconsistently, and the appeals arrive
from parents on one side and creators on the other. Buying a generic "safe for
kids" score does not help, because the argument is never about whether the clip
is wholesome; it is about which of *your* five bands it belongs in.

## Why this needs Jev

1. **The rubric is the operator's text.** The bands typed into the sidebar
   become the levels of a Score, in order, youngest first. Rewrite a band and
   the question is rewritten. This is the one primitive where an operator's
   policy document maps directly onto the model's interface — a Score *is* an
   ordered rubric, which is exactly what an age policy is.
2. **A Score returns a position, not a bucket.** `1.7` means it sits between
   band 1 and band 2 and leans older. That between-ness is the useful part, and
   Python turns it into a band with `assign_band()`, which rounds **up** at a
   tunable margin — because for age ratings the safe direction is the older
   audience, and that asymmetry is a policy choice that belongs in code.
3. **Concerns are separate Nouls.** "Could a child copy this at home" and "does
   this push purchases" are independent of the band and of each other. A single
   rubric would force the imitable-stunt video and the loot-box game onto one
   dial, when they need different thresholds and different reviewers.
4. **Price** makes it viable to re-rate the entire back catalogue when the
   policy changes — which, after any regulatory letter, is precisely the job.

## How it works

```python
questions[f"band_{index}"] = score(
    {"task": "Which of these audience bands does this content belong in, "
             "using the platform's own definitions?", "content": reference},
    bands)                                     # <- the operator's own lines

for position, concern in enumerate(concerns):  # <- the operator's own concerns
    questions[f"concern_{index}_{position}"] = noul(
        {"task": "Is the concern described below present in this content?",
         "concern": f"`concerns[{position}].text`", "content": reference}, ...)

questions[f"borderline_{index}"] = noul("Would two reviewers applying these same "
                                        "band definitions land in different bands?")
questions[f"driver_{index}"]     = choice(..., DRIVER_KINDS)   # incl. nothing_notable
```

```python
def assign_band(value, bands, margin):        # rounding is Python's job
    lower = max(0, min(int(value), len(bands) - 1))
    if lower >= len(bands) - 1: return len(bands) - 1
    return lower + 1 if (value - lower) >= margin else lower

def decide(band_index, confidence, borderline, concerns, gates):
    flagged = [n for n, p in concerns.items() if p >= gates["concern"]]
    if confidence < gates["confidence"]:                 return "human_review"
    if borderline >= gates["borderline"]:                return "human_review"
    if flagged and band_index < gates["concern_floor"]:  return "human_review"
    return "publish"
```

The app **refuses to run** on a rubric of labels: `rubric_problems()` rejects any
band under 16 characters before a request is sent. `["PG", "12", "18"]` is not a
rubric, it is a legend, and a model given a legend invents the definitions.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five sample bands, six sample concerns, and ten synthetic items are bundled: a
paper-plate craft video, a cartoon with one minute of mild peril, a rooftop
stunt filmed without warnings, a match-three game with streak-pressure and paid
chests, an unmoderated homework livestream that reads out children's towns, two
chefs mildly swearing at each other, a factual teen documentary about vaping,
and a late-night poker stream. They exercise every band. It runs cold.

## Design notes

- **Bands are validated, not trusted.** The 16-character floor is enforced in
  Python because an operator in a hurry will type "PG" and get confident
  nonsense back.
- **`nothing_notable` is mandatory** on the driver Choice. Most content is
  unremarkable, and without the escape hatch a counting song gets attributed to
  `mature_themes` at plausible confidence.
- **The concern floor is an index, not a threshold on the model.** A gambling
  reference in the adults-only band is expected; the same reference in the
  four-year-old band is an incident. Same Noul, different meaning by band —
  which only works because band and concern are separate answers.
- **The model never counts.** Band distribution, flag tallies, and queue sizes
  are all Python.

## Limits

- **This assists human content reviewers. It does not replace them.** A rating
  decides what a child can see; it needs a person to sign it off, and this app's
  "publish" route should be read as "a reviewer can approve this quickly", not
  as a decision.
- **The thresholds are placeholders**, picked so the sample exercises every
  route. Tune them against items your own reviewers have already banded, and
  measure both directions — an over-strict rating suppresses a creator, an
  over-loose one is the failure that makes the news.
- **It reads a summary, not the content.** No video, no audio, no images, no
  thumbnails, no comment section. A summary that omits the frightening thirty
  seconds produces a confident, wrong rating — garbage in, confident garbage out.
- **No jurisdiction logic.** Legal age thresholds differ by country and this app
  knows none of them. It applies the bands you typed, nothing more.
- `jev-1.13` reads literally: a documentary *about* a harm and content
  *promoting* that harm use much the same vocabulary, and the bundled vaping
  documentary is included precisely to show that boundary being tested.
- Confidence is not correctness. A high-confidence band on content whose summary
  is misleading is still wrong, and the `borderline` Noul catches only the cases
  the model itself can see are close.
