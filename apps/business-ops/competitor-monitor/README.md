# 🔭 Competitor Monitor

A relevance filter over a competitor-news firehose. Relevance is relative to a
roadmap you write in prose — which is the only way the word means anything.

## The problem

Competitive intelligence arrives as a firehose: changelogs, funding rounds,
partnership posts, analyst reports, conference keynotes, someone's manifesto
about how data quality is everyone's job. A product team subscribes to all of
it, reads none of it, and then finds out from a prospect that the competitor
shipped the thing they are three sprints away from launching.

Summarisation does not help. A digest of fifteen items is still fifteen items,
and the summary of a funding round is "they raised money", which you could have
guessed. The job is **suppression** — throwing away fourteen of them, with a
reason you can defend when someone asks why their competitor wasn't in the
brief.

And "important" is not a property of the news. Northbeam shipping on-prem is
irrelevant if you have decided against on-prem, and existential if you have
not. Any generic importance score is answering a question nobody asked.

## Why this needs Jev

**Relevance is relative, so the roadmap goes in the state.** Every judgment is
made against a prose description of what you are building and what you are
afraid of. Change the roadmap, and the same firehose produces a different brief
— which is correct, and which no generic classifier can do.

**Shipped versus announced is a separate, independently thresholdable Noul.**
This is the distinction that makes a competitive brief useful. Quillstream's
warehouse-native execution is directly on the roadmap and it is a *private
preview with a waitlist*. Cindermill's anomaly engine is a keynote demo
entering beta "next quarter". Both are on-roadmap; neither is a fire. Folding
"shipped" into a relevance rubric would make them indistinguishable from
Lumea's GA lineage release, which is the actual fire.

**The filter itself is free to re-tune.** Relevance, threat and substance
thresholds are sliders over stored judgments: re-filtering the whole feed costs
nothing. Only editing the roadmap re-runs inference, because only the roadmap
changes what the questions mean.

One request: 15 updates × 8 judgments = 120 questions.

## How it works

```python
questions[f"relevance__{index}"] = score(
    {"task": "How directly does this competitor update bear on the roadmap below?",
     "our_roadmap": roadmap, "update": reference},
    ["The update has nothing to do with anything the roadmap describes",
     "The update is in the same market but touches none of the named roadmap items",
     "The update relates to an area the roadmap names but is not the same capability",
     "The update directly addresses a capability the roadmap says is being built",
     "The update directly addresses a named roadmap capability and appears to ship it first"])

questions[f"shipped__{index}"] = noul(
    {"task": "Does this update describe something that is available to customers now, "
             "rather than announced, previewed, or planned?", ...},
    true="It is described as generally available, released, or in customers' hands",
    false="It is a preview, beta, waitlist, roadmap item, or announcement of future work")
```

Triage is Python:

```python
if judgment["substance"] < substance_at:                        return "noise", ...
if judgment["relevance"] >= relevance_at and shipped:           return "act", ...
if judgment["relevance"] >= relevance_at:                       return "watch", ...
if judgment["threat"] >= threat_at or pricing_move >= 0.7:      return "watch", ...
return "noise", ...
```

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

15 synthetic updates across five competitors ship with it, and most of them are
noise on purpose: an analyst-award press release, a vendor-run benchmark where
the vendor wins every category, a manifesto, an ANZ reseller agreement, a VP
hire. Buried in it: one GA release directly on the roadmap, one pricing model
change, one acqui-hire that buys a roadmap capability, and a free tier launched
by someone the roadmap says you are nervous about.

## Design notes

**Why `substance` gates everything.** It runs first and discards marketing
content regardless of how relevant it scores. A competitor's blog post about
your exact roadmap area is still a blog post. The offline check asserts a
high-relevance, high-threat, low-substance item is suppressed.

**Why `threat` is a second rubric and not part of relevance.** "Does this touch
what we are building" and "would a prospect ask us about it" come apart
constantly. Verdant's free tier touches no roadmap capability and would be in
every deal conversation within a month.

**Why `our_stated_gap` exists.** The roadmap says what you are *not* building.
An update in that area is not irrelevant — it is the evidence that the decision
needs revisiting. It is kept as a separate signal rather than being scored,
because it should inform a strategy conversation, not a weekly triage.

**Why the suppression ratio is in the metric row.** A filter keeping 80% of the
firehose has not filtered. Putting the number on screen makes the thresholds
honest.

## Limits

- **Ingestion is not included.** It takes `{competitor, headline, detail,
  source}` as JSON. Getting the firehose into that shape — RSS, changelog
  scrapers, a press-release feed — is your problem, and it is most of the work.
- **It reads the announcement, not the product.** "Generally available" is a
  claim. `shipped` measures how the vendor describes it, and vendors describe
  waitlists as launches.
- **No dates, no counting.** The model is never asked when something shipped or
  how many releases a competitor made this quarter. `jev-1.13` cannot compare
  dates; ordering and tallies are pandas.
- **A stale roadmap produces a confident, wrong brief.** The roadmap text is the
  entire basis of relevance. Update it when it changes, or the filter quietly
  optimises for last quarter.
- **`jev-1.13` reads literally.** If the roadmap says "Snowflake and BigQuery
  push-down", an update about Databricks push-down scores lower than you might
  want. Write the roadmap the way you would brief an analyst, including the
  adjacencies you care about.
- **It suppresses; it does not decide.** The `act` pile is an agenda item, not
  a re-plan.
