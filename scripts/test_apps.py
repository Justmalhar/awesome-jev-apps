#!/usr/bin/env python3
"""Offline checks for every app's pure logic. No API key, no network.

    uv run --with httpx --with streamlit --with pandas python scripts/test_apps.py

Covers question construction, decision rules, chunking, and batching -- the
parts that can be wrong without the API ever being reached. It does NOT verify
live responses; scripts/smoke_test.py does that and needs a key.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"


def load(app: str, module: str):
    """Import an app's module with its own folder on sys.path, as it runs."""
    path = APPS / app / f"{module}.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(f"{app}_{module}", path)
    loaded = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations via sys.modules, so register before exec.
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def check_semantic_ctrl_f() -> None:
    app = load("semantic-ctrl-f", "app")
    text = (APPS / "semantic-ctrl-f" / "sample_lease.txt").read_text()
    blocks = app.split_blocks(text)
    assert len(blocks) > 10, blocks

    questions = app.build_questions(blocks, "Can I keep a cat?")
    options = questions["best_block"]["criteria"]
    # Every block must be addressable, or the model cannot select it.
    assert len(options) == len(blocks) + 1, "one option per block plus 'none'"
    assert "none" in options, "a Choice must have a no-match escape hatch"
    assert all(str(i) in options for i in range(len(blocks)))
    assert questions["has_answer"]["type"] == "noul"

    # Line-splitting fallback for documents with huge paragraphs.
    assert len(app.split_blocks("x" * 700 + "\n" + "y" * 700)) == 2
    print("  semantic-ctrl-f ok")


def check_feed_ranker() -> None:
    app = load("feed-ranker", "app")
    stories = [{"title": "A compiler in Rust", "url": "u", "points": 1, "comments": 2}] * 3
    questions = app.build_questions(stories, "compilers")
    # 3 stories x (3 dimensions + 1 junk) = 12 questions, one request.
    assert len(questions) == 3 * (len(app.DIMENSIONS) + 1) == 12, len(questions)
    for spec in app.DIMENSIONS.values():
        assert len(spec["levels"]) >= 2
        # Levels must be descriptions, not bare labels.
        assert all(len(level) > 15 for level in spec["levels"]), spec["levels"]
    print("  feed-ranker ok")


def check_paper_screener() -> None:
    app = load("paper-screener", "app")
    papers = json.loads((APPS / "paper-screener" / "sample_abstracts.json").read_text())
    assert len(papers) >= 8
    protocol = app.DEFAULT_PROTOCOL
    questions = app.build_questions(papers, protocol)
    per_paper = len(protocol["include"]) + len(protocol["exclude"])
    assert len(questions) == len(papers) * per_paper

    # The veto rule is the whole point: one fatal exclusion beats every inclusion.
    accept, reject = 0.75, 0.25
    verdict, reason = app.decide([0.99] * 4, [0.95, 0.0, 0.0], accept, reject)
    assert verdict == "exclude", f"a fired exclusion must veto strong inclusions: {reason}"

    verdict, _ = app.decide([0.9, 0.9, 0.9, 0.9], [0.0, 0.0, 0.0], accept, reject)
    assert verdict == "include"

    verdict, _ = app.decide([0.9, 0.5, 0.9, 0.9], [0.0, 0.0, 0.0], accept, reject)
    assert verdict == "review", "borderline must reach a human, not be forced either way"

    verdict, _ = app.decide([0.9, 0.05, 0.9, 0.9], [0.0, 0.0, 0.0], accept, reject)
    assert verdict == "exclude", "a clearly unmet inclusion excludes"
    print("  paper-screener ok")


def check_repo_grep() -> None:
    grep = load("repo-grep", "repo_grep")
    chunks = grep.extract_chunks(ROOT / "_shared", max_chars=4000)
    names = {c.name for c in chunks}
    assert "load_provider" in names and "noul" in names, sorted(names)
    # Line numbers must be real: the parser's, not guessed.
    for chunk in chunks:
        assert chunk.line > 0 and chunk.source.strip()

    batches = grep.batch(chunks, budget_chars=2000)
    assert sum(len(b) for b in batches) == len(chunks), "batching must not drop chunks"
    assert len(batches) > 1, "a small budget must split"
    print(f"  repo-grep ok ({len(chunks)} functions, {len(batches)} batches)")


def check_statement_categorizer() -> None:
    app = load("statement-categorizer", "app")
    rows = [{"description": "TESCO 4471", "amount": -12.0}] * 4
    questions = app.build_questions(rows)
    assert len(questions) == 12, "category + subscription + business per row"
    assert "other" in app.CATEGORIES, "needs a no-match category"
    # Descriptions carry the boundary cases; bare labels are a documented trap.
    assert all(len(v) > 20 for v in app.CATEGORIES.values())
    csv_path = APPS / "statement-categorizer" / "sample_transactions.csv"
    header = csv_path.read_text().splitlines()[0]
    assert "description" in header and "amount" in header, header
    print("  statement-categorizer ok")


def check_ci_triage() -> None:
    app = load("ci-triage", "app")
    failures = json.loads((APPS / "ci-triage" / "sample_failures.json").read_text())
    assert len(failures) >= 10
    assert all({"test", "message", "log"} <= set(f) for f in failures)
    questions = app.build_questions(failures[:5])
    assert len(questions) == 15, "class + retry + blast per failure"
    assert "unclear" in app.CLASSES
    print("  ci-triage ok")


def check_agent_tool_router() -> None:
    router = load("agent-tool-router", "router")
    catalog = router.CATALOG
    assert len(catalog) >= 30, len(catalog)
    names = [t["name"] for t in catalog]
    assert len(names) == len(set(names)), "duplicate tool names would collide as Choice keys"
    assert all({"name", "summary", "parameters"} <= set(t) for t in catalog)
    print(f"  agent-tool-router ok ({len(catalog)} tools)")


def check_no_arithmetic_asked() -> None:
    """jev-1.13 cannot count, add, or compare dates -- documented failure modes.

    Scoped to strings that actually reach the model: the arguments of noul(),
    choice() and score(). Prose in docstrings and CLI help is not a question.
    """
    banned = (
        "how many", "count the", "number of times", "total of", "sum of",
        "add up", "calculate", "how much is", "days between", "earlier than",
    )
    builders = {"noul", "choice", "score"}
    offenders = []

    for path in APPS.rglob("*.py"):
        if path.name == "jev_provider.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in builders:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    lowered = inner.value.lower()
                    hit = next((p for p in banned if p in lowered), None)
                    if hit:
                        offenders.append(
                            f"{path.relative_to(ROOT)}: {name}(...) contains {hit!r} "
                            f"-- {inner.value[:60]}"
                        )

    assert not offenders, (
        "arithmetic/counting asked of the model (do it in Python):\n  "
        + "\n  ".join(offenders)
    )
    print("  no arithmetic asked of the model ok")


def main() -> int:
    print("offline app checks:")
    for check in (
        check_semantic_ctrl_f,
        check_feed_ranker,
        check_paper_screener,
        check_repo_grep,
        check_statement_categorizer,
        check_ci_triage,
        check_agent_tool_router,
        check_no_arithmetic_asked,
    ):
        check()
    print("\nall offline app checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
