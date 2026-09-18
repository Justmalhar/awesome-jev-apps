"""Offline checks for meeting-actions. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    # Each app ships its own module named `app`; the shared runner imports them
    # into one process, so drop any predecessor before resolving ours.
    sys.modules.pop("app", None)
    sys.modules.pop("jev_provider", None)
    import app

    text = app.load_sample()
    turns, names, candidates = app.prepare(text)

    # --- splitting -----------------------------------------------------------
    assert len(turns) > 30, len(turns)
    assert turns[0]["speaker"] == "Priya"
    assert all(turn["text"] for turn in turns), "a turn with no text is a parse bug"
    assert [t["index"] for t in turns] == list(range(len(turns)))
    # The title line belongs to nobody and must not become a turn.
    assert not any("standing meeting" in t["text"] for t in turns)

    # --- the roster IS the owner option set ---------------------------------
    assert names == ["Priya", "Tomas", "Dan", "Mei"], names
    # Rahul and Jonas are talked about but never speak. They are in the
    # transcript text, so an LLM would happily name them; here they cannot be
    # returned as owners at all -- those turns must come back as "external".
    assert "Rahul" in text and "Jonas" in text
    assert "Rahul" not in names and "Jonas" not in names

    options = app.owner_options(names)
    assert set(options) == set(names) | {app.UNASSIGNED, app.EXTERNAL}
    assert app.UNASSIGNED in options and app.EXTERNAL in options

    # --- deadline spans are found verbatim ----------------------------------
    by_index = {t["index"]: t for t in turns}
    absolute_turn = next(t for t in turns if "14 March" in t["text"])
    assert "14 March" in absolute_turn["deadline_spans"]
    relative_turn = next(t for t in turns if "End of next sprint" in t["text"])
    assert "End of next sprint" in relative_turn["deadline_spans"]
    assert any("30 June" in t["deadline_spans"] for t in turns)
    assert any("end of the week" in t["deadline_spans"] for t in turns)

    # Every span must be a literal slice of its own turn -- that is the guarantee.
    for turn in turns:
        for span in turn["deadline_spans"]:
            assert span in turn["text"], (turn["index"], span)
    # Overlapping patterns must not yield a span nested inside another.
    for turn in turns:
        spans = turn["deadline_spans"]
        assert not any(a != b and a in b for a in spans for b in spans), spans

    assert app.find_deadlines("no timing at all in this sentence") == []
    assert app.is_relative("Friday") is True
    assert app.is_relative("end of next sprint") is True
    assert app.is_relative("14 March") is False
    assert app.is_relative("30 June") is False
    assert app.is_relative("2025-03-14") is False

    # --- question design -----------------------------------------------------
    questions = app.build_questions(candidates, names)
    with_deadlines = [t for t in candidates if t["deadline_spans"]]
    assert len(questions) == 4 * len(candidates) + len(with_deadlines)

    for qid, question in questions.items():
        if question["type"] != "choice":
            continue
        # Every Choice needs its escape hatch.
        if qid.startswith("owner_"):
            assert app.UNASSIGNED in question["criteria"], qid
            assert app.EXTERNAL in question["criteria"], qid
        else:
            assert app.NO_DEADLINE in question["criteria"], qid
        assert len(question["criteria"]) >= 2, qid

    # Deadline options must be spans out of that turn, never anything else.
    for turn in with_deadlines:
        criteria = questions[f"deadline_{turn['index']}"]["criteria"]
        assert set(criteria) - {app.NO_DEADLINE} == set(turn["deadline_spans"])

    # Three independent Nouls, not one collapsed rubric: a turn can be a firm
    # commitment AND be retracted AND carry no deadline, all at once.
    for turn in candidates:
        for prefix in ("commit_", "stands_", "timed_"):
            assert questions[f"{prefix}{turn['index']}"]["type"] == "noul"

    # --- policy at its boundaries -------------------------------------------
    firm = dict(commit_threshold=0.6, stands_threshold=0.5, owner_threshold=0.55)

    assert app.decide(0.6, 0.9, "Tomas", 0.9, **firm)[0] == "action"      # on threshold
    assert app.decide(0.59, 0.9, "Tomas", 0.9, **firm)[0] == "discarded"  # just under
    # Retraction vetoes before the owner is even considered.
    assert app.decide(0.95, 0.49, "Mei", 0.99, **firm)[0] == "discarded"
    assert app.decide(0.95, 0.5, "Mei", 0.99, **firm)[0] == "action"
    # "Someone should..." and "put it on Rahul" both need a human, for
    # different reasons, and the reason string must say which.
    unassigned = app.decide(0.9, 0.9, app.UNASSIGNED, 0.9, **firm)
    assert unassigned[0] == "needs owner" and "nobody" in unassigned[1]
    external = app.decide(0.9, 0.9, app.EXTERNAL, 0.9, **firm)
    assert external[0] == "needs owner" and "not in the room" in external[1]
    # A named owner the model is unsure about is not an action either.
    assert app.decide(0.9, 0.9, "Dan", 0.54, **firm)[0] == "needs owner"
    assert app.decide(0.9, 0.9, "Dan", 0.55, **firm)[0] == "action"

    assert by_index  # kept for readability of failures above


if __name__ == "__main__":
    check()
    print("ok")
