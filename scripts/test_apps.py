#!/usr/bin/env python3
"""Discover and run every app's offline checks. No API key, no network.

    uv run --with httpx --with streamlit --with pandas python scripts/test_apps.py
    python scripts/test_apps.py --only data/entity-resolver

Each app owns its checks in `apps/<category>/<slug>/test_app.py`, exporting a
`check()` that raises on failure. No app is referenced by name in this file --
which is what lets many apps be built in parallel without collisions.

Two repo-wide guards live here, because they are about the whole corpus:
  - no app asks the model to count or do arithmetic
  - every app ships the files the spec requires
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"

REQUIRED_FILES = ("README.md", "pyproject.toml", "test_app.py", "jev_provider.py", "providers.toml")
BANNED_IN_QUESTIONS = (
    "how many", "count the", "number of times", "total of", "sum of",
    "add up", "calculate", "how much is", "days between", "earlier than",
)
QUESTION_BUILDERS = {"noul", "choice", "score"}


def app_dirs() -> list[Path]:
    return sorted(p for p in APPS.glob("*/*") if p.is_dir() and not p.name.startswith("_"))


def _purge_app_modules() -> None:
    """Drop every module that was imported from inside apps/.

    Apps all use the same module names -- `app`, `jev_provider` -- so without
    this the FIRST app's `app` module stays in sys.modules and every later
    test_app.py silently asserts against it. That fails loudly if you are lucky
    and passes wrongly if you are not, so it is purged between every app.
    """
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if origin and Path(origin).is_relative_to(APPS):
            del sys.modules[name]


def run_check(app_dir: Path) -> tuple[bool, str]:
    """Import and run one app's test_app.check(), fully isolated."""
    test_file = app_dir / "test_app.py"
    if not test_file.is_file():
        return False, "no test_app.py"

    name = f"check_{app_dir.parent.name}_{app_dir.name}".replace("-", "_")
    _purge_app_modules()
    sys.path.insert(0, str(app_dir))
    try:
        spec = importlib.util.spec_from_file_location(name, test_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve annotations via sys.modules
        spec.loader.exec_module(module)
        if not hasattr(module, "check"):
            return False, "test_app.py defines no check()"
        module.check()
        return True, ""
    except Exception:
        return False, traceback.format_exc(limit=6)
    finally:
        if str(app_dir) in sys.path:
            sys.path.remove(str(app_dir))
        _purge_app_modules()


def check_required_files() -> list[str]:
    problems = []
    for app_dir in app_dirs():
        missing = [f for f in REQUIRED_FILES if not (app_dir / f).is_file()]
        if missing:
            problems.append(f"{app_dir.relative_to(ROOT)}: missing {', '.join(missing)}")
        if not any(app_dir.glob("sample_*")) and not any(app_dir.glob("*catalog.json")):
            # An app may opt out, but only by stating why in its pyproject.
            manifest = (app_dir / "pyproject.toml")
            excused = manifest.is_file() and "no_sample_data" in manifest.read_text(encoding="utf-8")
            if not excused:
                problems.append(
                    f"{app_dir.relative_to(ROOT)}: no bundled sample data "
                    f"(declare [tool.awesome-jev] no_sample_data = \"<reason>\" if it genuinely needs none)"
                )
    return problems


def check_no_arithmetic() -> list[str]:
    """jev-1.13 cannot count, add, or compare dates. Scoped to strings that
    actually reach the model: the arguments of noul()/choice()/score()."""
    offenders = []
    for path in APPS.rglob("*.py"):
        if path.name in {"jev_provider.py", "test_app.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            offenders.append(f"{path.relative_to(ROOT)}: does not parse: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.id if isinstance(node.func, ast.Name) else None
            if fname not in QUESTION_BUILDERS:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    lowered = inner.value.lower()
                    hit = next((p for p in BANNED_IN_QUESTIONS if p in lowered), None)
                    if hit:
                        offenders.append(
                            f"{path.relative_to(ROOT)}: {fname}(...) contains {hit!r} "
                            f"-- do it in Python: {inner.value[:60]}"
                        )
    return offenders


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run one app, e.g. data/entity-resolver")
    args = parser.parse_args()

    targets = app_dirs()
    if args.only:
        targets = [d for d in targets if f"{d.parent.name}/{d.name}" == args.only]
        if not targets:
            print(f"no app matching {args.only!r}", file=sys.stderr)
            return 2

    print(f"offline checks for {len(targets)} app(s):\n")
    failures: list[tuple[str, str]] = []

    for app_dir in targets:
        label = f"{app_dir.parent.name}/{app_dir.name}"
        ok, detail = run_check(app_dir)
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append((label, detail))

    print()
    if not args.only:
        for label, problems in (
            ("file layout", check_required_files()),
            ("arithmetic asked of the model", check_no_arithmetic()),
        ):
            if problems:
                failures.append((label, "\n    ".join(problems)))
                print(f"  FAIL  {label} ({len(problems)} problem(s))")
            else:
                print(f"  ok    {label}")

    if failures:
        print(f"\n{len(failures)} failure(s):\n")
        for label, detail in failures:
            print(f"-- {label} --\n    {detail}\n")
        return 1

    print(f"\nall offline checks passed ({len(targets)} apps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
