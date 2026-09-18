#!/usr/bin/env python3
"""test-gap-finder -- which behaviours in a module have no test?

    uv run --with httpx python test_gap.py sample_module.py sample_tests.py
    uv run --with httpx python test_gap.py src/orders.py tests/test_orders.py --json

Line coverage answers a different question. A behaviour can be executed by a
test that never asserts anything about it -- the classic 100%-covered module
where the refund path is exercised on the way to somewhere else and nobody
would notice if it returned the wrong number.

So: `ast` lists the behaviours (functions, methods, their docstrings, their
raises) and lists the tests. difflib blocks the pairs. Jev answers the one
question neither can: does this test actually exercise that behaviour?
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SOURCE_CHARS = 900
CANDIDATES_PER_BEHAVIOUR = 4

RISK_LEVELS = [
    "Nothing depends on this being right: a debug helper, a repr, or a thin pass-through",
    "A mistake here shows up immediately in the next thing a developer runs, and is obvious when it does",
    "A mistake here reaches a user as wrong output or a confusing error, but is recoverable once noticed",
    "A mistake here loses data, mischarges money, or lets somebody through a check that should have stopped them",
]


@dataclass
class Behaviour:
    name: str
    doc: str
    signature: str
    raises: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class Test:
    name: str
    doc: str
    source: str = ""


@dataclass
class Pair:
    behaviour: Behaviour
    test: Test
    similarity: float


def _words(text: str) -> list[str]:
    return sorted(set(re.findall(r"[a-z0-9]+", re.sub(r"(?<!^)(?=[A-Z])", " ", text).lower())))


def extract_behaviours(source: str) -> list[Behaviour]:
    """Public functions and methods, from the parser. Exact, free."""
    tree = ast.parse(source)
    behaviours: list[Behaviour] = []

    def add(node, prefix: str) -> None:
        if node.name.startswith("_"):
            return  # private helpers and dunders are not the behaviours you ship
        raises = sorted({
            child.exc.func.id if isinstance(child.exc, ast.Call) and isinstance(child.exc.func, ast.Name)
            else (child.exc.id if isinstance(child.exc, ast.Name) else "raise")
            for child in ast.walk(node)
            if isinstance(child, ast.Raise) and child.exc is not None
        })
        behaviours.append(
            Behaviour(
                name=f"{prefix}{node.name}",
                doc=(ast.get_docstring(node) or "").strip()[:300],
                signature=f"{node.name}({', '.join(a.arg for a in node.args.args if a.arg not in {'self', 'cls'})})",
                raises=raises,
                source=(ast.get_source_segment(source, node) or "")[:SOURCE_CHARS],
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, "")
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(child, f"{node.name}.")
    return behaviours


def extract_tests(source: str) -> list[Test]:
    """Anything named test_*. Also from the parser."""
    tree = ast.parse(source)
    tests: list[Test] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests.append(
                Test(
                    name=node.name,
                    doc=(ast.get_docstring(node) or "").strip()[:300],
                    source=(ast.get_source_segment(source, node) or "")[:SOURCE_CHARS],
                )
            )
    return tests


def candidate_pairs(behaviours: list[Behaviour], tests: list[Test],
                    per_behaviour: int = CANDIDATES_PER_BEHAVIOUR) -> list[Pair]:
    """Blocking: the N most name-similar tests per behaviour.

    Deliberately name-based and deliberately generous. It only has to get the
    right test into the shortlist; Jev decides whether it is really a test for
    this behaviour, which is the judgment a name match cannot make -- see
    `test_refund_is_logged`, which names `refund` and asserts nothing about it.
    """
    pairs: list[Pair] = []
    for behaviour in behaviours:
        target = _words(behaviour.name) + _words(behaviour.doc)
        ranked = sorted(
            (
                (difflib.SequenceMatcher(None, target, _words(test.name) + _words(test.doc)).ratio(), test)
                for test in tests
            ),
            key=lambda item: -item[0],
        )
        for ratio, test in ranked[:per_behaviour]:
            pairs.append(Pair(behaviour=behaviour, test=test, similarity=ratio))
    return pairs


def build_questions(pairs: list[Pair], behaviours: list[Behaviour]) -> dict:
    """One noul per candidate pair, plus one risk score per behaviour. One request."""
    questions: dict[str, dict] = {}
    for index in range(len(pairs)):
        questions[f"covers_{index}"] = noul(
            {"task": "Does this test verify that this behaviour is correct?",
             "behaviour": {"signature": f"`pairs[{index}].behaviour.signature`",
                           "docstring": f"`pairs[{index}].behaviour.doc`",
                           "code": f"`pairs[{index}].behaviour.source`"},
             "test": {"name": f"`pairs[{index}].test.name`",
                      "code": f"`pairs[{index}].test.source`"}},
            true="The test asserts something that would fail if this behaviour were wrong",
            false="The test exercises this code only incidentally, or asserts about something else entirely",
        )
    for index in range(len(behaviours)):
        questions[f"risk_{index}"] = score(
            {"task": "If this behaviour were silently wrong, what would the consequence be?",
             "behaviour": {"signature": f"`behaviours[{index}].signature`",
                           "docstring": f"`behaviours[{index}].doc`",
                           "code": f"`behaviours[{index}].source`"}},
            RISK_LEVELS,
        )
    return questions


def find_gaps(behaviours: list[Behaviour], pairs: list[Pair], covered: dict[int, float],
              risks: dict[int, float], *, cover_gate: float, risk_gate: float) -> list[dict]:
    """Pure policy. Best covering test per behaviour, then the gap decision."""
    best: dict[str, tuple[float, str]] = {}
    for index, pair in enumerate(pairs):
        probability = covered.get(index, 0.0)
        current = best.get(pair.behaviour.name)
        if current is None or probability > current[0]:
            best[pair.behaviour.name] = (probability, pair.test.name)

    rows = []
    for index, behaviour in enumerate(behaviours):
        probability, test_name = best.get(behaviour.name, (0.0, ""))
        risk = risks.get(index, 0.0)
        is_gap = probability < cover_gate
        rows.append(
            {
                "behaviour": behaviour.name,
                "signature": behaviour.signature,
                "best_test": test_name if probability >= cover_gate else "",
                "coverage": round(probability, 3),
                "risk": round(risk, 3),
                "verdict": ("untested_and_risky" if is_gap and risk >= risk_gate
                            else "untested" if is_gap else "covered"),
            }
        )
    rows.sort(key=lambda row: (row["coverage"], -row["risk"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Find behaviours with no corresponding test.")
    parser.add_argument("module", nargs="?", type=Path, default=HERE / "sample_module.py")
    parser.add_argument("tests", nargs="?", type=Path, default=HERE / "sample_tests.py")
    parser.add_argument("--cover-gate", type=float, default=0.6, help="P(covered) that counts as tested")
    parser.add_argument("--risk-gate", type=float, default=2.0, help="risk score that fails the build")
    parser.add_argument("--candidates", type=int, default=CANDIDATES_PER_BEHAVIOUR)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    for path in (args.module, args.tests):
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 2

    behaviours = extract_behaviours(args.module.read_text(encoding="utf-8"))
    tests = extract_tests(args.tests.read_text(encoding="utf-8"))
    if not behaviours:
        print("no public behaviours found", file=sys.stderr)
        return 2
    pairs = candidate_pairs(behaviours, tests, args.candidates)

    try:
        provider = load_provider(start=HERE)
        print(f"matching {len(behaviours)} behaviour(s) against {len(tests)} test(s) "
              f"via {len(pairs)} candidate pair(s) on {provider.name}...", file=sys.stderr)
        with JevClient(provider=provider) as client:
            state = {
                "pairs": [
                    {"behaviour": {"signature": p.behaviour.signature, "doc": p.behaviour.doc,
                                   "source": p.behaviour.source},
                     "test": {"name": p.test.name, "source": p.test.source}}
                    for p in pairs
                ],
                "behaviours": [
                    {"signature": b.signature, "doc": b.doc, "source": b.source} for b in behaviours
                ],
            }
            questions = build_questions(pairs, behaviours)
            answers = client.ask(state, questions)
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    covered = {index: answers.noul(f"covers_{index}") for index in range(len(pairs))}
    risks = {index: answers.score(f"risk_{index}") for index in range(len(behaviours))}
    rows = find_gaps(behaviours, pairs, covered, risks,
                     cover_gate=args.cover_gate, risk_gate=args.risk_gate)
    risky = [row for row in rows if row["verdict"] == "untested_and_risky"]

    if args.as_json:
        print(json.dumps({"behaviours": rows, "cost_usd": round(answers.cost_usd, 8)}, indent=2))
    else:
        for row in rows:
            marker = {"untested_and_risky": "GAP! ", "untested": "gap  ", "covered": "ok   "}[row["verdict"]]
            detail = f"covered by {row['best_test']}" if row["best_test"] else f"risk {row['risk']:.2f}/3"
            print(f"{marker} {row['signature']:<40} {detail}")
        print(f"\n{len(risky)} risky gap(s) at risk gate {args.risk_gate}")

    print(
        f"\n{len(questions)} question(s) over {len(behaviours)} behaviour(s) in 1 request · "
        f"{answers.input_tokens:,} tokens · ${answers.cost_usd:.6f} · {answers.elapsed_s:.2f}s",
        file=sys.stderr,
    )
    return 1 if risky else 0


if __name__ == "__main__":
    raise SystemExit(main())
