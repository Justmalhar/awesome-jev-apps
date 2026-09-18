# 🔗 Note Linker

You just wrote a note. Which of the 900 notes already in your vault should it
link to?

## The problem

A Zettelkasten is worth exactly as much as its links, and linking is the part
everyone stops doing. Writing the note is the fun bit; remembering that you
argued the opposite position in a note from March is the bit that requires
having read your own vault recently.

The workarounds all fail differently. Backlink panels only show links you
already made. Full-text search finds the notes that share vocabulary, which is
a poor proxy — the note that *contradicts* you often shares no keywords, and
the note that shares every keyword is often your weekly journal. Embedding
similarity finds neighbours but cannot tell you *why* they are neighbours, and
"why" is the whole point: a merge candidate, a contradiction and a prerequisite
need three different actions from you.

## Why this needs Jev

- **Pairwise comparison is the classic N-questions-one-request shape.** 12 notes
  × 5 judgments = 60 questions in one call, with the new note ingested once and
  every pair judged against it in parallel. A chat model needs one call per pair
  or a single prompt whose output you must then parse.
- **Typed relations, not a similarity number.** Cosine distance gives you 0.83
  and no idea what to do. `duplicate`, `contradicts`, `extends` and `background`
  are four separate Nouls you threshold separately, which is how the app can say
  "merge this" versus "you assumed this" versus "you said the opposite here".
- **The link block is assembled by Python from your own titles.** Jev cannot
  generate, so it cannot invent a note that is not in your vault — which is the
  failure mode that makes a generative linker unusable.

## How it works

```python
relation    = score("How closely related are these two notes?", [4 concrete levels])
extends     = noul("Does the new note add detail, evidence, or a development to the existing note's subject?")
background  = noul("Does the existing note explain background that the new note takes for granted?")
contradicts = noul("Do these two notes make claims that cannot both be right?")
duplicate   = noul("Do these two notes cover the same material closely enough that one should be merged?")
```

Four Nouls rather than one five-way Choice, because they are **not mutually
exclusive**. A note can extend an old note *and* contradict part of it — that is
the most interesting case in the vault, and a Choice would force it to pick one.

The relation Score is the gate; the Nouls are the type. Priority order is policy:

```python
def link_type(j, *, relate_gate, noul_gate):
    if j["duplicate"]    >= noul_gate:  return "merge"        # before anything else
    if j["contradicts"]  >= noul_gate:  return "contradicts"  # resolve before extending
    if j["relation"]     <  relate_gate: return None
    if j["extends"]      >= noul_gate:  return "extends"
    if j["background"]   >= noul_gate:  return "background"
    return "see_also"
```

Note that `merge` and `contradicts` fire *before* the relation gate. A near-
duplicate with a low topical-relation score is still worth surfacing; the gate
exists to suppress weak "see also" noise, not to hide conflicts.

The output is a markdown block of `[[wikilinks]]`, built by string assembly from
the vault's own titles.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_vault.json` (12 notes) and `sample_note.md` — a note
about connection-pool sizing that genuinely extends two vault notes, contradicts
an older one titled "Connection limits are an application concern", and should
ignore the sourdough log and the weekly journal.

For a real vault, export an index with `title, path, tags, excerpt` per note.
Obsidian: a short script over the vault folder taking the first ~900 characters
of each file is enough.

## Privacy

The full text of your new note (first 4,000 characters) and the excerpt of every
vault note in the batch are sent to whichever provider `providers.toml` selects.

## Design notes

- **Why excerpts, not whole notes.** `jev-1.13` degrades on large state full of
  irrelevant detail. The first ~900 characters of a note is almost always its
  thesis; sending 900 full notes would be both expensive and worse.
- **Why the new note sits in state, referenced as `` `new_note` ``.** It is sent
  once and pointed at from every question, rather than repeated 900 times.
- **Why both thresholds are sliders.** A sparse vault wants a permissive gate to
  build connective tissue; a mature vault wants a strict one or every note links
  to everything. Same evidence, different vault, $0.00 to switch.

## Limits

- **One direction only.** It judges the new note against existing ones. It does
  not revisit whether two *existing* notes should link — that is O(n²) pairs and
  a different app.
- **Excerpt-deep.** A note whose first 900 characters are front-matter and a
  heading judges as poorly as it reads.
- **No date reasoning.** `jev-1.13` cannot compare dates, so "is this note
  superseded by a newer one?" is not asked. It detects duplication; which note
  is older is Python's business, from the vault index.
- **`contradicts` is the noisiest judgment.** Notes that argue against a
  *hypothetical* position read like contradictions. Raise the Noul gate if you
  see this, or treat that section as "worth re-reading" rather than "wrong".
