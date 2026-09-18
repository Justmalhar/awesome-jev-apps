#!/usr/bin/env python3
"""pr-risk-scorer -- score a diff for blast radius, then route the review.

    uv run --with httpx python pr_risk.py --diff sample_diff.patch
    uv run --with httpx python pr_risk.py --diff sample_diff.patch --save scores.json
    uv run --with httpx python pr_risk.py --from-scores scores.json --w-security 3.0

The second and third commands are the point. Judgments are scored ONCE and
cached; every reweighting after that is pure Python arithmetic over stored
floats and costs nothing. A prompt-and-parse pipeline that emits "risk: high"
has to re-run the model to answer "what if we cared more about auth?".

The diff is split into per-file hunks by Python, which knows exactly where a
file starts. The model is only asked what a parser cannot answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

CHARS_PER_TOKEN = 4
CONTEXT_SAFETY = 0.5
MAX_HUNK_CHARS = 6000

DEPTH_LEVELS = [
    "Cosmetic only: comments, formatting, import order, or a renamed local variable; behaviour is identical",
    "Self-contained logic change whose effects stop at this file's own callers and are obvious from the diff",
    "Changes a shared interface, schema, or global state that other modules depend on without knowing",
    "Touches authentication, payments, data deletion, or a migration where a mistake cannot be rolled back cleanly",
]

# Policy lives here, not in any instruction, so tuning re-runs no inference.
DEFAULT_WEIGHTS = {"depth": 1.0, "security": 1.5, "irreversible": 2.0, "concurrency": 1.0}
ROUTES = (
    (5.0, "two reviewers, one from the owning team"),
    (2.5, "careful review by a maintainer"),
    (0.0, "skim and merge"),
)


@dataclass
class FileDiff:
    path: str
    patch: str


@dataclass
class FileRisk:
    path: str
    depth: float
    security: float
    irreversible: float
    concurrency: float

    def weighted(self, weights: dict[str, float]) -> float:
        return (
            self.depth * weights["depth"]
            + self.security * weights["security"]
            + self.irreversible * weights["irreversible"]
            + self.concurrency * weights["concurrency"]
        )


def split_diff(text: str, max_chars: int = MAX_HUNK_CHARS) -> list[FileDiff]:
    """One entry per file in a unified diff. Python knows where files start."""
    files: list[FileDiff] = []
    path: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if path is not None and buffer:
            files.append(FileDiff(path=path, patch="\n".join(buffer)[:max_chars]))

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            buffer = []
            parts = line.split(" b/")
            path = parts[-1].strip() if len(parts) > 1 else line[len("diff --git "):].strip()
        elif path is not None:
            buffer.append(line)
    flush()
    return files


def batch(files: list[FileDiff], budget_chars: int) -> list[list[FileDiff]]:
    """Pack files into requests that fit the provider's context window."""
    groups: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    size = 0
    for item in files:
        cost = len(item.patch) + len(item.path) + 200
        if current and size + cost > budget_chars:
            groups.append(current)
            current, size = [], 0
        current.append(item)
        size += cost
    if current:
        groups.append(current)
    return groups


def build_questions(files: list[FileDiff]) -> dict:
    """Four independent judgments per file, all in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(files)):
        reference = {"path": f"`files[{index}].path`", "patch": f"`files[{index}].patch`"}
        questions[f"depth_{index}"] = score(
            {"task": "How deep a review does this file's change deserve?", "file": reference},
            DEPTH_LEVELS,
        )
        # The three below are independent of the depth rubric AND of each other:
        # a migration can be trivial to read, a race condition can live in a
        # cosmetic-looking refactor. Separate nouls keep separate thresholds.
        questions[f"security_{index}"] = noul(
            {"task": "Does this change alter an authentication, authorisation, secret-handling, or cryptographic code path?",
             "file": reference},
            true="The diff changes who may do what, how a credential is stored or checked, or how data is signed or encrypted",
            false="Security-relevant code may be nearby, but this diff does not change its behaviour",
        )
        questions[f"irreversible_{index}"] = noul(
            {"task": "If this change is wrong, would reverting the code be insufficient to undo the damage?",
             "file": reference},
            true="It writes, drops, or rewrites persisted data, sends external side effects, or migrates a schema in place",
            false="A revert restores the previous behaviour completely",
        )
        questions[f"concurrency_{index}"] = noul(
            {"task": "Does this change introduce or alter concurrent behaviour whose correctness depends on ordering?",
             "file": reference},
            true="Threads, async tasks, locks, retries, caches, or shared mutable state are added or restructured",
            false="Execution stays sequential and deterministic, or concurrency is untouched",
        )
    return questions


def route(total: float) -> str:
    """Pure policy. Change these numbers, re-run nothing."""
    for threshold, verdict in ROUTES:
        if total >= threshold:
            return verdict
    return ROUTES[-1][1]


def parse_weights(args: argparse.Namespace) -> dict[str, float]:
    return {
        "depth": args.w_depth,
        "security": args.w_security,
        "irreversible": args.w_irreversible,
        "concurrency": args.w_concurrency,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a git diff for review effort.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--diff", type=Path, default=HERE / "sample_diff.patch",
                        help="unified diff file (default: bundled sample)")
    source.add_argument("--from-scores", type=Path, help="reweight cached scores, no inference")
    parser.add_argument("--save", type=Path, help="write scores to JSON for later reweighting")
    parser.add_argument("--w-depth", type=float, default=DEFAULT_WEIGHTS["depth"])
    parser.add_argument("--w-security", type=float, default=DEFAULT_WEIGHTS["security"])
    parser.add_argument("--w-irreversible", type=float, default=DEFAULT_WEIGHTS["irreversible"])
    parser.add_argument("--w-concurrency", type=float, default=DEFAULT_WEIGHTS["concurrency"])
    parser.add_argument("--gate", type=float, default=5.0, help="exit 1 when total risk reaches this")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    weights = parse_weights(args)

    risks: list[FileRisk] = []
    tokens = questions_asked = requests = 0
    cost = elapsed = 0.0

    if args.from_scores:
        cached = json.loads(args.from_scores.read_text(encoding="utf-8"))
        risks = [FileRisk(**row) for row in cached["files"]]
        print(f"reweighting {len(risks)} cached file score(s) — no inference", file=sys.stderr)
    else:
        if not args.diff.is_file():
            print(f"no such diff: {args.diff}", file=sys.stderr)
            return 2
        files = split_diff(args.diff.read_text(encoding="utf-8"))
        if not files:
            print("no file diffs found", file=sys.stderr)
            return 2
        try:
            provider = load_provider(start=HERE)
            budget = int(provider.context_tokens * CHARS_PER_TOKEN * CONTEXT_SAFETY)
            groups = batch(files, budget)
            print(f"scoring {len(files)} file(s) in {len(groups)} request(s) via {provider.name}...",
                  file=sys.stderr)
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group)
                    answers = client.ask({"files": [asdict(f) for f in group]}, questions)
                    tokens += answers.input_tokens
                    cost += answers.cost_usd
                    elapsed += answers.elapsed_s
                    questions_asked += len(questions)
                    requests += 1
                    for index, item in enumerate(group):
                        risks.append(
                            FileRisk(
                                path=item.path,
                                depth=answers.score(f"depth_{index}"),
                                security=answers.noul(f"security_{index}"),
                                irreversible=answers.noul(f"irreversible_{index}"),
                                concurrency=answers.noul(f"concurrency_{index}"),
                            )
                        )
        except JevError as exc:
            print(f"request failed: {exc}", file=sys.stderr)
            return 1

    if args.save:
        args.save.write_text(json.dumps({"files": [asdict(r) for r in risks]}, indent=2), encoding="utf-8")
        print(f"scores cached in {args.save} — reweight with --from-scores", file=sys.stderr)

    scored = sorted(((r.weighted(weights), r) for r in risks), key=lambda pair: -pair[0])
    total = max((value for value, _ in scored), default=0.0)
    verdict = route(total)

    if args.as_json:
        print(json.dumps(
            {
                "weights": weights,
                "verdict": verdict,
                "files": [{"path": r.path, "risk": round(v, 3), **asdict(r)} for v, r in scored],
                "cost_usd": round(cost, 8),
            },
            indent=2,
        ))
    else:
        for value, risk in scored:
            flags = " ".join(
                name for name, probability in (
                    ("security", risk.security), ("irreversible", risk.irreversible),
                    ("concurrency", risk.concurrency),
                ) if probability >= 0.6
            )
            print(f"{value:6.2f}  {risk.path}  depth={risk.depth:.2f}/3  {flags}")
        print(f"\nverdict: {verdict}")

    if requests:
        print(
            f"\n{questions_asked} question(s) over {len(risks)} file(s) in {requests} request(s) · "
            f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s",
            file=sys.stderr,
        )
    return 1 if total >= args.gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
