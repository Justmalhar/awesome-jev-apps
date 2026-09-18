# 🔬 repo-grep

Grep a codebase for things you can only describe in English.

```bash
uv run --with httpx python repo_grep.py . "makes a network call with no timeout"
```

```
src/client.py:84: fetch_user()          P=0.94
src/sync.py:212: push_batch()           P=0.88
tools/upload.py:31: _post()             P=0.71

3 hit(s) of 412 functions · 2 request(s) · 118,402 tokens · $0.004973
```

## The problem

### Why this is possible now and wasn't before

The rule *"makes a network call with no timeout"* has no regex. `requests.get(`
misses `httpx`, `urllib`, a wrapped session, and the helper three layers down;
it also false-positives on every call that does pass a timeout.

Running a frontier LLM over 412 functions costs real money and minutes, so
nobody does it, so the lint never gets written. At **$0.042 per million input
tokens** the whole repo is half a cent, which moves this from "interesting demo"
to "run it in CI on every PR".

## Why this needs Jev

Chunking is done by Python's `ast` module, not by the model:

```python
tree = ast.parse(text)
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        source = ast.get_source_segment(text, node)
```

Exact boundaries, correct line numbers, zero tokens. Asking a model to find
where functions start and end would be slower, costlier, and worse than the
parser that ships with the language. The model is only asked the one thing a
parser cannot answer: *does this code do the thing I described?*

This is the general shape: **known structure in code, semantic judgment in Jev.**

## Batching

Functions are packed into requests sized to the provider's context window,
read from `providers.toml` rather than hardcoded — which matters, because
OpenRouter serves 32k against TypeSafe's 64k, so the same scan takes twice as
many requests there.

## Run it

### As a CI gate

Exits `1` when there are hits, so it drops straight into a workflow:

```yaml
- name: no untimed network calls
  run: |
    uv run --with httpx python repo_grep.py src \
      "makes a network call without an explicit timeout" --threshold 0.8
```

Raise `--threshold` until the false-positive rate is tolerable. Start around
0.8 for a blocking gate and 0.6 for an advisory one, then tune on your own
codebase — these numbers are starting points, not universal.

## Rules that work well

- "catches an exception and silently swallows it"
- "builds SQL by string concatenation instead of parameters"
- "logs something that looks like a credential or token"
- "mutates a default argument"
- "does file I/O inside a request handler"
- "has a retry loop with no backoff"

## Rules that don't

- *"is longer than 50 lines"* — counting. Do it in Python; `jev-1.13` does not count.
- *"is slower than the version in main"* — needs two states and a comparison.
- *"was written after the v2 refactor"* — date reasoning, another documented weak spot.

Anything you'd measure, measure. Anything you'd have to explain to a new
colleague in a sentence, ask.

## Limits

Python only, one chunk per function. Module-level code and class bodies outside
methods are not scanned. Extending to other languages means swapping
`extract_chunks()` for a tree-sitter grammar — the rest is unchanged.
