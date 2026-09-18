# 🔖 Bookmark Organizer

File a decade of bookmarks into the folder tree **you** describe — not one
somebody else chose for you.

## The problem

Everyone's bookmark bar has the same three folders: `Read later`, `Work`, and
`bookmarks bar` with 1,400 things in it. Filing a bookmark at save time costs
five seconds you do not have, so nothing gets filed, so the folders are useless,
so you stop filing. Ten years later you have a flat pile you search by half-
remembered title.

Doing it retroactively means 1,400 decisions. Automatic taggers do it *for* you
and produce somebody else's taxonomy — "Technology / Software / Web" — which is
not how you think and therefore not where you will look. The taxonomy has to be
yours, which means the tool has to take it as **input**.

## Why this needs Jev

- **Hierarchy falls out of two Choices in one request.** A top-level Choice and a
  sub-folder Choice per bookmark, judged in parallel, then reconciled in Python.
  26 bookmarks × 4 judgments = 104 questions in one call.
- **Confidence is a first-class output.** `answers.confidence()` on each Choice
  tells you which filings to check by hand. A tagger that returns a label with no
  uncertainty makes you re-check all 1,400 or none.
- **The taxonomy is data, not a prompt.** The folder tree goes into the Choice
  options, so re-describing your tree is a config change. You can redesign your
  folders and re-file, and the app does not change.

## How it works

```python
tops = {name: f"Bookmarks about: {name}." for name in tree} | {"unfiled": "None of the listed folders fits."}
subs = {f"{top}/{sub}": f"{sub} (a sub-folder of {top})" for top in tree for sub in tree[top]}
subs["none"] = "No sub-folder in the whole tree fits; it belongs at the top level."

top       = choice("Which top-level folder does this bookmark belong in?", tops)
sub       = choice("Which sub-folder in the tree fits this bookmark best?", subs)
personal  = noul("Was this saved for personal life rather than for work?")
reference = noul("Is this something to come back to repeatedly rather than read once?")
```

**The sub-folder Choice deliberately ranges over the whole tree**, not just the
children of the chosen top. That lets the two levels disagree — and the
disagreement is the interesting output:

```python
def file_path(j, tree):
    if j["top"] == "unfiled":  return "Unfiled", False
    if j["sub"] == "none":     return j["top"], False
    parent, _, child = j["sub"].partition("/")
    if parent == j["top"]:     return f"{j['top']}/{child}", False
    return f"{j['top']}/_needs a look", True      # it straddles two folders
```

"Notes on making a 3D printed keyboard case" is genuinely Engineering *and*
Home. Constraining the sub-Choice to the chosen parent would have hidden that
behind a confident wrong answer. Surfacing it puts one bookmark in front of a
human instead of twenty-six.

`personal` and `reference` are Nouls, not folders, because they cut *across* the
tree: a reference page exists in every branch. The Work/Life root split is a
checkbox that re-files everything for free.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Runs cold against `sample_bookmarks.json` — 26 bookmarks spanning 2018 to 2024,
including the ones that are hard on purpose: a keyboard-case build log that is
half hobby and half engineering, a "gallery of ancient maps" that belongs
nowhere, a Wirecutter keyboard review that is shopping rather than tooling, and
several reference pages that cut across branches.

Then **edit the tree** in the textarea and re-file. Browser exports: Chrome and
Firefox both export HTML; parse to `title, url, note, added`.

## Privacy

Bookmark titles, URLs and your own notes on them are sent to whichever provider
`providers.toml` selects — a bookmark pile is an unusually good map of a person,
so read that policy before uploading a decade of yours.

## Design notes

- **Why two Choices instead of one flat "top/sub" Choice.** A flat Choice over 15
  leaf folders makes the top-level decision implicit, so you cannot tell "wrong
  branch" from "right branch, wrong leaf" — and cannot file to a top-level
  folder when no leaf fits.
- **Why `unfiled` and `none` exist.** A Choice must return something. Without an
  escape hatch, the map gallery gets confidently filed under `Travel and places`
  at plausible confidence and you never look at it again.
- **Why the review gate is a slider.** Filing 1,400 bookmarks, you might accept
  everything above 0.4 confidence. Filing 40, you would check everything below
  0.8. Same evidence, $0.00 to change your mind.

## Limits

- **Changing the tree costs a full re-run.** The taxonomy is inside the Choice
  options, so new folders mean new questions. Only the *thresholds* are free —
  unlike `task-prioritizer`, there is no reusable half here.
- **Title and note deep, not page deep.** A bookmark titled "Untitled" or
  "(1) LinkedIn" carries no signal. Fetching page titles before filing would
  help and is not implemented.
- **No date reasoning.** `added` is displayed and never sent for judgment;
  `jev-1.13` cannot compare dates, so "is this bookmark stale?" is not asked.
  Sorting or expiring by age is Python's job.
- **Large trees degrade.** Beyond roughly 40 sub-folders the sub Choice starts
  spreading probability thinly and confidence drops across the board. Two passes
  (top-level first, then a per-branch Choice) would scale further at the cost of
  a second request.
- **No deduplication or dead-link detection.** Neither needs a model; both are
  worth adding in plain Python.
