# 🧨 api-breaking-change

Which of this release's signature changes will actually break the people using
your library?

```bash
uv run --with httpx python api_break.py sample_api_old.py sample_api_new.py
```

```
BREAK  connect                      2.71/3  new required parameter(s): credentials
BREAK  fetch                        2.64/3  retries: 3 -> 0
risky  Client.send                  1.85/3  blocking -> block codemod-able
BREAK  parse_legacy                 2.40/3  parse_legacy no longer exists
intern _cache_key                   1.10/3  new required parameter(s): namespace
...
```

*(Illustrative. The tool prints its own measured footer.)*

## The problem

Semver is a promise made by the person least able to evaluate it — the author,
at the end of the branch, looking at their own diff. The tooling that exists is
either exact and useless (`griffe`, `pyapi-diff`: every signature delta, ranked
equally, hundreds of lines on a big release) or absent.

The interesting line in the sample is `fetch(url, retries=3)` becoming
`fetch(url, retries=0)`. Nothing raises. Every caller keeps working. Retries
quietly stop happening across every downstream service, and the first anyone
knows is a pager. A structural differ reports it in the same tone as a renamed
private helper.

## Why this needs Jev

**The diff is computed, the materiality is judged.** `ast` knows exactly what
changed — that is set arithmetic over parsed signatures, and asking a model for
it would be slower, costlier, and wrong more often:

```python
dropped = [p for p in before.params if p not in after.params]
changed_defaults = [f"{p}: {before.defaults[p]} -> {after.defaults[p]}" ...]
```

What no parser can answer is whether a change is *material to a consumer*.
That depends on whether the name was ever meant to be called, whether the
failure is loud or silent, and whether a codemod can fix it. Three judgments,
one request, well under a cent for a whole release.

## How it works

```python
questions[f"impact_{i}"] = score(
    {"task": "How would this change affect code outside this library that already uses it?",
     "change": ref},
    IMPACT_LEVELS)
questions[f"mechanical_{i}"] = noul(
    {"task": "Could every affected call site be fixed by a mechanical find-and-replace, with no judgement about intent?",
     "change": ref},
    true="A rename or reordering where the correct replacement is identical everywhere",
    false="Each call site needs a decision about what the caller actually wanted")
```

The impact levels are deliberately **not** ordered by how loud the failure is:

```python
"Only code reaching into a private, underscore-prefixed name could notice this change",
"Documented usage keeps working; only an unusual call style ... breaks",
"Ordinary documented usage now raises immediately at import time or on the first call",
"Calls keep succeeding and return different results, so the breakage is invisible until the data is wrong",
```

The **silent** change ranks above the loud one. A `TypeError` on import is
found by the consumer's own CI within a day. A changed default is found in
production, by someone who does not know your library exists.

## Run it

```bash
cp ../../../.env.example .env
uv run --with httpx python api_break.py                       # uses the bundled pair
uv run --with httpx python api_break.py old.py new.py --json
uv run --with httpx python api_break.py old.py new.py --gate 1.5   # stricter
```

Exits `1` when anything is judged breaking, so:

```yaml
- run: |
    git show origin/main:mylib/api.py > /tmp/old.py
    uv run --with httpx python api_break.py /tmp/old.py mylib/api.py
```

The bundled `sample_api_old.py` / `sample_api_new.py` pair contains a new
required argument, a dropped default, a silent default flip, a deleted
deprecated function, a keyword rename, and two changes to underscore-prefixed
internals that should *not* fail a release.

## Design notes

**Why `documented` is a Noul and not the bottom level of the score.** Whether
something is public is not a lesser degree of impact — it is the gate in front
of the whole question. Keeping it separate means `--public-gate` can be tuned
against a library that marks its surface differently (an `__all__`, a
`@public` decorator) without touching the impact rubric.

**Why `mechanical` is separate too.** It is orthogonal: the loudest break in
the sample is also the most mechanically fixable. Teams treat "breaking but
codemod-able" very differently from "breaking and everyone must think", and
that is a routing decision, so it gets its own bit.

## Limits

- **Python only, one module at a time.** Two files in, signatures out. No
  package-level walk, no `__all__`, no re-exports — a symbol moved between
  modules reads as removed.
- **Signatures only.** A function that keeps its signature and changes its
  return type, raises a new exception, or alters its side effects is invisible
  here. That needs the bodies, which is `repo-grep`'s shape, not this one.
- **Type annotations are ignored.** Narrowing `int` to `Literal[1, 2]` breaks
  callers and produces no change record.
- **No positional/keyword-only detection.** Moving a parameter behind `*` is a
  real break this differ does not report.
- **`jev-1.13` cannot compare versions or dates**, so nothing here reasons
  about deprecation windows. If you want "deprecated for two releases", compute
  it in Python from your own changelog.
- **The gate is unvalidated.** `2.0` was chosen so the sample separates. Run it
  over your last ten releases and move it before making it a required check.
