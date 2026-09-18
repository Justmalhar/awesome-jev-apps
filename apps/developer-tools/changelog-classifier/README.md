# 📝 changelog-classifier

Sort merged PRs into release-note sections, so code can assemble the notes from
lines a human already wrote.

```bash
cp ../../../.env.example .env
uv run --with httpx python changelog.py --prs sample_prs.json
```

```markdown
## Security
- Redact authorization headers from request logs (#2236)

## Breaking
- Breaking: the minimum supported version is now 3.10. Update your runtime before upgrading. (#2218)
- Migration: scripts passing --quiet must be updated to --silent. (#2230)

## Feature
- Users can now set a daily or weekly schedule on any saved report and have the CSV emailed. (#2211)
...

## Needs a human
- #2245 wip
```

## The problem

Release notes are written the night before the release, by reading 60 squashed
commits and deciding which ones anyone outside the team cares about. It is
twenty minutes of tedium that gets skipped, and then the notes say
"various fixes and improvements".

Conventional-commit prefixes are the usual answer and they decay: `fix:` on a
CI change, `chore:` on something that breaks every consumer, and a `feat:`
nobody outside the repo can observe. The label describes the author's intent at
commit time, not the reader's experience at upgrade time.

## Why this needs Jev

**Selection, not generation — and Jev cannot generate, which is the point.**
Every line in the output is verbatim text from the PR: the title, the first
sentence of the body, or the migration note. Python extracts those three
candidate strings; Jev picks which one *is* the release-note line:

```python
ENTRY_SOURCES = {
    "title": "The pull request title reads as a complete, self-contained release-note line.",
    "summary": "The first sentence of the body describes the change better than the title does.",
    "breaking_note": "The migration or breaking-change note is the line a reader most needs to see.",
    "none": "None of the available strings would make a usable release-note line on its own.",
}
```

An LLM asked to *write* the notes will produce fluent lines describing
behaviour that was never shipped, and you cannot review them without reading
every diff — which is the work you were trying to avoid. A selected line is, by
construction, something a teammate wrote about a change they made.

At $0.042/Mtok, four judgments over 60 PRs is a fraction of a cent per release,
so this runs in the release workflow rather than as a chore someone owns.

## How it works

Four judgments per PR, one request:

```python
questions[f"section_{i}"]       = choice({...}, SECTIONS)          # where it goes
questions[f"user_visible_{i}"]  = noul({"task": "Would somebody who only uses the product, "
                                                "and never reads its source, notice this change?", ...})
questions[f"breaking_{i}"]      = noul({"task": "Does upgrading to this require an existing user "
                                                "to change their own code, config, or data?", ...})
questions[f"entry_{i}"]         = choice({...}, ENTRY_SOURCES)     # which existing string to print
```

`assemble()` then applies policy in Python: breaking beats everything, an
`internal` change with high `user_visible` is rescued into Fixes rather than
dropped, and `unclear` goes to a "Needs a human" list at the bottom instead of
being guessed at.

## Design notes

**Why user-visible and breaking are Nouls, not sections.** This is the mistake
the brief invites: "sort into user-facing / internal / breaking" sounds like one
Choice with three options. It is not. #2221 is a `dependency` bump that is also
a security fix. #2248 is a `feature`-shaped API change that is also breaking.
#2239 is `performance` that no user can observe. Those properties co-occur
freely, so they are independent Nouls with independent thresholds — and the
section stays a Choice, because a change belongs under exactly one heading.

**Why `unclear` and `none` exist.** PR #2245 is titled `wip` with an empty
body. Without an escape hatch on both Choices it would be filed confidently
under `internal` with a blank line printed for it. A Choice always returns
something; the only question is whether you gave it somewhere honest to go.

**Why the first sentence is sliced in Python.** `first_sentence()` is a regex.
Asking the model for "the first sentence" is asking it to generate, and it will
paraphrase — at which point the verbatim guarantee is gone.

## Run it

```bash
uv run --with httpx python changelog.py --prs sample_prs.json          # markdown
uv run --with httpx python changelog.py --prs sample_prs.json --json   # for tooling
uv run --with httpx python changelog.py --breaking-gate 0.4            # stricter
```

Feed it from `gh pr list --state merged --json number,title,body,labels`.
Exits `1` when anything landed in "Needs a human", so a release workflow can
stop and ask.

## Limits

- **One line per PR.** A PR that ships three user-visible things gets one line,
  whichever the selection picks.
- **The candidate strings cap the quality.** If nobody wrote a usable sentence,
  the entry is the title, warts and all — `none` flags it but cannot fix it.
- **No grouping or deduplication.** Five PRs implementing one feature produce
  five lines. `issue-deduper`'s shape would fix that; it is a different app.
- **No ordering by importance** within a section. `jev-1.13` cannot count or
  rank across items in one pass; sorting is by PR number, in Python.
- **Labels are shown but not trusted.** They are part of the state the model
  reads, so a repo that lies with its labels will mislead it.
- **Truncation.** Bodies are cut at 700 characters; a migration note buried
  under a long template may not be visible.
