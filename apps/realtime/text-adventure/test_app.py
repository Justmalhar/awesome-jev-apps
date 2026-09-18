"""Offline checks for text-adventure."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import adventure as app

    start = {"room": app.WORLD["start"], "inventory": [], "scrapes": 0}
    questions = app.build_questions(start, "pry the grate open with the crowbar")
    assert len(questions) == 3, "one request per turn, three questions"
    assert app.UNCLEAR in questions["resolution"]["criteria"], "a Choice needs a no-match option"
    assert app.NO_TARGET in questions["target"]["criteria"], "a Choice needs a no-match option"
    assert questions["dangerous"]["type"] == "noul"

    # Carried items must be selectable as targets even once out of the room.
    carrying = {"room": "tunnel", "inventory": ["lantern"], "scrapes": 0}
    assert "lantern" in app.build_questions(carrying, "light the lantern")["target"]["criteria"]

    state = app.scene_state(start, "take the lantern")
    assert state["player_action"] == "take the lantern"
    assert state["carrying"] == ["nothing at all"], "an empty inventory must still read as something"
    assert "grate" in state["visible_objects"]

    # Taking an item.
    after, lines = app.apply_turn(start, "succeeds", "lantern", 0.0, 0.7)
    assert after["inventory"] == ["lantern"] and "take" in lines[0]
    # Taking it twice does not duplicate it.
    again, _ = app.apply_turn(after, "succeeds", "lantern", 0.0, 0.7)
    assert again["inventory"] == ["lantern"]

    # The lock is Python's, not the model's: confident success does not open it.
    blocked, lines = app.apply_turn(start, "succeeds", "grate", 0.0, 0.7)
    assert blocked["room"] == "cellar", "no crowbar, no exit"
    assert "lever" in lines[0]
    with_bar = {"room": "cellar", "inventory": ["crowbar"], "scrapes": 0}
    opened, _ = app.apply_turn(with_bar, "succeeds", "grate", 0.0, 0.7)
    assert opened["room"] == "tunnel"

    # A failed judgment never moves the player, whatever the target.
    for resolution in ("blocked", "impossible", "no_effect", app.UNCLEAR):
        stuck, lines = app.apply_turn(with_bar, resolution, "grate", 0.0, 0.7)
        assert stuck["room"] == "cellar", resolution
        assert lines[0] == app.NARRATION[resolution]

    # Danger is scored independently of success, and the tally is Python's.
    hurt, lines = app.apply_turn(start, "impossible", "grate", 0.9, 0.7)
    assert hurt["scrapes"] == 1 and "scrape" in lines[-1]
    assert app.apply_turn(start, "impossible", "grate", 0.69, 0.7)[0]["scrapes"] == 0
    assert app.apply_turn(start, "impossible", "grate", 0.70, 0.7)[0]["scrapes"] == 1

    # Every room's exits must point somewhere real.
    for room in app.WORLD["rooms"].values():
        for spec in room["exits"].values():
            assert spec["to"] in app.WORLD["rooms"]


if __name__ == "__main__":
    check()
    print("ok")
