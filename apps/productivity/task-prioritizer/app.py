"""Task Prioritizer -- re-rank a task list against this week's stated goal.

The point of this app is the split between two kinds of judgment:

  * Properties of the task itself (effort, does it unblock someone, did you
    promise it to an outsider). These never change when your goal changes, so
    they are asked ONCE and cached for the life of the task.
  * Properties of the task AGAINST a goal. These are re-asked only for the goal
    you typed, and only for tasks whose stable judgments are already in hand.

Change the goal and you pay for two questions per task, not seven. Move a
weight and you pay for nothing at all.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_tasks.json"

DEFAULT_GOAL = (
    "This week the only thing that matters is getting the self-serve signup flow "
    "into the hands of the three design partners. Anything that does not put a "
    "working signup in front of them can wait."
)

EFFORT_LEVELS = [
    "A few minutes: a reply, a config flag, or approving something already prepared",
    "Half a day or less: contained work in one place with no coordination needed",
    "Several days: touches more than one component or needs someone else's time",
    "Weeks: needs design, review from other teams, or a migration to land first",
]

SERVES_LEVELS = [
    "Doing this would not move the stated goal at all",
    "Helps indirectly: cleans up something the goal touches, but is not on its path",
    "On the critical path: the goal is worse or later without it",
    "The goal cannot be reached until this is done",
]

# Judgments that are properties of the task alone -- asked once, cached forever.
STABLE_KEYS = ("effort", "unblocks", "promised", "decays")
# Judgments that only mean anything relative to a goal -- re-asked per goal.
GOAL_KEYS = ("serves", "required")


def load_tasks(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def missing(tasks: list[dict], stable: dict, goal_cache: dict, goal_key: str) -> tuple[list[int], list[int]]:
    """Which tasks still need which half of the judgment. Pure; drives the cost."""
    need_stable = [i for i, task in enumerate(tasks) if task["id"] not in stable]
    need_goal = [i for i, task in enumerate(tasks) if (goal_key, task["id"]) not in goal_cache]
    return need_stable, need_goal


def build_questions(tasks: list[dict], goal: str, need_stable: list[int], need_goal: list[int]) -> dict:
    """Only the judgments that are actually missing. Both halves ride one request."""
    questions: dict[str, dict] = {}
    for index in need_stable:
        task = {"title": f"`tasks[{index}].title`", "notes": f"`tasks[{index}].notes`",
                "project": f"`tasks[{index}].project`"}
        questions[f"effort_{index}"] = score(
            {"task": "How much work is this item, as described?", "item": task}, EFFORT_LEVELS
        )
        questions[f"unblocks_{index}"] = noul(
            {"task": "Is somebody else waiting on this item before they can continue?", "item": task},
            true="Another person or team is held up until this is done",
            false="Nobody else's work depends on it",
        )
        questions[f"promised_{index}"] = noul(
            {"task": "Was this item promised to someone outside the team?", "item": task},
            true="A customer, partner, executive, or auditor was told this would happen",
            false="Internal intent only; no external party is expecting it",
        )
        questions[f"decays_{index}"] = noul(
            {"task": "Does this item get more expensive or harder the longer it is left?", "item": task},
            true="Delay compounds the work: growing backlog, drifting branch, spreading bad data",
            false="It costs the same whenever it is picked up",
        )
    for index in need_goal:
        task = {"title": f"`tasks[{index}].title`", "notes": f"`tasks[{index}].notes`",
                "project": f"`tasks[{index}].project`"}
        questions[f"serves_{index}"] = score(
            {"task": "How much does finishing this item move the stated goal?",
             "item": task, "this_weeks_goal": goal},
            SERVES_LEVELS,
        )
        questions[f"required_{index}"] = noul(
            {"task": "Must this item be finished before the stated goal can be reached?",
             "item": task, "this_weeks_goal": goal},
            true="The goal is blocked until this lands",
            false="The goal can be reached without it, even if it helps",
        )
    return questions


def priority(stable: dict, goalj: dict, weights: dict[str, float]) -> float:
    """Pure policy. Effort divides rather than adds: cheap wins float."""
    value = (
        weights["serves"] * goalj["serves"] / (len(SERVES_LEVELS) - 1)
        + weights["required"] * goalj["required"]
        + weights["unblocks"] * stable["unblocks"]
        + weights["promised"] * stable["promised"]
        + weights["decays"] * stable["decays"]
    )
    effort = 1.0 + weights["effort_penalty"] * stable["effort"] / (len(EFFORT_LEVELS) - 1)
    return value / effort


def bucket(stable: dict, goalj: dict, *, serve_gate: float, noul_gate: float) -> str:
    """Four buckets, decided in code so the gates are free to move."""
    if goalj["required"] >= noul_gate or goalj["serves"] >= serve_gate:
        return "now"
    if stable["promised"] >= noul_gate or stable["unblocks"] >= noul_gate:
        return "unrelated but owed"
    if stable["decays"] >= noul_gate:
        return "rotting"
    return "not this week"


def main() -> None:
    st.set_page_config(page_title="Task Prioritizer", page_icon="✅", layout="wide")
    st.title("✅ Task Prioritizer")
    st.caption("Re-rank the list against this week's goal. Change the goal, keep most of the judgments.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Weights")
        st.caption("Re-ranking makes **no** API calls.")
        weights = {
            "serves": st.slider("Serves the goal", 0.0, 2.0, 1.0, 0.1),
            "required": st.slider("Goal is blocked without it", 0.0, 2.0, 0.8, 0.1),
            "unblocks": st.slider("Unblocks someone else", 0.0, 2.0, 0.6, 0.1),
            "promised": st.slider("Promised externally", 0.0, 2.0, 0.5, 0.1),
            "decays": st.slider("Gets worse if left", 0.0, 2.0, 0.3, 0.1),
            "effort_penalty": st.slider("Penalty for big items", 0.0, 2.0, 0.8, 0.1),
        }
        st.divider()
        serve_gate = st.slider("Serves-the-goal to land in Now", 0.0, 3.0, 2.0, 0.1)
        noul_gate = st.slider("P(yes) for a flag to count", 0.0, 1.0, 0.6, 0.05)
        tasks_path = st.text_input("Tasks JSON (optional)", value="")

    goal = st.text_area("This week's goal", value=DEFAULT_GOAL, height=110)
    tasks = load_tasks(Path(tasks_path).expanduser() if tasks_path.strip() else None)
    if not tasks:
        st.warning("No tasks. Provide a tasks JSON, or restore sample_tasks.json.")
        return

    stable_cache: dict = st.session_state.setdefault("stable", {})
    goal_cache: dict = st.session_state.setdefault("goal_judgments", {})
    meter: dict = st.session_state.setdefault("meter", {"questions": 0, "tokens": 0, "cost": 0.0,
                                                        "elapsed": 0.0, "requests": 0})
    goal_key = goal.strip()
    need_stable, need_goal = missing(tasks, stable_cache, goal_cache, goal_key)

    if need_stable or need_goal:
        st.info(
            f"To judge this list against this goal: {len(need_stable)} task(s) need their "
            f"stable judgments, {len(need_goal)} need goal-relative ones. "
            f"{len(tasks) - len(need_stable)} stable judgment set(s) come from cache."
        )
    else:
        st.success("Everything on screen came from cache. This goal has already been judged.")

    if st.button("Judge what is missing", type="primary", disabled=not (need_stable or need_goal)):
        questions = build_questions(tasks, goal_key, need_stable, need_goal)
        try:
            with st.spinner(f"Asking {len(questions)} questions..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"tasks": tasks, "goal": goal_key}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        for index in need_stable:
            stable_cache[tasks[index]["id"]] = {
                "effort": answers.score(f"effort_{index}"),
                "unblocks": answers.noul(f"unblocks_{index}"),
                "promised": answers.noul(f"promised_{index}"),
                "decays": answers.noul(f"decays_{index}"),
            }
        for index in need_goal:
            goal_cache[(goal_key, tasks[index]["id"])] = {
                "serves": answers.score(f"serves_{index}"),
                "required": answers.noul(f"required_{index}"),
            }
        meter["questions"] += len(questions)
        meter["tokens"] += answers.input_tokens
        meter["cost"] += answers.cost_usd
        meter["elapsed"] += answers.elapsed_s
        meter["requests"] += 1
        st.rerun()

    ready = [t for t in tasks if t["id"] in stable_cache and (goal_key, t["id"]) in goal_cache]
    if not ready:
        st.stop()

    # --- policy only below this line ---
    rows = []
    for task in ready:
        stable = stable_cache[task["id"]]
        goalj = goal_cache[(goal_key, task["id"])]
        rows.append((priority(stable, goalj, weights), bucket(stable, goalj, serve_gate=serve_gate,
                                                              noul_gate=noul_gate), task, stable, goalj))
    rows.sort(key=lambda row: -row[0])

    order = ["now", "unrelated but owed", "rotting", "not this week"]
    labels = {"now": "🎯 Serves the goal", "unrelated but owed": "🤝 Owed to someone",
              "rotting": "🧊 Getting worse", "not this week": "💤 Not this week"}
    columns = st.columns(4)
    for column, name in zip(columns, order):
        column.metric(labels[name], sum(1 for row in rows if row[1] == name))

    for name in order:
        group = [row for row in rows if row[1] == name]
        if not group:
            continue
        st.subheader(labels[name])
        for total, _, task, stable, goalj in group:
            with st.container(border=True):
                left, right = st.columns([5, 1])
                left.markdown(f"**{task['title']}**")
                flags = []
                if goalj["required"] > noul_gate:
                    flags.append("⛔ goal blocked without it")
                if stable["promised"] > noul_gate:
                    flags.append("🤝 promised outside")
                if stable["decays"] > noul_gate:
                    flags.append("🧊 rots if left")
                if stable["unblocks"] > noul_gate:
                    flags.append("🚧 someone is waiting")
                left.caption(
                    f"{task.get('project', '')} · effort {stable['effort']:.2f}/3 · "
                    f"serves goal {goalj['serves']:.2f}/3"
                    + ("  ·  " + " · ".join(flags) if flags else "")
                )
                right.metric("Rank", f"{total:.2f}")
                if task.get("notes"):
                    with st.expander("Notes"):
                        st.text(task["notes"])

    st.divider()
    st.caption(
        f"{meter['requests']} request(s), {meter['questions']} questions, "
        f"{meter['tokens']:,} input tokens, {meter['elapsed']:.1f}s, ${meter['cost']:.6f} so far. "
        f"Rewriting the goal re-asks {len(GOAL_KEYS)} questions per task; the "
        f"{len(STABLE_KEYS)} stable ones are never asked twice, and the weights above cost nothing."
    )


if __name__ == "__main__":
    main()
