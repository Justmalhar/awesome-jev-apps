#!/usr/bin/env python3
"""changelog-classifier -- sort merged PRs so code can assemble release notes.

    uv run --with httpx python changelog.py --prs sample_prs.json
    uv run --with httpx python changelog.py --prs sample_prs.json --json

The model never WRITES a line of the changelog. It cannot: it selects. Every
line printed is text a human already wrote -- the PR title, the first sentence
of the body, or the migration note -- and Jev's job is to pick which of those
existing strings is the entry, which section it belongs in, and whether it is
user-visible or breaking.

That constraint is a feature. Generated release notes hallucinate behaviour
that was never shipped; a selected line is, by construction, something someone
on the team wrote about a change they actually made.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, choice  # noqa: E402

SAMPLE = HERE / "sample_prs.json"
BODY_CHARS = 700

SECTIONS = {
    "feature": "Adds a capability that did not exist before, which someone could now use.",
    "fix": "Corrects behaviour that was wrong: a bug report, a regression, an incorrect result.",
    "performance": "Same behaviour, measurably less time, memory, or money to get it.",
    "security": "Closes a vulnerability, hardens a credential path, or patches a known advisory.",
    "internal": "Refactors, tests, CI, tooling, or developer ergonomics with no effect on anyone outside the repo.",
    "dependency": "Moves a third-party package or runtime version, with no first-party behaviour change of its own.",
    "docs": "Changes documentation, comments, or examples only.",
    "unclear": "The title and body do not say enough to place it.",
}

# Selection, not generation: each option names a string extracted by Python.
ENTRY_SOURCES = {
    "title": "The pull request title reads as a complete, self-contained release-note line.",
    "summary": "The first sentence of the body describes the change better than the title does.",
    "breaking_note": "The migration or breaking-change note is the line a reader most needs to see.",
    "none": "None of the available strings would make a usable release-note line on its own.",
}

SECTION_ORDER = ("security", "breaking", "feature", "fix", "performance", "dependency", "docs", "internal")


def first_sentence(text: str) -> str:
    """Python slices the string. Asking a model to 'extract the first sentence'
    is asking it to generate, and it will paraphrase."""
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return ""
    match = re.search(r"(?<=[.!?])\s", cleaned)
    return (cleaned[: match.start()] if match else cleaned)[:240]


def breaking_note(text: str) -> str:
    """Pull the migration paragraph out by marker, exactly."""
    for line in str(text).splitlines():
        stripped = line.strip().lstrip("*-# ").strip()
        lowered = stripped.lower()
        if lowered.startswith(("breaking", "migration", "migrate:", "upgrade note")):
            return stripped[:240]
    return ""


def prepare(prs: list[dict]) -> list[dict]:
    """Attach the candidate strings. Every one of them is verbatim human text."""
    prepared = []
    for pr in prs:
        body = str(pr.get("body", ""))[:BODY_CHARS]
        prepared.append(
            {
                "number": pr.get("number"),
                "title": str(pr.get("title", "")).strip(),
                "body": body,
                "labels": list(pr.get("labels", [])),
                "candidates": {
                    "title": str(pr.get("title", "")).strip(),
                    "summary": first_sentence(body),
                    "breaking_note": breaking_note(body),
                },
            }
        )
    return prepared


def build_questions(prs: list[dict]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(prs)):
        reference = {
            "title": f"`prs[{index}].title`",
            "body": f"`prs[{index}].body`",
            "labels": f"`prs[{index}].labels`",
        }
        questions[f"section_{index}"] = choice(
            {"task": "Which release-notes section does this merged pull request belong in?",
             "pull_request": reference},
            SECTIONS,
        )
        # user-visible and breaking are NOT sections. Both can be true of a
        # `feature`, a `fix`, or a `dependency`, so they are separate nouls with
        # separate thresholds rather than extra options on the choice.
        questions[f"user_visible_{index}"] = noul(
            {"task": "Would somebody who only uses the product, and never reads its source, notice this change?",
             "pull_request": reference},
            true="It changes what the product does, shows, accepts, or returns",
            false="Only people working inside the repository would ever observe it",
        )
        questions[f"breaking_{index}"] = noul(
            {"task": "Does upgrading to this require an existing user to change their own code, config, or data?",
             "pull_request": reference},
            true="Something that worked before now needs action from the user to keep working",
            false="Upgrading is a drop-in; existing usage continues to behave",
        )
        questions[f"entry_{index}"] = choice(
            {"task": "Which of these existing strings should be used verbatim as this change's release-note line?",
             "pull_request": reference,
             "available_strings": f"`prs[{index}].candidates`"},
            ENTRY_SOURCES,
        )
    return questions


def assemble(entries: list[dict], *, user_gate: float, breaking_gate: float) -> dict[str, list[dict]]:
    """Python assembles the notes. Routing policy, no inference."""
    sections: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if entry["breaking"] >= breaking_gate:
            sections["breaking"].append(entry)
            continue
        if entry["section"] in {"internal", "docs"} and entry["user_visible"] >= user_gate:
            # The model placed it internally but it is visible: surface it as a
            # fix rather than dropping it. This is policy, and it lives here.
            sections["fix"].append(entry)
            continue
        if entry["section"] == "unclear":
            sections["unclear"].append(entry)
            continue
        sections[entry["section"]].append(entry)
    return sections


def render(sections: dict[str, list[dict]]) -> str:
    """Pure string assembly from selected, verbatim lines."""
    out: list[str] = ["# Release notes", ""]
    for name in SECTION_ORDER:
        items = sections.get(name)
        if not items:
            continue
        out.append(f"## {name.title()}")
        for item in sorted(items, key=lambda i: i["number"] or 0):
            out.append(f"- {item['line']} (#{item['number']})")
        out.append("")
    if sections.get("unclear"):
        out.append("## Needs a human")
        for item in sections["unclear"]:
            out.append(f"- #{item['number']} {item['title']}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sort merged PRs into release-note sections.")
    parser.add_argument("--prs", type=Path, default=SAMPLE, help="JSON: [{number, title, body, labels}]")
    parser.add_argument("--user-gate", type=float, default=0.6,
                        help="P(user-visible) that rescues an internally-labelled change")
    parser.add_argument("--breaking-gate", type=float, default=0.6,
                        help="P(requires user action) that promotes a change to Breaking")
    parser.add_argument("--batch", type=int, default=25, help="PRs per request")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if not args.prs.is_file():
        print(f"no such file: {args.prs}", file=sys.stderr)
        return 2
    prs = prepare(json.loads(args.prs.read_text(encoding="utf-8")))
    if not prs:
        print("no pull requests", file=sys.stderr)
        return 2

    groups = [prs[i:i + args.batch] for i in range(0, len(prs), args.batch)]
    entries: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        provider = load_provider(start=HERE)
        print(f"classifying {len(prs)} PR(s) in {len(groups)} request(s) via {provider.name}...",
              file=sys.stderr)
        with JevClient(provider=provider) as client:
            for group in groups:
                questions = build_questions(group)
                answers = client.ask({"prs": group}, questions)
                cost += answers.cost_usd
                tokens += answers.input_tokens
                elapsed += answers.elapsed_s
                asked += len(questions)
                for index, pr in enumerate(group):
                    source = answers.choice(f"entry_{index}")
                    line = pr["candidates"].get(source) or pr["title"]
                    entries.append(
                        {
                            "number": pr["number"],
                            "title": pr["title"],
                            "line": line,
                            "entry_source": source,
                            "section": answers.choice(f"section_{index}"),
                            "section_confidence": round(answers.confidence(f"section_{index}"), 3),
                            "user_visible": round(answers.noul(f"user_visible_{index}"), 3),
                            "breaking": round(answers.noul(f"breaking_{index}"), 3),
                        }
                    )
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    sections = assemble(entries, user_gate=args.user_gate, breaking_gate=args.breaking_gate)

    if args.as_json:
        print(json.dumps({"entries": entries, "cost_usd": round(cost, 8)}, indent=2))
    else:
        print(render(sections))

    print(
        f"{asked} question(s) over {len(entries)} PR(s) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s",
        file=sys.stderr,
    )
    return 1 if sections.get("unclear") else 0


if __name__ == "__main__":
    raise SystemExit(main())
