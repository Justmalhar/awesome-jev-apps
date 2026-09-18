"""log-pattern-miner -- turn a wall of log lines into named failure modes.

The map-reduce here is the whole app. MAP: normalise every line into a template
(regex, in Python), collapse identical templates, and ask Jev one question set
per distinct template. REDUCE: Python sums the line counts per failure mode.

That only works because the map step is cheap. 200,000 log lines collapse to a
few hundred templates, and a few hundred templates is a handful of requests at
$0.042 per million input tokens -- cents, not a budget conversation. At frontier
prices you would sample, and sampling is how the one template that matters gets
missed.

The model is never shown a count and never asked for one: `jev-1.13` cannot
count. Every number on this page is Python's.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_logs.txt"
TEMPLATE_CHARS = 300
EXAMPLES_PER_TEMPLATE = 2

# Variable parts, replaced before grouping. Order matters: longest first.
PLACEHOLDERS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hash>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<email>"),
    (re.compile(r"(?<![\w.])0x[0-9a-f]+", re.I), "<addr>"),
    (re.compile(r"/[\w./-]{4,}"), "<path>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|MB|GB|KB)\b"), "<measure>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)

FAILURE_MODES = {
    "upstream_unavailable": "A service this process depends on refused, reset, or never answered the connection.",
    "timeout": "Something took longer than a deadline allowed and was abandoned part-way.",
    "auth_rejected": "A credential, token, signature, or permission check was refused.",
    "resource_exhausted": "Memory, disk, file handles, connections, or a pool ran out.",
    "bad_input": "Data arriving from outside did not have the shape or values the code required.",
    "programming_error": "The process hit a defect in its own code: a null dereference, a bad cast, an impossible branch.",
    "config_error": "A setting, secret, or environment variable is missing, malformed, or points somewhere wrong.",
    "deploy_lifecycle": "Routine start-up, shutdown, migration, or rollout activity rather than a failure.",
    "other": "A real failure mode, but none of the above describes it.",
    "unclear": "The line does not say enough to place it.",
}


@dataclass
class Template:
    """One normalised line shape, plus everything Python counted about it."""

    pattern: str
    count: int = 0
    examples: list[str] = field(default_factory=list)
    levels: Counter = field(default_factory=Counter)

    @property
    def top_level(self) -> str:
        return self.levels.most_common(1)[0][0] if self.levels else "UNKNOWN"


def normalise(line: str) -> str:
    """Replace the variable parts. Pure regex: exact, free, and reproducible."""
    text = line.strip()
    for pattern, placeholder in PLACEHOLDERS:
        text = pattern.sub(placeholder, text)
    return " ".join(text.split())[:TEMPLATE_CHARS]


def level_of(line: str) -> str:
    match = re.search(r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", line)
    return match.group(1) if match else "UNKNOWN"


def cluster(lines: list[str]) -> list[Template]:
    """The MAP step's cheap half. 200k lines in, a few hundred templates out."""
    templates: dict[str, Template] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        pattern = normalise(stripped)
        if not pattern:
            continue
        template = templates.setdefault(pattern, Template(pattern=pattern))
        template.count += 1
        template.levels[level_of(stripped)] += 1
        if len(template.examples) < EXAMPLES_PER_TEMPLATE:
            template.examples.append(stripped[:TEMPLATE_CHARS])
    return sorted(templates.values(), key=lambda t: -t.count)


def build_questions(templates: list[Template]) -> dict:
    """Three judgments per distinct template -- not per line. One request."""
    questions: dict[str, dict] = {}
    for index in range(len(templates)):
        reference = {
            "line_shape": f"`templates[{index}].pattern`",
            "examples": f"`templates[{index}].examples`",
        }
        questions[f"mode_{index}"] = choice(
            {"task": "What kind of failure does this log line report?", "log": reference},
            FAILURE_MODES,
        )
        # Independent of the mode: plenty of upstream_unavailable lines are
        # retried successfully and nobody should ever see them, while a single
        # bad_input line can be the thing that needs paging. Separate nouls so
        # each can be thresholded on its own.
        questions[f"actionable_{index}"] = noul(
            {"task": "Could an engineer do something about this without more information than the line gives?",
             "log": reference},
            true="It names what failed specifically enough to start work",
            false="It is routine chatter, or so vague that the next step would be to go looking for a better log line",
        )
        questions[f"data_at_risk_{index}"] = noul(
            {"task": "Does this line suggest that data was lost, corrupted, or left in an inconsistent state?",
             "log": reference},
            true="Work was dropped mid-flight, a write half-applied, or a record could not be reconciled",
            false="The operation failed cleanly, or nothing was being persisted",
        )
    return questions


def reduce_modes(judged: list[dict]) -> list[dict]:
    """The REDUCE step. Python sums the line counts per mode -- never the model."""
    totals: dict[str, dict] = {}
    for item in judged:
        bucket = totals.setdefault(
            item["mode"], {"mode": item["mode"], "lines": 0, "templates": 0, "actionable_lines": 0}
        )
        bucket["lines"] += item["count"]
        bucket["templates"] += 1
        if item["actionable"] >= 0.6:
            bucket["actionable_lines"] += item["count"]
    return sorted(totals.values(), key=lambda row: -row["lines"])


def triage(item: dict, *, actionable_gate: float, data_gate: float) -> str:
    """Pure policy over stored floats."""
    if item["data_at_risk"] >= data_gate:
        return "page_someone"
    if item["mode"] in {"unclear", "other"}:
        return "improve_the_log_line"
    if item["mode"] == "deploy_lifecycle":
        return "ignore"
    return "investigate" if item["actionable"] >= actionable_gate else "ignore"


def main() -> None:
    st.set_page_config(page_title="Log Pattern Miner", page_icon="🪵", layout="wide")
    st.title("🪵 Log Pattern Miner")
    st.caption("Many log lines in, named failure modes out. Regex maps, Jev judges, Python reduces.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Policy (no inference)")
        actionable_gate = st.slider("P(actionable) to investigate", 0.0, 1.0, 0.6, 0.05)
        data_gate = st.slider("P(data at risk) to page someone", 0.0, 1.0, 0.6, 0.05)
        max_templates = st.slider("Templates to judge (most frequent first)", 10, 500, 200, 10)
        batch_size = st.slider("Templates per request", 5, 60, 25, 5)

    uploaded = st.file_uploader("Log file (one line per entry)", type=["txt", "log"])
    raw = uploaded.read().decode("utf-8", "replace") if uploaded else SAMPLE.read_text(encoding="utf-8")
    if not uploaded:
        st.caption("Using the bundled sample log.")

    lines = raw.splitlines()
    templates = cluster(lines)[:max_templates]
    columns = st.columns(3)
    columns[0].metric("Log lines", f"{len([l for l in lines if l.strip()]):,}")
    columns[1].metric("Distinct templates", len(templates))
    columns[2].metric(
        "Collapse ratio",
        f"{len([l for l in lines if l.strip()]) / max(len(templates), 1):.0f}×",
    )
    if not templates:
        st.warning("Nothing to cluster.")
        return
    if not st.button(f"Name the failure modes in {len(templates)} templates", type="primary"):
        return

    groups = [templates[i:i + batch_size] for i in range(0, len(templates), batch_size)]
    judged: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        with st.spinner(f"Mapping in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    state = {"templates": [{"pattern": t.pattern, "examples": t.examples} for t in group]}
                    questions = build_questions(group)
                    answers = client.ask(state, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, template in enumerate(group):
                        item = {
                            "pattern": template.pattern,
                            "count": template.count,
                            "level": template.top_level,
                            "mode": answers.choice(f"mode_{index}"),
                            "confidence": round(answers.confidence(f"mode_{index}"), 3),
                            "actionable": round(answers.noul(f"actionable_{index}"), 3),
                            "data_at_risk": round(answers.noul(f"data_at_risk_{index}"), 3),
                        }
                        item["triage"] = triage(item, actionable_gate=actionable_gate, data_gate=data_gate)
                        judged.append(item)
    except JevError as exc:
        st.error(str(exc))
        return

    st.subheader("Failure modes by volume")
    st.caption("Line totals summed in Python from the cluster counts.")
    rolled = reduce_modes(judged)
    st.bar_chart({row["mode"]: row["lines"] for row in rolled})
    st.dataframe(rolled, width="stretch")

    for name, title, blurb in (
        ("page_someone", "🔴 Data at risk", "Judged to have lost or corrupted something."),
        ("investigate", "🟠 Investigate", "Actionable failures, ordered by how much of the log they are."),
        ("improve_the_log_line", "🟡 Unnameable", "The line itself does not say enough. Fix the logging."),
        ("ignore", "⚪ Noise", "Routine, or not actionable as written."),
    ):
        items = [j for j in judged if j["triage"] == name]
        if not items:
            continue
        st.subheader(f"{title} ({len(items)} template(s))")
        st.caption(blurb)
        st.dataframe(
            sorted(items, key=lambda i: -i["count"]),
            width="stretch",
            column_order=["count", "level", "mode", "confidence", "actionable", "data_at_risk", "pattern"],
        )

    st.divider()
    st.caption(
        f"{asked} question(s) over {len(judged)} template(s) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s measured — "
        f"covering {sum(j['count'] for j in judged):,} log lines."
    )


if __name__ == "__main__":
    main()
