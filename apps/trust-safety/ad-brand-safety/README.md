# 📺 Ad Brand Safety

Judges one creative against many placement contexts — **as pairs** — using the
brand's own written exclusion rules.

## The problem

Keyword blocklists are the industry's standing joke. They defund a news site
over the word "shooting" in a film review, and they sail straight past the
placement that actually ends up on a screenshot in a trade magazine — because
brand damage never lives in a keyword. It lives in the *relationship* between
two things: what the ad says, and what the page beside it is about.

"Treat yourself this weekend" is a perfectly good headline. It is fine on a
recipe page, fine on a sports report, and career-ending directly beneath a
coach-crash story. No property of the creative and no property of the page
predicts that. Only the pair does.

Meanwhile the actual exclusion list at most advertisers is a document their
legal and brand teams wrote, specific to that brand's history and category, and
no vendor taxonomy encodes it.

## Why this needs Jev

1. **Two states, judged together.** The creative and the placement both go into
   state, and every question names both — `` `creative.headline` `` and
   `` `placements[i].page_summary` `` in the same instruction. This is a
   compatibility judgment, not a classification of either side. Classify the
   page alone and you have rebuilt the blocklist that already failed.
2. **The brand's rules are state.** One Noul per exclusion rule, pointing at
   `` `rules[j].text` ``. Legal amends the document, the questions change on the
   next run. No taxonomy mapping, no vendor ticket.
3. **Three hazards, three Nouls.** Implied comment, tonal clash, and audience
   mismatch co-occur freely and have completely different owners — legal, brand,
   and media buying. Separate Nouls keep separate thresholds.
4. **Price.** Six rules × a placement list of thousands, at $0.042/Mtok, is
   cheap enough to re-run the whole plan when the creative changes — which is
   exactly when a blocklist built for the old creative silently stops applying.

## How it works

```python
pair = {"ad_headline": "`creative.headline`", "ad_body": "`creative.body`",
        "page_title": f"`placements[{index}].page_title`",
        "page_summary": f"`placements[{index}].page_summary`", ...}

for position, rule in enumerate(rules):
    questions[f"rule_{index}_{position}"] = noul(
        {"task": "Would running this ad on this page breach the brand exclusion rule quoted below?",
         "rule": f"`rules[{position}].text`", "pair": pair}, ...)

questions[f"implied_comment_{index}"]   = noul("... would the ad read as commenting on the page's subject?")
questions[f"tone_clash_{index}"]        = noul("... does the ad's tone clash with the page's register?")
questions[f"audience_mismatch_{index}"] = noul("... is this page's audience the wrong one for the offer?")
questions[f"suitability_{index}"]       = score(..., SUITABILITY_LEVELS)
questions[f"context_{index}"]           = choice(..., CONTEXT_KINDS)   # incl. unclear
```

```python
def decide(rule_hit, hazards, suitability, gates):
    flagged = [n for n, p in hazards.items() if p >= gates["hazard"]]
    if rule_hit >= gates["rule"]:      return "block" if suitability >= gates["block"] else "human_review"
    if flagged:                        return "human_review"
    if suitability >= gates["block"]:  return "human_review"   # a gap in the rule set
    if suitability >= gates["caution"]:return "reduce"
    if rule_hit >= gates["uncertain"]: return "human_review"
    return "run"
```

Damage with **no rule behind it** does not auto-block. It routes to a person,
because what it really means is that the brand's exclusion document is missing a
line — and that is a thing for a human to write, not for an app to infer.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

One creative (a cheerful home-coffee subscription), six exclusion rules, and ten
synthetic placements ship with it: a crash report, an election dispute, an
obituary, a recall notice for the advertiser's own category, an unmoderated
comment thread, a children's crafts page, and four perfectly good coffee-adjacent
pages that must come back `run`. It runs cold.

## Design notes

- **Suitability is framed as "if a journalist screenshotted it".** That is the
  actual harm model in media buying, and it gives the model something concrete
  to judge instead of an abstraction.
- **`unclear` is mandatory** on the context Choice. Most of the open web fits
  none of the listed kinds, and without an escape hatch an ordinary recipe page
  gets filed as `hard_news_distress` at plausible confidence.
- **`reduce` exists** because the honest answer to mild awkwardness is neither
  "run it" nor "block it" — it is "keep serving and drop the frequency", which
  is what media buyers already do manually.
- **The model never counts.** Rule firing frequencies, queue sizes, and the
  worst rule per placement are Python. `jev-1.13` cannot count.

## Limits

- **This assists human media and brand-safety reviewers. It does not replace
  them.** Blocking inventory moves money away from a publisher; that decision
  affects a real business and needs a person.
- **The thresholds are placeholders**, picked so the sample exercises every
  route. Tune them on placements your own brand team has already ruled on, and
  measure over-blocking explicitly — over-blocking is not the safe direction, it
  is a transfer of money away from news publishers with no audit trail.
- **Text only, and a summary at that.** It never sees the rendered page, the
  images beside the slot, the video it preceded, or what loads below the fold.
  Page summaries are truncated to 1,200 characters on purpose — large state full
  of irrelevant detail measurably degrades accuracy.
- **A page changes after you judge it.** A live news page's subject can invert
  within an hour of a verdict. This is a pre-bid or a plan-review tool, not a
  standing guarantee.
- `jev-1.13` reads literally: satire sites, opinion columns written in character,
  and headlines that quote the thing they criticise are known weak spots.
- Rules that depend on something not in the state — a publisher's contract tier,
  historical viewability, or an allowlist — cannot be applied here. Enforce them
  in your own code before or after this step.
