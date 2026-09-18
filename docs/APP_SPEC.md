# App specification

**Every app in this repo conforms to this document.** Read it fully before
writing one. If you are an AI agent building an app here, this is your contract.

## 1. Files

```
apps/<category>/<slug>/
├── app.py            # Streamlit app  (or <name>.py for a CLI)
├── test_app.py       # REQUIRED. Offline checks. No key, no network.
├── pyproject.toml    # this app's deps alone
├── README.md         # see §6
├── sample_*.{json,csv,txt}   # REQUIRED. It must run before the user supplies anything.
├── jev_provider.py   # GENERATED — never write this by hand
└── providers.toml    # GENERATED — never write this by hand
```

Generate the last two with `python scripts/sync_provider.py` after creating the
folder. **Never create or edit them yourself.**

Apps are **standalone**: no imports from the repo root, no imports from another
app. Someone must be able to copy the folder out and run it.

## 2. The client API

```python
from jev_provider import JevClient, JevError, load_provider, noul, choice, score

provider = load_provider(start=HERE)        # HERE = Path(__file__).resolve().parent
with JevClient(provider=provider) as client:
    answers = client.ask(state, questions)  # ONE request, many questions

answers.noul("qid")           # float 0..1   (no confidence exists for a noul)
answers.choice("qid")         # str, the winning option
answers.score("qid")          # float, can land between levels
answers.probabilities("qid")  # dict[str, float]
answers.confidence("qid")     # choice/score only; raises on a noul
answers.input_tokens          # int
answers.cost_usd              # float, input-only (output is free)
answers.elapsed_s             # float
```

Question builders:

```python
noul("Is X true?", true="what yes means", false="what no means")
choice("Which one?", {"opt_a": "description", "opt_b": "description", "none": "no match"})
score("How much?", ["level 0 description", "level 1 description", "level 2 description"])
```

`instructions` may be a **dict** — preferred when there are several parts:

```python
noul({"task": "Does this clause disadvantage the customer?",
      "clause": f"`clauses[{i}].text`"})
```

Backticked paths reference nested state. Question ids are for your code only;
they are never sent to the model, so put the full meaning in the question.

## 3. Question design — non-negotiable

| Rule | Why |
|---|---|
| **Batch.** Independent questions over the same state go in ONE `ask()`. | They run in parallel against state ingested once. Looping is the #1 rejected mistake. |
| **Every Choice gets a no-match option** (`none`/`other`/`unclear`). | A Choice must return something; without one it nominates the least-wrong option at plausible confidence. |
| **Independent properties = separate Nouls.** | If two labels can be true at once they are not one Choice and not levels on a rubric. Collapsing them destroys separate thresholding. |
| **Score levels describe concrete situations.** | `["low","medium","high"]` is rejected. Each level must stand alone. |
| **A Noul is not an intensity dial.** | 0.5 = yes and no equally likely. For degree, use a Score. |
| **Policy lives in Python.** | Thresholds/weights in code, so tuning re-runs no inference. Never bake a threshold into an instruction. |
| **Put boundary cases in `criteria`.** | The model reads literally. If you'd explain "I meant…", that explanation belongs in the criteria. |

A second `ask()` is justified **only** when an earlier answer decides what state
to send next (see `ai-infrastructure/agent-tool-router`). Otherwise: one request.

## 4. Never ask the model to

- **count** anything (characters, occurrences, list items)
- **do arithmetic** (sums, totals, percentages, differences)
- **compare dates** or compute durations
- **generate** text — it cannot; it selects

Do these in Python. `scripts/test_apps.py` walks the AST of every `noul()`,
`choice()` and `score()` call and **fails the build** on phrases like "how many",
"count the", "sum of", "days between". Rewording past the check is a bug, not a fix.

Batch large inputs against `provider.context_tokens` (TypeSafe 64k, OpenRouter
32k). Never hardcode a context size.

## 5. Required UI/CLI elements

Streamlit apps:

```python
st.set_page_config(page_title=..., page_icon=..., layout="wide")
# sidebar: provider name + model, then the tunable thresholds
# main: input (with bundled sample as default) -> action button -> results
# footer: REAL measured cost, tokens, latency, and question count
```

The footer prints **measured** values from `answers`, never estimates. Use
`width="stretch"` on dataframes (not the deprecated `use_container_width`).

CLIs: `argparse`, results to stdout, diagnostics to stderr, `--json` where it
makes sense, and a meaningful exit code if it can gate CI.

Handle `JevError` and show the message — it already tells the user which key to
set or where to add credit.

## 6. README structure

1. Title + one-line pitch
2. **The problem** — the real workflow, and why it is painful today
3. **Why this needs Jev** — the structural property being exploited. If the
   honest answer is "it's an LLM app but cheaper", the app does not belong here.
4. **How it works** — the actual question design, with a code excerpt
5. **Run it** — `cp ../../../.env.example .env` then the command
6. **Design notes** — why these primitives, why these levels
7. **Limits** — what it cannot do, including relevant `jev-1.13` failure modes

Be honest. A README that only lists wins reads as marketing and gets rejected.

## 7. test_app.py

Required. Runs offline, no key, no network. Export a `check()` that raises on
failure:

```python
"""Offline checks for <slug>."""
from __future__ import annotations
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app  # or your CLI module

    questions = app.build_questions(...)
    assert len(questions) == expected
    # a Choice must have an escape hatch
    assert "none" in questions["..."]["criteria"]
    # Score levels must be descriptions, not labels
    assert all(len(level) > 15 for level in app.LEVELS)
    # exercise every pure decision function at its boundaries
    assert app.decide(...) == "expected"


if __name__ == "__main__":
    check()
    print("ok")
```

Test the **decision logic**, not the API. Put pure logic in named functions
(`build_questions`, `decide`, `split_*`, `batch`) so it is testable without a key.

`scripts/test_apps.py` discovers and runs every `apps/*/*/test_app.py`
automatically — you never edit that file.

## 8. Checklist before you finish

```bash
python scripts/sync_provider.py                       # generate the two files
uv run --no-project --with httpx --with streamlit --with pandas --with scikit-learn \
  python scripts/test_apps.py                         # must pass
uv run --no-project python -m compileall -q apps
```

- [ ] real workflow, not a benchmark
- [ ] exploits a structural Jev property, named in the README
- [ ] runs from a cold clone against bundled sample data
- [ ] every Choice has a no-match option
- [ ] Score levels are concrete situations
- [ ] no counting/arithmetic/date maths asked of the model
- [ ] one request unless a second is genuinely required
- [ ] footer prints measured cost and tokens
- [ ] `test_app.py` passes
- [ ] README covers limits honestly
