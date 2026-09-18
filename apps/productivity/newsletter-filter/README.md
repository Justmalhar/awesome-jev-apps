# 📨 Newsletter Filter

Keep only the items in this week's digests that you would genuinely have read.

## The problem

You subscribe to five good newsletters. Together they deliver about 25 items a
week, and you read two or three. The other 22 are not *bad* — they are simply
not for you: a funding round, a conference CFP, a gentle introduction to
something you already know, two sponsored slots, and whatever every outlet in
the field covered on Tuesday.

The cost is not the reading, it is the scanning. Twenty-five headline-and-blurb
decisions, five times a week, each one cheap and none of them free. So the mail
piles up unread and you eventually lose the two items that were worth it.

Unsubscribing does not fix this: the newsletters are good, and the two items you
want are in there. You need the *filter*, not fewer sources.

## Why this needs Jev

- **It selects; it cannot generate.** Every headline and blurb on screen came
  from the newsletter. There is no summarised version to mistrust — the whole
  output is a subset of the input, which is the only safe shape for something
  deciding what you read.
- **22 items × 5 judgments = 110 questions, one request.** All five judgments
  are independent, run in parallel against state ingested once.
- **The cut list is visible and the gates are free.** Every dropped item shows
  *why* it was dropped, and every gate re-filters the week for $0.00. A filter
  you cannot audit is a filter you will not trust for long.

## How it works

```python
match      = score("How well does this item match what the reader said they want?", [4 levels])
substance  = score("How much substance does this item carry?", [4 levels])
sponsored  = noul("Is this a paid placement, advertisement, or promotion of a product?")
withholds  = noul("Does the headline withhold the fact it promises so the reader has to click?")
everywhere = noul("Is this item about news the reader has very likely already seen elsewhere?")
```

Two Scores because two independent things go wrong with digest items, and they
are both degrees: *wrong subject* and *nothing in it*. The second is the one
keyword filters miss entirely — "Understanding write amplification" matches every
keyword a storage engineer could name and is a beginner's introduction they do
not need.

Three Nouls because these are facts, not degrees, and they are vetoes with
different politics:

```python
def keeps(j, *, match_gate, substance_gate, noul_gate, drop_duplicates):
    if j["sponsored"] >= noul_gate:                    return False   # always
    if drop_duplicates and j["everywhere"] >= noul_gate: return False # your choice
    if j["match"] < match_gate:                        return False
    return j["substance"] >= substance_gate
```

`withholds` is deliberately **not** a veto. Clickbait framing around a real
result is still a real result; the flag is shown next to the item and you decide.
Making it a veto would be baking an opinion into the filter, and opinions belong
in sliders.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_digest.json` — 22 items across four newsletters for a
data-platform reader, containing two sponsored slots, two pieces of clickbait
wrapped around real content, a funding round, a CFP, a remote-work opinion piece,
an introduction to something the reader already knows, and four items that
genuinely earn the click.

Real digests: an IMAP script that parses each newsletter's list items into
`newsletter, title, blurb, url` is about 40 lines and depends entirely on your
newsletters' HTML.

## Privacy

Item titles, blurbs and newsletter names, plus the sentence describing what you
read, are sent to whichever provider `providers.toml` selects.

## Design notes

- **Why `everywhere` is optional.** "I have seen this everywhere" is true and
  sometimes irrelevant — if you have been offline for a week, the widely-covered
  item is exactly what you want. That is a checkbox, not a rule.
- **Why no Choice here.** There is no mutually-exclusive category worth
  extracting. Every label that matters (sponsored, clickbait, widely-covered) can
  be true simultaneously, which makes them Nouls by construction.
- **Why substance has a bottom level describing "a headline and a link".** It is
  a concrete situation, not the word "low". Newsletters genuinely publish items
  that are nothing but a link, and that level has to name it to catch it.

## Limits

- **Blurb-deep only.** The judgment is made on what the newsletter chose to say.
  A brilliant article with a lazy blurb gets cut, and there is no way around that
  short of fetching every link.
- **"Already seen elsewhere" is a guess about the world.** `jev-1.13` has a
  knowledge cutoff and no access to your history; it is really judging whether an
  item has the *shape* of widely-covered news. It will be wrong about genuinely
  niche items that happen to sound like announcements.
- **No dates.** The model is never asked whether an item is old news, because it
  cannot compare dates. If your digest has timestamps, filter on them in Python.
- **No cross-newsletter deduplication.** If three newsletters cover the same
  release, all three are judged separately and all three can survive. Comparing
  items to each other is a different shape — see `note-linker`.
