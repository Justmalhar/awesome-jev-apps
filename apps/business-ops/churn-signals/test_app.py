"""Offline checks for churn-signals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    conversations = json.loads((HERE / "sample_conversations.json").read_text(encoding="utf-8"))
    assert len(conversations) >= 8
    assert all({"account", "channel", "text"} <= set(c) for c in conversations)

    questions = app.build_questions(conversations)
    per_conversation = len(app.SIGNALS) + len(app.GRADED)
    assert len(questions) == len(conversations) * per_conversation

    # independent signals must be separate Nouls, never a Choice or a rubric
    for key in app.SIGNALS:
        assert questions[f"{key}__0"]["type"] == "noul"
        assert set(questions[f"{key}__0"]["criteria"]) == {"true", "false"}
    for key, levels in app.GRADED.items():
        assert questions[f"{key}__0"]["type"] == "score"
        assert all(len(level) > 15 for level in levels), key

    # feature matrix shape is the deliverable
    judgments = [
        {**{k: 0.9 for k in app.SIGNALS}, **{k: 2.0 for k in app.GRADED}}
        for _ in conversations
    ]
    judgments[0] = {**{k: 0.1 for k in app.SIGNALS}, **{k: 0.0 for k in app.GRADED}}
    frame = app.to_frame(conversations, judgments)
    assert len(frame) == len(conversations)
    assert list(app.SIGNALS) + list(app.GRADED) == list(frame.columns)[3:]

    rollup = app.account_rollup(frame, 0.6)
    assert rollup["conversations"].sum() == len(conversations)
    # the quiet conversation must not drag its account's max down
    assert rollup["signals fired"].max() == len(app.SIGNALS)
    # nothing fires when the threshold is above every probability
    assert app.account_rollup(frame, 0.99)["signals fired"].sum() == 0


if __name__ == "__main__":
    check()
    print("ok")
