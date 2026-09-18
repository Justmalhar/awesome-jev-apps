# apps/

Eight runnable applications. Each folder is **self-contained** — clone any one
of them out of this repo on its own and it still works.

## Running any app

Every app follows the same three steps:

```bash
cd apps/<app-name>
cp ../../.env.example .env     # add ONE key: TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py    # or: uv run --with httpx python <script>.py
```

`uv` reads each app's own `pyproject.toml`, so dependencies are installed per
app. There is no repo-wide virtualenv to create and nothing to `pip install`.

## The eight

| Folder | Entry point | Interface | Needs |
|---|---|---|---|
| [`semantic-ctrl-f/`](semantic-ctrl-f) | `app.py` | Streamlit | bundled sample lease |
| [`feed-ranker/`](feed-ranker) | `app.py` | Streamlit | live Hacker News API |
| [`inbox-triage/`](inbox-triage) | `app.py` | Streamlit | bundled inbox, or a folder of `.eml` |
| [`paper-screener/`](paper-screener) | `app.py` | Streamlit | bundled abstracts |
| [`repo-grep/`](repo-grep) | `repo_grep.py` | CLI | any Python directory |
| [`statement-categorizer/`](statement-categorizer) | `app.py` | Streamlit | bundled CSV, or your own export |
| [`ci-triage/`](ci-triage) | `app.py` | Streamlit | bundled failures JSON |
| [`agent-tool-router/`](agent-tool-router) | `router.py` | CLI | bundled 42-tool catalog |

Two are CLIs rather than Streamlit apps. That is deliberate: `repo-grep` belongs
in a pipe and in CI next to ripgrep, and `agent-tool-router` is a component you
wire into an agent, not something you sit and click.

Every app ships sample data, so all eight run before you have uploaded anything
of your own.

## Switching provider

Nothing app-specific. Either edit `provider` in the app's `providers.toml`, or:

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

⚠️ OpenRouter serves **32k** context against TypeSafe's **64k**. Apps that pack
large state — `semantic-ctrl-f` on a long contract, `feed-ranker` at 50 stories,
`repo-grep` on a big tree — will need more requests, or will refuse, on
OpenRouter. Each app reads `context_tokens` from config rather than assuming.

## Anatomy of an app folder

```
apps/<name>/
├── app.py / <name>.py   # the whole app, usually one file
├── jev_provider.py      # GENERATED — do not edit
├── providers.toml       # GENERATED — do not edit
├── pyproject.toml       # this app's dependencies alone
├── README.md            # what it does, why it needs Jev, design notes, limits
└── sample_*.{json,csv,txt}
```

`jev_provider.py` and `providers.toml` are copies, written by
[`scripts/sync_provider.py`](../scripts). Editing a copy will be overwritten and
will fail CI. Edit [`_shared/jev_provider.py`](../_shared) and re-sync.

The duplication is the price of every folder being independently forkable, and
it is generated rather than hand-maintained. See [`_shared/README.md`](../_shared/README.md).

## Patterns these apps share

Reading any two of them will show you the house style. In short:

- **One request, many questions.** Independent questions over the same state go
  in a single call. They run in parallel against state ingested once.
- **Every Choice has an escape hatch.** `none`, `other`, `unclear`. A Choice must
  return something, so without one it nominates the least-wrong option with
  perfectly plausible confidence.
- **Independent properties are separate Nouls**, never levels on one rubric.
  Collapsing them destroys your ability to threshold them separately.
- **Score levels describe concrete situations.** `["low","medium","high"]` is not
  a rubric.
- **Policy lives in Python.** Thresholds, weights, and routing sit in code, so
  tuning costs nothing and re-runs no inference.
- **No arithmetic in the model.** `jev-1.13` cannot count or add. Every total on
  every screen is computed in Python — and CI fails the build if a question
  asks otherwise.

## Adding an app

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the editorial bar. Then:

```bash
mkdir apps/my-app
# write app.py, pyproject.toml, README.md, sample data
python scripts/sync_provider.py     # generates jev_provider.py + providers.toml
python scripts/test_apps.py         # add a check for your app first
```
