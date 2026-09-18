#!/usr/bin/env python3
"""Regenerate every app index from catalog.json + what is actually on disk.

    python scripts/build_catalog.py           # write the indexes
    python scripts/build_catalog.py --check   # fail if any index is stale (CI)

Status is DETECTED, not trusted. An app counts as built only when its folder
has the files the spec requires, so the README can never claim an app exists
when it does not. catalog.json supplies the taxonomy and the copy; the
filesystem supplies the truth.

Generates:
  README.md                      (between the CATALOG markers)
  apps/README.md                 (between the CATALOG markers)
  apps/<category>/README.md      (whole file)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"
CATALOG = ROOT / "catalog.json"

START = "<!-- CATALOG:START -->"
END = "<!-- CATALOG:END -->"

# An app is "built" only if it has all of these.
REQUIRED = ("README.md", "pyproject.toml", "test_app.py")


def is_built(category: str, slug: str) -> bool:
    folder = APPS / category / slug
    if not folder.is_dir():
        return False
    if not all((folder / f).is_file() for f in REQUIRED):
        return False
    # Needs an entry point that is not the generated client or its own test.
    entry = [
        p for p in folder.glob("*.py")
        if p.name not in {"jev_provider.py", "test_app.py"}
    ]
    return bool(entry)


def load() -> dict:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    for category in data["categories"]:
        for app in category["apps"]:
            app["built"] = is_built(category["slug"], app["slug"])
    return data


def summary(data: dict) -> tuple[int, int]:
    apps = [a for c in data["categories"] for a in c["apps"]]
    return sum(1 for a in apps if a["built"]), len(apps)


def progress_bar(done: int, total: int, width: int = 28) -> str:
    filled = round(width * done / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def root_table(data: dict) -> str:
    done, total = summary(data)
    lines = [
        f"**{done} of {total} built** &nbsp; `{progress_bar(done, total)}`",
        "",
        "| Category | Apps | Built | What it covers |",
        "|---|---:|---:|---|",
    ]
    for category in data["categories"]:
        built = sum(1 for a in category["apps"] if a["built"])
        lines.append(
            f"| {category['icon']} **[{category['title']}](apps/{category['slug']})** "
            f"| {len(category['apps'])} | {built} | {category['blurb']} |"
        )
    lines += ["", "<details>", "<summary><b>All 100, by category</b></summary>", ""]
    for category in data["categories"]:
        lines += [f"#### {category['icon']} {category['title']}", ""]
        lines += ["| App | What it does | Why it needs Jev |", "|---|---|---|"]
        for app in category["apps"]:
            mark = "✅" if app["built"] else "⬜"
            name = (
                f"[{app['title']}](apps/{category['slug']}/{app['slug']})"
                if app["built"] else app["title"]
            )
            lines.append(f"| {mark} {name} | {app['does']} | {app['why']} |")
        lines.append("")
    lines += ["</details>", ""]
    return "\n".join(lines)


def apps_table(data: dict) -> str:
    done, total = summary(data)
    lines = [f"**{done} of {total} built.**", ""]
    for category in data["categories"]:
        built = sum(1 for a in category["apps"] if a["built"])
        lines += [
            f"### {category['icon']} [{category['title']}]({category['slug']}) "
            f"— {built}/{len(category['apps'])}",
            "",
            category["blurb"],
            "",
        ]
        for app in category["apps"]:
            if app["built"]:
                lines.append(f"- **[{app['title']}]({category['slug']}/{app['slug']})** — {app['does']}")
            else:
                lines.append(f"- {app['title']} *(planned)* — {app['does']}")
        lines.append("")
    return "\n".join(lines)


def category_readme(category: dict) -> str:
    built = sum(1 for a in category["apps"] if a["built"])
    lines = [
        f"# {category['icon']} {category['title']}",
        "",
        category["blurb"],
        "",
        f"**{built} of {len(category['apps'])} built.**",
        "",
        "| App | What it does | Why it needs Jev |",
        "|---|---|---|",
    ]
    for app in category["apps"]:
        mark = "✅" if app["built"] else "⬜"
        name = f"[{app['title']}]({app['slug']})" if app["built"] else app["title"]
        lines.append(f"| {mark} {name} | {app['does']} | {app['why']} |")
    lines += [
        "",
        "## Running any of these",
        "",
        "```bash",
        f"cd apps/{category['slug']}/<app>",
        "cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY",
        "uv run streamlit run app.py       # or: uv run --with httpx python <name>.py",
        "```",
        "",
        "Every app is standalone — copy the folder out and it still works. Each ships",
        "sample data, so it runs before you supply anything of your own.",
        "",
        "## Contributing one",
        "",
        "Unbuilt rows above are open. Read [`../../docs/APP_SPEC.md`](../../docs/APP_SPEC.md)",
        "for the contract and [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) for the",
        "editorial bar, then open a PR with one app.",
        "",
        "<sub>This file is generated by `scripts/build_catalog.py`. Edit `catalog.json` instead.</sub>",
        "",
    ]
    return "\n".join(lines)


def splice(path: Path, body: str) -> tuple[bool, str]:
    """Replace the region between the markers. Returns (changed, new_text)."""
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{path.relative_to(ROOT)} is missing {START} / {END} markers")
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    new = f"{head}{START}\n{body}{END}{tail}"
    return new != text, new


def main() -> int:
    check_only = "--check" in sys.argv
    argparse.ArgumentParser().parse_known_args()

    data = load()
    stale: list[str] = []

    for path, body in ((ROOT / "README.md", root_table(data)),
                       (APPS / "README.md", apps_table(data))):
        changed, new = splice(path, body)
        if changed:
            stale.append(str(path.relative_to(ROOT)))
            if not check_only:
                path.write_text(new, encoding="utf-8")

    for category in data["categories"]:
        path = APPS / category["slug"] / "README.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = category_readme(category)
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            stale.append(str(path.relative_to(ROOT)))
            if not check_only:
                path.write_text(body, encoding="utf-8")

    done, total = summary(data)
    if check_only and stale:
        print("stale indexes (run: python scripts/build_catalog.py):", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        return 1

    print(f"catalog: {done}/{total} built")
    for category in data["categories"]:
        built = sum(1 for a in category["apps"] if a["built"])
        print(f"  {category['slug']:<20} {built:>3}/{len(category['apps']):<3}")
    print(f"\n{'checked' if check_only else 'wrote'} {len(stale) or 'no'} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
