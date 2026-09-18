#!/usr/bin/env python3
"""api-breaking-change -- which of these signature changes will break callers?

    uv run --with httpx python api_break.py sample_api_old.py sample_api_new.py
    uv run --with httpx python api_break.py old.py new.py --gate 2.0 --json

`ast` computes the diff. Removed symbols, added required parameters, renamed
parameters, changed defaults -- all of that is exact, free, and wrong to ask a
model for. What a parser cannot tell you is whether the change MATTERS: adding
a required parameter to `_internal_helper` is noise; changing a default from
True to False is invisible at import time and quietly alters everyone's
results. That judgement is the only thing Jev is asked for.

Exits 1 when anything clears --gate, so it drops into CI as a release gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

IMPACT_LEVELS = [
    "Only code reaching into a private, underscore-prefixed name could notice this change",
    "Documented usage keeps working; only an unusual call style, such as passing every argument positionally, breaks",
    "Ordinary documented usage now raises immediately at import time or on the first call",
    "Calls keep succeeding and return different results, so the breakage is invisible until the data is wrong",
]


@dataclass
class Signature:
    name: str
    kind: str          # "function" | "method"
    params: list[str]
    defaults: dict[str, str]
    required: list[str]
    doc: str
    source: str

    @property
    def public(self) -> bool:
        return not any(part.startswith("_") for part in self.name.split("."))


@dataclass
class Change:
    symbol: str
    kind: str          # removed | param_added | param_removed | param_renamed | default_changed | made_private
    detail: str
    before: str
    after: str


def extract_signatures(source: str) -> dict[str, Signature]:
    """Exact, from the stdlib parser. Never ask a model to read a signature."""
    tree = ast.parse(source)
    found: dict[str, Signature] = {}

    def record(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str, kind: str) -> None:
        arguments = node.args
        positional = [a.arg for a in (*arguments.posonlyargs, *arguments.args)]
        keyword_only = [a.arg for a in arguments.kwonlyargs]
        params = positional + keyword_only
        defaults: dict[str, str] = {}
        # Defaults align to the TAIL of the positional list -- slicing, in Python.
        tail = positional[len(positional) - len(arguments.defaults):] if arguments.defaults else []
        for name, value in zip(tail, arguments.defaults):
            defaults[name] = ast.unparse(value)
        for name, value in zip(keyword_only, arguments.kw_defaults):
            if value is not None:
                defaults[name] = ast.unparse(value)
        full = f"{prefix}{node.name}"
        found[full] = Signature(
            name=full,
            kind=kind,
            params=[p for p in params if p not in {"self", "cls"}],
            defaults=defaults,
            required=[p for p in params if p not in defaults and p not in {"self", "cls"}],
            doc=(ast.get_docstring(node) or "").strip()[:400],
            source=(ast.get_source_segment(source, node) or "")[:1200],
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            record(node, "", "function")
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    record(child, f"{node.name}.", "method")
    return found


def diff_signatures(old: dict[str, Signature], new: dict[str, Signature]) -> list[Change]:
    """Set arithmetic, in Python. The model is never asked what changed."""
    changes: list[Change] = []
    for name, before in old.items():
        after = new.get(name)
        if after is None:
            changes.append(Change(symbol=name, kind="removed",
                                  detail=f"{name} no longer exists",
                                  before=before.source, after=""))
            continue
        added = [p for p in after.params if p not in before.params]
        dropped = [p for p in before.params if p not in after.params]
        if added and dropped and len(added) == len(dropped):
            # Same arity, different names: a rename, not an add plus a remove.
            changes.append(Change(symbol=name, kind="param_renamed",
                                  detail=f"{', '.join(dropped)} -> {', '.join(added)}",
                                  before=before.source, after=after.source))
        else:
            if [p for p in added if p in after.required]:
                changes.append(Change(
                    symbol=name, kind="param_added",
                    detail=f"new required parameter(s): {', '.join(p for p in added if p in after.required)}",
                    before=before.source, after=after.source))
            if dropped:
                changes.append(Change(symbol=name, kind="param_removed",
                                      detail=f"parameter(s) gone: {', '.join(dropped)}",
                                      before=before.source, after=after.source))
        changed_defaults = [
            f"{p}: {before.defaults[p]} -> {after.defaults[p]}"
            for p in before.defaults
            if p in after.defaults and after.defaults[p] != before.defaults[p]
        ]
        if changed_defaults:
            changes.append(Change(symbol=name, kind="default_changed",
                                  detail="; ".join(changed_defaults),
                                  before=before.source, after=after.source))
        now_required = [p for p in after.required if p in before.defaults]
        if now_required:
            changes.append(Change(symbol=name, kind="param_added",
                                  detail=f"default dropped, now required: {', '.join(now_required)}",
                                  before=before.source, after=after.source))
    return changes


def build_questions(changes: list[Change]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(changes)):
        reference = {
            "symbol": f"`changes[{index}].symbol`",
            "what_changed": f"`changes[{index}].detail`",
            "before": f"`changes[{index}].before`",
            "after": f"`changes[{index}].after`",
        }
        questions[f"impact_{index}"] = score(
            {"task": "How would this change affect code outside this library that already uses it?",
             "change": reference},
            IMPACT_LEVELS,
        )
        # Independent of impact: a loud break can still be trivially fixable by
        # a codemod, while a silent one may need every call site reasoned about.
        questions[f"mechanical_{index}"] = noul(
            {"task": "Could every affected call site be fixed by a mechanical find-and-replace, with no judgement about intent?",
             "change": reference},
            true="A rename or reordering where the correct replacement is identical everywhere",
            false="Each call site needs a decision about what the caller actually wanted",
        )
        questions[f"documented_{index}"] = noul(
            {"task": "Was the affected behaviour part of the library's documented, intended surface?",
             "change": reference},
            true="The name and its docstring present this as something callers are meant to use",
            false="It is internal, experimental, or explicitly marked as not part of the public interface",
        )
    return questions


def verdict(impact: float, documented: float, *, gate: float, public_gate: float) -> str:
    """Pure policy: tune without re-running inference."""
    if documented < public_gate:
        return "internal"
    if impact >= gate:
        return "breaking"
    if impact >= gate - 1.0:
        return "risky"
    return "compatible"


def main() -> int:
    parser = argparse.ArgumentParser(description="Flag API changes that break downstream consumers.")
    parser.add_argument("old", nargs="?", type=Path, default=HERE / "sample_api_old.py")
    parser.add_argument("new", nargs="?", type=Path, default=HERE / "sample_api_new.py")
    parser.add_argument("--gate", type=float, default=2.0, help="impact score that fails the build")
    parser.add_argument("--public-gate", type=float, default=0.5,
                        help="P(documented surface) below which a change is treated as internal")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    for path in (args.old, args.new):
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 2

    old = extract_signatures(args.old.read_text(encoding="utf-8"))
    new = extract_signatures(args.new.read_text(encoding="utf-8"))
    changes = diff_signatures(old, new)
    if not changes:
        print("no signature changes")
        return 0

    try:
        provider = load_provider(start=HERE)
        print(f"judging {len(changes)} signature change(s) via {provider.name}...", file=sys.stderr)
        with JevClient(provider=provider) as client:
            questions = build_questions(changes)
            answers = client.ask({"changes": [asdict(c) for c in changes]}, questions)
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    rows = []
    for index, change in enumerate(changes):
        impact = answers.score(f"impact_{index}")
        documented = answers.noul(f"documented_{index}")
        rows.append({
            "symbol": change.symbol,
            "change": change.kind,
            "detail": change.detail,
            "impact": round(impact, 3),
            "confidence": round(answers.confidence(f"impact_{index}"), 3),
            "codemod_fixable": round(answers.noul(f"mechanical_{index}"), 3),
            "documented": round(documented, 3),
            "verdict": verdict(impact, documented, gate=args.gate, public_gate=args.public_gate),
        })
    rows.sort(key=lambda r: -r["impact"])
    breaking = [r for r in rows if r["verdict"] == "breaking"]

    if args.as_json:
        print(json.dumps({"changes": rows, "cost_usd": round(answers.cost_usd, 8)}, indent=2))
    else:
        for row in rows:
            marker = {"breaking": "BREAK", "risky": "risky", "internal": "intern", "compatible": "ok   "}[row["verdict"]]
            fix = " codemod-able" if row["codemod_fixable"] >= 0.6 else ""
            print(f"{marker}  {row['symbol']:<28} {row['impact']:.2f}/3  {row['detail']}{fix}")
        print(f"\n{len(breaking)} breaking change(s) at gate {args.gate}")

    print(
        f"\n{len(questions)} question(s) over {len(changes)} change(s) in 1 request · "
        f"{answers.input_tokens:,} tokens · ${answers.cost_usd:.6f} · {answers.elapsed_s:.2f}s",
        file=sys.stderr,
    )
    return 1 if breaking else 0


if __name__ == "__main__":
    raise SystemExit(main())
