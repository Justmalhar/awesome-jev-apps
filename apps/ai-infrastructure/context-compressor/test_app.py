"""Offline checks for context-compressor. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    data = app.load_context(None)
    assert {"current_turn", "items"} <= set(data)
    items = data["items"]
    assert len(items) >= 12
    assert any(item["kind"] == "system" for item in items)
    assert len({item["id"] for item in items}) == len(items)

    questions = app.build_questions(items)
    assert len(questions) == len(items) * (len(app.PROPERTIES) + 1)
    for name in app.PROPERTIES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["loss_0"]["type"] == "score"
    assert all(len(level) > 15 for level in app.LOSS_LEVELS)
    assert len(set(app.LOSS_LEVELS)) == len(app.LOSS_LEVELS)

    # Token cost is Python arithmetic over characters, never a model judgment.
    assert app.token_cost({"text": "x" * 400}) == 100
    assert app.token_cost({"text": ""}) == 1

    calm = {"needed_now": 0.0, "binding_constraint": 0.0, "superseded": 0.0, "only_source": 0.0}
    hot = {**calm, "needed_now": 1.0, "only_source": 1.0}
    assert app.value(hot, 3.0, constraint_bonus=1.5) > app.value(calm, 3.0, constraint_bonus=1.5)
    # superseded material is worth nothing however loud it is
    assert app.value({**hot, "superseded": 1.0}, 3.0, constraint_bonus=1.5) == 0.0
    # a standing constraint beats a merely interesting item at the same loss
    assert app.value({**calm, "binding_constraint": 1.0}, 0.0, constraint_bonus=1.5) > 0.0

    def row(rid, kind, text, **props):
        merged = {**calm, **props}
        return {"id": rid, "kind": kind, "text": text, "properties": merged,
                "loss": props.pop("loss", 3.0) if "loss" in props else 3.0}

    rows = [
        row("sys-01", "system", "s" * 40),
        row("con-01", "user_turn", "c" * 40, binding_constraint=0.9),
        row("big-01", "tool_output", "b" * 4000, needed_now=1.0, only_source=1.0),
        row("junk-1", "document", "j" * 40),
    ]
    kept = app.select(rows, 10**6, constraint_bonus=1.5, pin_gate=0.7)
    assert {r["id"] for r in kept} == {"sys-01", "con-01", "big-01", "junk-1"}

    # Under a budget that fits neither big item, pins still survive.
    tight = app.select(rows, 5, constraint_bonus=1.5, pin_gate=0.7)
    assert {r["id"] for r in tight} == {"sys-01", "con-01"}, [r["id"] for r in tight]
    assert all(r["pinned"] for r in tight)

    # Output preserves the original ordering, so the window reads chronologically.
    order = [r["id"] for r in app.select(rows, 10**6, constraint_bonus=1.5, pin_gate=0.7)]
    assert order == ["sys-01", "con-01", "big-01", "junk-1"]

    # Nothing is ever rewritten: kept text is byte-identical to the input.
    by_id = {r["id"]: r for r in rows}
    assert all(k["text"] == by_id[k["id"]]["text"] for k in kept)

    assert len(app.batch(items, 10**6)) == 1
    assert sum(len(g) for g in app.batch(items, 25)) == len(items)


if __name__ == "__main__":
    check()
    print("ok")
