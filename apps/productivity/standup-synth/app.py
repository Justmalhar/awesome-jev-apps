"""Standup Synth -- assemble your actual week from commits, PRs, and tickets.

The division of labour is the point. Jev SELECTS: which of 30 activity items a
teammate would actually want to hear about. Python ASSEMBLES: the sections, the
ordering, the counts, and the markdown. Nothing on screen is generated text --
every bullet is the commit message or ticket title you already wrote.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_activity.json"

# Whether work is finished is a FACT from the tracker, never a judgment.
DONE_STATES = {"merged", "closed", "done", "released", "committed"}

THEMES = {
    "feature": "New capability that did not exist before.",
    "fix": "Repairs behaviour that was wrong or broken.",
    "refactor": "Restructures existing code without changing what it does.",
    "infra": "Build, deploy, CI, monitoring, dependencies, or environments.",
    "docs": "Documentation, runbooks, comments, or examples.",
    "review": "Reviewing, unblocking, or supporting someone else's work.",
    "other": "None of these describe it.",
}


def load_activity(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def is_done(item: dict) -> bool:
    """Read from the tracker's own state field. The model is never asked."""
    return str(item.get("state", "")).lower() in DONE_STATES


def build_questions(items: list[dict]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(items)):
        entry = {
            "kind": f"`activity[{index}].kind`",
            "title": f"`activity[{index}].title`",
            "body": f"`activity[{index}].body`",
            "repo": f"`activity[{index}].repo`",
        }
        questions[f"notable_{index}"] = noul(
            {"task": "Would a teammate who was away this week want to be told about this item?",
             "item": entry},
            true="It changes something they might rely on, or answers a question they would ask",
            false="Routine housekeeping nobody else needs to hear about",
        )
        questions[f"visible_{index}"] = noul(
            {"task": "Does this change something a user or another team can observe?", "item": entry},
            true="Behaviour, an interface, an API, or an operational characteristic changes outside this codebase",
            false="Internal only: nothing outside the team could tell the difference",
        )
        questions[f"blocked_{index}"] = noul(
            {"task": "Does this item say the author is waiting on somebody or something else?",
             "item": entry},
            true="It names a dependency, a pending review, an approval, or an unanswered question",
            false="The author can carry on without anyone else",
        )
        questions[f"risky_{index}"] = noul(
            {"task": "Does this item touch an area where a mistake is expensive?", "item": entry},
            true="Authentication, payments, data migration, deletion, or production infrastructure",
            false="An area where a mistake is caught in review or costs little to undo",
        )
        questions[f"theme_{index}"] = choice(
            {"task": "What kind of work is this item?", "item": entry}, THEMES
        )
    return questions


def section(item: dict, judgment: dict, *, notable_gate: float, noul_gate: float) -> str:
    """Pure policy. 'Done' comes from the tracker; 'worth saying' comes from Jev."""
    if judgment["blocked"] >= noul_gate and not is_done(item):
        return "blocked"
    if judgment["notable"] < notable_gate:
        return "dropped"
    return "shipped" if is_done(item) else "in_progress"


def render_standup(grouped: dict[str, list[tuple[dict, dict]]], noul_gate: float) -> str:
    """Assemble the update in Python, from the user's own titles. No generation."""
    headings = [("shipped", "Shipped"), ("in_progress", "In progress"), ("blocked", "Blocked")]
    lines: list[str] = []
    for key, heading in headings:
        rows = grouped.get(key, [])
        if not rows:
            continue
        lines.append(f"**{heading}**")
        for item, judgment in rows:
            marks = []
            if judgment["visible"] >= noul_gate:
                marks.append("user-visible")
            if judgment["risky"] >= noul_gate:
                marks.append("needs care")
            suffix = f" _({', '.join(marks)})_" if marks else ""
            reference = f"`{item['id']}` " if item.get("id") else ""
            lines.append(f"- {reference}{item['title']}{suffix}")
        lines.append("")
    if not lines:
        return "_Nothing this week cleared the bar. Lower the gate, or it was a quiet week._"
    dropped = len(grouped.get("dropped", []))
    lines.append(f"_{dropped} routine item(s) left out._" if dropped else "")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    st.set_page_config(page_title="Standup Synth", page_icon="🗒️", layout="wide")
    st.title("🗒️ Standup Synth")
    st.caption("Your actual week, assembled from commits, PRs, and tickets. Jev selects; Python writes.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Thresholds")
        st.caption("Re-assembling makes **no** API calls.")
        notable_gate = st.slider("P(notable) to make the update", 0.0, 1.0, 0.55, 0.05)
        noul_gate = st.slider("P(yes) for a flag to count", 0.0, 1.0, 0.6, 0.05)
        activity_path = st.text_input("Activity JSON (optional)", value="")

    items = load_activity(Path(activity_path).expanduser() if activity_path.strip() else None)
    if not items:
        st.warning("No activity. Provide an activity JSON, or restore sample_activity.json.")
        return
    if not activity_path.strip():
        kinds = sorted({item.get("kind", "?") for item in items})
        st.caption(f"Using the bundled sample week: {len(items)} items ({', '.join(kinds)}).")

    if st.button(f"Judge {len(items)} items", type="primary"):
        questions = build_questions(items)
        try:
            with st.spinner("Reading your week..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"activity": items}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["week"] = {
            "items": items,
            "judgments": [
                {
                    "notable": answers.noul(f"notable_{i}"),
                    "visible": answers.noul(f"visible_{i}"),
                    "blocked": answers.noul(f"blocked_{i}"),
                    "risky": answers.noul(f"risky_{i}"),
                    "theme": answers.choice(f"theme_{i}"),
                    "theme_confidence": answers.confidence(f"theme_{i}"),
                }
                for i in range(len(items))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    state = st.session_state.get("week")
    if not state:
        st.info("Hit the button to judge the week.")
        return

    # --- policy only below this line ---
    grouped: dict[str, list[tuple[dict, dict]]] = {}
    for item, judgment in zip(state["items"], state["judgments"]):
        key = section(item, judgment, notable_gate=notable_gate, noul_gate=noul_gate)
        grouped.setdefault(key, []).append((item, judgment))
    for rows in grouped.values():
        rows.sort(key=lambda pair: -pair[1]["notable"])

    columns = st.columns(4)
    columns[0].metric("Shipped", len(grouped.get("shipped", [])))
    columns[1].metric("In progress", len(grouped.get("in_progress", [])))
    columns[2].metric("Blocked", len(grouped.get("blocked", [])))
    columns[3].metric("Cost", f"${state['cost']:.6f}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Your update")
        update = render_standup(grouped, noul_gate)
        st.markdown(update)
        st.code(update, language="markdown")
    with right:
        st.subheader("By theme")
        themes: dict[str, int] = {}
        for _, judgment in zip(state["items"], state["judgments"]):
            themes[judgment["theme"]] = themes.get(judgment["theme"], 0) + 1
        for theme, total in sorted(themes.items(), key=lambda pair: -pair[1]):
            st.caption(f"**{theme}** — {total} item(s)")
        with st.expander(f"Left out ({len(grouped.get('dropped', []))})"):
            for item, judgment in grouped.get("dropped", []):
                st.caption(f"{item.get('id', '')} {item['title']} · notable P={judgment['notable']:.2f}")

    st.divider()
    st.caption(
        f"{len(state['items'])} items × 5 judgments = {state['questions']} questions in 1 request · "
        f"{state['elapsed']:.1f}s · {state['tokens']:,} input tokens · ${state['cost']:.6f}. "
        "Both gates re-assemble the update for $0.00."
    )


if __name__ == "__main__":
    main()
