"""dependency-audit -- does this dependency's changelog affect YOUR code?

Dependabot tells you a version moved. It cannot tell you whether the five
behaviour changes in that release touch anything you call, because it has never
read your code. So the changelog gets skimmed, or skipped, and the entry that
mattered -- "the default for `retries` is now 0" -- is found in production.

This app reads both sides: the changelog entries (split by Python, by heading)
and YOUR call sites (extracted by Python, with `ast`). The only question put to
Jev is the one neither parser can answer: does this entry affect how we use it?

    uv run streamlit run app.py
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE_CHANGELOG = HERE / "sample_changelog.md"
SAMPLE_USAGE = HERE / "sample_usage.py"
ENTRY_CHARS = 1200

URGENCY_LEVELS = [
    "Nothing we call is affected; this is a routine version bump we can take whenever",
    "Something we call changed, but our calls keep behaving exactly as they do today",
    "Our code keeps running and quietly does something different, so we would not notice until the output is wrong",
    "Our code stops working on upgrade, or keeps a vulnerability open until we act",
]


@dataclass
class Entry:
    heading: str
    text: str


@dataclass
class UsageSite:
    """One place we touch the dependency. Found by ast, never by the model."""

    file: str
    line: int
    symbol: str
    source: str


@dataclass
class Usage:
    package: str
    imported: list[str] = field(default_factory=list)
    sites: list[UsageSite] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "package": self.package,
            "imported_names": self.imported,
            "call_sites": [
                {"where": f"{s.file}:{s.line}", "symbol": s.symbol, "code": s.source}
                for s in self.sites
            ],
        }


def split_changelog(markdown: str) -> list[Entry]:
    """One entry per bullet under a heading. Structure is Python's job."""
    entries: list[Entry] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join(" ".join(buffer).split())[:ENTRY_CHARS]
        if text:
            entries.append(Entry(heading=heading, text=text))

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            flush()
            buffer = []
            heading = line.lstrip("# ").strip()
        elif re.match(r"^\s*[-*]\s+", line):
            flush()
            buffer = [re.sub(r"^\s*[-*]\s+", "", line)]
        elif line.strip():
            buffer.append(line.strip())
    flush()
    return entries


def extract_usage(source: str, package: str, filename: str = "our_code.py") -> Usage:
    """Every import of, and reference to, the package -- exact, from the parser."""
    usage = Usage(package=package)
    tree = ast.parse(source)
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == package:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                    usage.imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == package:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                    usage.imported.append(f"{node.module}.{alias.name}")

    # Objects built from the package count as usage too: `client = Client()`
    # then `client.send(...)`. One pass in source order catches the common case.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        root = node.value.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in aliases:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = aliases[root.id]

    lines = source.splitlines()
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        root = node.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in aliases:
            continue
        key = (node.lineno, ast.unparse(node.func))
        if key in seen:
            continue
        seen.add(key)
        # Include the surrounding line so the argument values are visible.
        snippet = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ast.unparse(node)
        usage.sites.append(
            UsageSite(file=filename, line=node.lineno, symbol=aliases[root.id], source=snippet[:300])
        )
    usage.sites.sort(key=lambda s: s.line)
    return usage


def build_questions(entries: list[Entry]) -> dict:
    """Three judgments per changelog entry, against our usage, in one request."""
    questions: dict[str, dict] = {}
    for index in range(len(entries)):
        reference = {
            "section": f"`entries[{index}].heading`",
            "entry": f"`entries[{index}].text`",
            "our_imports": "`usage.imported_names`",
            "our_call_sites": "`usage.call_sites`",
        }
        questions[f"relevant_{index}"] = noul(
            {"task": "Does this changelog entry describe something the call sites shown actually use?",
             "context": reference},
            true="A function, argument, or behaviour named in the entry appears in our call sites",
            false="It concerns a part of the library our code never touches",
        )
        # Independent of relevance: an entry can be relevant and require nothing
        # (a bug fix we benefit from), or be irrelevant today and still need a
        # decision (a deprecation we will hit next release).
        questions[f"action_needed_{index}"] = noul(
            {"task": "Would somebody have to edit our code, configuration, or data for this entry?",
             "context": reference},
            true="Upgrading without an edit leaves our code broken, insecure, or behaving differently from before",
            false="Upgrading is a drop-in for us; the entry needs no work on our side",
        )
        questions[f"urgency_{index}"] = score(
            {"task": "What happens to our code if we take this upgrade and change nothing?",
             "context": reference},
            URGENCY_LEVELS,
        )
    return questions


def verdict(relevant: float, action: float, urgency: float,
            *, relevance_gate: float, action_gate: float, urgency_gate: float) -> str:
    """Pure policy over the three stored floats."""
    if urgency >= urgency_gate:
        return "blocks_upgrade"
    if relevant >= relevance_gate and action >= action_gate:
        return "needs_work"
    if relevant >= relevance_gate:
        return "read_before_upgrading"
    return "ignore"


def main() -> None:
    st.set_page_config(page_title="Dependency Audit", page_icon="📦", layout="wide")
    st.title("📦 Dependency Audit")
    st.caption("Does this release's changelog affect the way *we* use the package?")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        package = st.text_input("Package", value="shipyard")
        st.subheader("Policy (no inference)")
        relevance_gate = st.slider("P(relevant to our code)", 0.0, 1.0, 0.6, 0.05)
        action_gate = st.slider("P(we must edit something)", 0.0, 1.0, 0.5, 0.05)
        urgency_gate = st.slider("Urgency that blocks the upgrade", 0.0, 3.0, 2.0, 0.1)
        batch_size = st.slider("Entries per request", 5, 60, 25, 5)

    left, right = st.columns(2)
    with left:
        changelog_file = st.file_uploader("Dependency changelog (markdown)", type=["md", "txt"])
        changelog = (changelog_file.read().decode("utf-8") if changelog_file
                     else SAMPLE_CHANGELOG.read_text(encoding="utf-8"))
    with right:
        usage_file = st.file_uploader("Your module that uses it (.py)", type=["py"])
        our_code = (usage_file.read().decode("utf-8") if usage_file
                    else SAMPLE_USAGE.read_text(encoding="utf-8"))
        filename = usage_file.name if usage_file else SAMPLE_USAGE.name

    entries = split_changelog(changelog)
    try:
        usage = extract_usage(our_code, package, filename=filename)
    except SyntaxError as exc:
        st.error(f"Could not parse your module: {exc}")
        return

    columns = st.columns(3)
    columns[0].metric("Changelog entries", len(entries))
    columns[1].metric("Imported names", len(usage.imported))
    columns[2].metric("Call sites found", len(usage.sites))
    if not usage.sites:
        st.warning(f"No calls into `{package}` found. Set the package name in the sidebar.")
    with st.expander("What Python extracted from your code"):
        st.json(usage.summary())
    if not entries:
        st.warning("No changelog entries parsed.")
        return
    if not st.button(f"Audit {len(entries)} entries against our usage", type="primary"):
        return

    groups = [entries[i:i + batch_size] for i in range(0, len(entries), batch_size)]
    judged: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        with st.spinner(f"Auditing in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    state = {
                        "usage": usage.summary(),
                        "entries": [{"heading": e.heading, "text": e.text} for e in group],
                    }
                    questions = build_questions(group)
                    answers = client.ask(state, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, entry in enumerate(group):
                        relevant = answers.noul(f"relevant_{index}")
                        action = answers.noul(f"action_needed_{index}")
                        urgency = answers.score(f"urgency_{index}")
                        judged.append(
                            {
                                "section": entry.heading,
                                "entry": entry.text,
                                "relevant": round(relevant, 3),
                                "action_needed": round(action, 3),
                                "urgency": round(urgency, 3),
                                "confidence": round(answers.confidence(f"urgency_{index}"), 3),
                                "verdict": verdict(
                                    relevant, action, urgency,
                                    relevance_gate=relevance_gate,
                                    action_gate=action_gate,
                                    urgency_gate=urgency_gate,
                                ),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    buckets = defaultdict(list)
    for item in judged:
        buckets[item["verdict"]].append(item)

    columns = st.columns(4)
    columns[0].metric("Blocks the upgrade", len(buckets["blocks_upgrade"]))
    columns[1].metric("Needs work", len(buckets["needs_work"]))
    columns[2].metric("Read first", len(buckets["read_before_upgrading"]))
    columns[3].metric("Ignore", len(buckets["ignore"]))

    for name, title, blurb in (
        ("blocks_upgrade", "🔴 Blocks the upgrade", "Take this and something of ours breaks or stays vulnerable."),
        ("needs_work", "🟠 Needs work", "Relevant to our call sites and someone has to change something."),
        ("read_before_upgrading", "🟡 Read before upgrading", "Touches code we use, but appears to need nothing from us."),
        ("ignore", "⚪ Not our problem", "Concerns parts of the package we never call."),
    ):
        items = buckets.get(name)
        if not items:
            continue
        st.subheader(f"{title} ({len(items)})")
        st.caption(blurb)
        for item in sorted(items, key=lambda i: -i["urgency"]):
            with st.container(border=True):
                st.markdown(f"**{item['section']}** · urgency {item['urgency']:.2f}/3")
                st.write(item["entry"])
                st.caption(
                    f"relevant P={item['relevant']:.2f} · "
                    f"needs an edit P={item['action_needed']:.2f} · "
                    f"confidence {item['confidence']:.2f}"
                )

    st.divider()
    st.caption(
        f"{asked} question(s) over {len(judged)} entr(ies) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s measured."
    )


if __name__ == "__main__":
    main()
