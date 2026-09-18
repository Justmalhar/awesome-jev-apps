# 📦 dependency-audit

Read a dependency's changelog *against your own call sites*, not against the
world.

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

## The problem

Dependabot opens the PR. It knows a version moved and nothing else. The release
has fourteen changelog entries, and the honest answer to "is this safe?"
requires reading all fourteen while holding your own code in your head. So
nobody does it, and the bump gets approved on green CI — which proves only that
nothing you wrote a test for changed.

The entry that gets you is never the one under **Breaking**. It is this, under
*Changed*:

> The default for `fetch(retries=)` is now `0` instead of `3`.

Your `load_manifest()` calls `fetch(url)` and relies on that default. Nothing
raises. CI is green. Retries stop happening across your fleet.

## Why this needs Jev

**Relevance is a judgment about the intersection of two documents**, and it is
only answerable when both are in the same state. Python supplies both sides
exactly:

```python
entries = split_changelog(markdown)                   # per bullet, under its heading
usage   = extract_usage(our_module, "shipyard")       # ast: imports + call sites
```

Then one question the parsers cannot answer — *does this entry touch what we
actually call?* — asked per entry against our usage, batched into one request.
Thirteen entries is 39 questions and a fraction of a cent, which is what makes
it viable to run on every Dependabot PR rather than once a quarter on the one
dependency somebody worried about.

The generic alternative — asking a model "is this release breaking?" — answers
about *a* consumer, not *this* consumer. Half of the sample changelog is
irrelevant to the sample code, and knowing which half is the entire product.

## How it works

`ast` finds the call sites, including objects built from the package:

```python
client = Client()          # tracked
client.send(message, blocking=True)   # ...so this is a call site too
```

Three judgments per entry:

```python
questions[f"relevant_{i}"]      = noul({"task": "Does this changelog entry describe something "
                                                "the call sites shown actually use?", ...})
questions[f"action_needed_{i}"] = noul({"task": "Would somebody have to edit our code, "
                                                "configuration, or data for this entry?", ...})
questions[f"urgency_{i}"]       = score({"task": "What happens to our code if we take this "
                                                 "upgrade and change nothing?", ...}, URGENCY_LEVELS)
```

The urgency rubric is written from the consequence, not the label:

```python
"Nothing we call is affected; this is a routine version bump we can take whenever",
"Something we call changed, but our calls keep behaving exactly as they do today",
"Our code keeps running and quietly does something different, so we would not notice until the output is wrong",
"Our code stops working on upgrade, or keeps a vulnerability open until we act",
```

Level 2 sits below level 3 in loudness and above it in how much it will cost
you. That ordering is the opinion this app has.

## Design notes

**Why relevance and action are separate Nouls.** They come apart in both
directions. The `JobRegistry` leak fix is highly relevant to the sample's
long-running `drain()` and needs no action at all — it is a reason to upgrade.
The `enqueue_many()` deprecation needs action eventually and breaks nothing
today. One combined rubric would have to average them, and you would lose the
ability to list "relevant, nothing to do" separately from "act before 4.0".

**Why the sample usage file is a `.py` read as text.** It is parsed with `ast`,
never imported, so `shipyard` does not have to exist. Point the app at a real
module of yours and it works the same way.

## Limits

- **One module at a time.** Real usage is spread across a package; this reads
  one file. Concatenating your modules works but grows the state, and large
  state full of irrelevant detail measurably degrades accuracy — filter to the
  files that import the package first.
- **Call-site extraction is deliberately shallow.** Aliased imports and objects
  assigned from a package call are tracked; a dependency injected through a
  factory, stored on `self`, or passed as an argument is not.
- **Markdown changelogs only**, split on bullets under headings. A changelog
  written as prose paragraphs produces one enormous entry.
- **No version reasoning.** "Only affects 2.x" or "deprecated since 2.1" needs
  version comparison, which `jev-1.13` cannot do. Filter the changelog to the
  versions between yours and the target in Python before feeding it in.
- **Transitive dependencies are invisible.** This audits the package you named,
  not what it pulls in.
- **It cannot see your test suite.** "Relevant but we have a test that would
  catch it" is a judgment you still have to make.
