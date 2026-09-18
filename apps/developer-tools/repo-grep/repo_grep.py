#!/usr/bin/env python3
"""repo-grep -- grep a codebase for things you can only describe in English.

    uv run --with httpx python repo_grep.py . "makes a network call with no timeout"
    uv run --with httpx python repo_grep.py ./src "catches an exception and silently swallows it"
    uv run --with httpx python repo_grep.py . "builds SQL by string concatenation" --json

Regex finds the CANDIDATES; Jev judges them. Python's `ast` module chunks each
file into functions -- exactly, with correct line numbers, for free -- and Jev
answers one yes/no per function. Asking a model to find function boundaries
would be slower, costlier, and worse than the parser that ships with the
language.

Deliberately a CLI and not a Streamlit app: this output belongs in a pipe and
in CI, next to ripgrep.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_provider import JevClient, JevError, load_provider, noul  # noqa: E402

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".ruff_cache", "build", "dist", ".tox", "site-packages"}
CHARS_PER_TOKEN = 4
# Leave room for the state wrapper and the question text itself.
CONTEXT_SAFETY = 0.6


@dataclass
class Chunk:
    path: str
    name: str
    line: int
    source: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def extract_chunks(root: Path, max_chars: int) -> list[Chunk]:
    """One chunk per function/method, via the stdlib parser."""
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # not our job to fix unparseable files

        relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            source = ast.get_source_segment(text, node) or ""
            if not source.strip():
                continue
            chunks.append(
                Chunk(
                    path=relative,
                    name=node.name,
                    line=node.lineno,
                    source=source[:max_chars],
                )
            )
    return chunks


def batch(chunks: list[Chunk], budget_chars: int) -> list[list[Chunk]]:
    """Pack chunks into requests that fit the provider's context window."""
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    size = 0
    for chunk in chunks:
        cost = len(chunk.source) + len(chunk.path) + 80
        if current and size + cost > budget_chars:
            batches.append(current)
            current, size = [], 0
        current.append(chunk)
        size += cost
    if current:
        batches.append(current)
    return batches


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic grep over a Python codebase.")
    parser.add_argument("path", type=Path, help="directory to scan")
    parser.add_argument("rule", help="what to look for, in plain English")
    parser.add_argument("--threshold", type=float, default=0.6, help="min P(yes) to report")
    parser.add_argument("--max-chunk-chars", type=int, default=4000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--limit", type=int, default=0, help="stop after N functions (0 = all)")
    args = parser.parse_args()

    if not args.path.is_dir():
        print(f"not a directory: {args.path}", file=sys.stderr)
        return 2

    try:
        provider = load_provider(start=Path(__file__).resolve().parent)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    chunks = extract_chunks(args.path, args.max_chunk_chars)
    if args.limit:
        chunks = chunks[: args.limit]
    if not chunks:
        print("no Python functions found", file=sys.stderr)
        return 1

    budget_chars = int(provider.context_tokens * CHARS_PER_TOKEN * CONTEXT_SAFETY)
    batches = batch(chunks, budget_chars)

    if not args.as_json:
        print(
            f"scanning {len(chunks)} functions in {len(batches)} request(s) "
            f"via {provider.name}...",
            file=sys.stderr,
        )

    hits: list[tuple[float, Chunk]] = []
    total_cost = 0.0
    total_tokens = 0

    try:
        with JevClient(provider=provider) as client:
            for group in batches:
                state = {
                    "rule": args.rule,
                    "functions": [
                        {"id": i, "path": c.path, "name": c.name, "source": c.source}
                        for i, c in enumerate(group)
                    ],
                }
                questions = {
                    f"hit_{i}": noul(
                        {
                            "task": "Does this function match the rule?",
                            "rule": args.rule,
                            "function": f"`functions[{i}].source`",
                        },
                        true="The function clearly exhibits what the rule describes",
                        false="The function does not exhibit it, or there is not enough code to tell",
                    )
                    for i in range(len(group))
                }
                answers = client.ask(state, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                for i, chunk in enumerate(group):
                    probability = answers.noul(f"hit_{i}")
                    if probability >= args.threshold:
                        hits.append((probability, chunk))
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    hits.sort(key=lambda pair: -pair[0])

    if args.as_json:
        print(
            json.dumps(
                {
                    "rule": args.rule,
                    "scanned": len(chunks),
                    "hits": [
                        {"path": c.path, "line": c.line, "function": c.name, "probability": round(p, 4)}
                        for p, c in hits
                    ],
                    "cost_usd": round(total_cost, 8),
                },
                indent=2,
            )
        )
    else:
        for probability, chunk in hits:
            print(f"{chunk.location}: {chunk.name}()  P={probability:.2f}")
        print(
            f"\n{len(hits)} hit(s) of {len(chunks)} functions · "
            f"{len(batches)} request(s) · {total_tokens:,} tokens · ${total_cost:.6f}",
            file=sys.stderr,
        )

    # Exit 1 on hits so it works as a CI gate: `repo_grep.py . "rule" || exit 1`
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
