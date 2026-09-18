"""Offline checks for repo-grep."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import repo_grep

    # Chunk this app's own folder: ast must find real functions with real lines.
    chunks = repo_grep.extract_chunks(HERE, max_chars=4000)
    assert chunks, "ast should find functions in this folder"
    for chunk in chunks:
        assert chunk.line > 0 and chunk.source.strip()

    batches = repo_grep.batch(chunks, budget_chars=2000)
    assert sum(len(b) for b in batches) == len(chunks), "batching must not drop chunks"


if __name__ == "__main__":
    check()
    print("ok")
