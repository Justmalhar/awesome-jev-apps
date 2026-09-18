"""Offline checks for task-prioritizer. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    tasks = app.load_tasks(None)
    assert len(tasks) >= 15
    assert len({t["id"] for t in tasks}) == len(tasks), "task ids are cache keys; they must be unique"

    # cold start: everything is missing, so every question is asked once
    need_stable, need_goal = app.missing(tasks, {}, {}, "ship signup")
    assert need_stable == need_goal == list(range(len(tasks)))
    questions = app.build_questions(tasks, "ship signup", need_stable, need_goal)
    assert len(questions) == len(tasks) * (len(app.STABLE_KEYS) + len(app.GOAL_KEYS))
    assert all(len(level) > 15 for level in app.EFFORT_LEVELS + app.SERVES_LEVELS)
    assert questions["effort_0"]["type"] == "score"
    assert questions["promised_0"]["type"] == "noul"

    # the whole point: a new goal re-asks only the goal-relative half
    stable = {t["id"]: {} for t in tasks}
    goal_cache = {("ship signup", t["id"]): {} for t in tasks}
    need_stable, need_goal = app.missing(tasks, stable, goal_cache, "fix the billing bugs")
    assert need_stable == [], "stable judgments must survive a goal change"
    assert need_goal == list(range(len(tasks)))
    reasked = app.build_questions(tasks, "fix the billing bugs", need_stable, need_goal)
    assert len(reasked) == len(tasks) * len(app.GOAL_KEYS)
    assert len(reasked) < len(questions) / 2, "a goal change must cost less than half a cold run"

    # same goal again: nothing to ask at all
    assert app.missing(tasks, stable, goal_cache, "ship signup") == ([], [])
    assert app.build_questions(tasks, "ship signup", [], []) == {}

    weights = {"serves": 1.0, "required": 0.8, "unblocks": 0.6, "promised": 0.5,
               "decays": 0.3, "effort_penalty": 0.8}
    cheap = {"effort": 0.0, "unblocks": 1.0, "promised": 0.0, "decays": 0.0}
    dear = {"effort": 3.0, "unblocks": 1.0, "promised": 0.0, "decays": 0.0}
    on_goal = {"serves": 3.0, "required": 1.0}
    # identical value, more effort -> ranked lower
    assert app.priority(cheap, on_goal, weights) > app.priority(dear, on_goal, weights)
    # zero effort penalty makes size irrelevant
    flat = {**weights, "effort_penalty": 0.0}
    assert app.priority(cheap, on_goal, flat) == app.priority(dear, on_goal, flat)

    gates = {"serve_gate": 2.0, "noul_gate": 0.6}
    idle = {"effort": 1.0, "unblocks": 0.0, "promised": 0.0, "decays": 0.0}
    assert app.bucket(idle, {"serves": 2.5, "required": 0.0}, **gates) == "now"
    assert app.bucket(idle, {"serves": 0.0, "required": 0.9}, **gates) == "now"
    assert app.bucket({**idle, "promised": 0.9}, {"serves": 0.0, "required": 0.0}, **gates) == "unrelated but owed"
    assert app.bucket({**idle, "unblocks": 0.9}, {"serves": 0.0, "required": 0.0}, **gates) == "unrelated but owed"
    assert app.bucket({**idle, "decays": 0.9}, {"serves": 0.0, "required": 0.0}, **gates) == "rotting"
    assert app.bucket(idle, {"serves": 0.0, "required": 0.0}, **gates) == "not this week"


if __name__ == "__main__":
    check()
    print("ok")
