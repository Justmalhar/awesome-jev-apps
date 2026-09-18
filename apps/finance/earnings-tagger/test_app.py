"""Offline checks for earnings-tagger. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    transcript = json.loads((HERE / "sample_transcript.json").read_text(encoding="utf-8"))
    passages = transcript["passages"]
    assert len(passages) >= 15
    sections = {p["section"] for p in passages}
    assert sections == {"prepared_remarks", "qa"}, "the tone shift needs both halves of the call"
    assert len({p["speaker"] for p in passages}) >= 2

    # -- question design --
    questions = app.build_questions(passages)
    assert len(questions) == 4 * len(passages)
    assert app.NO_GUIDANCE in questions["guidance_0"]["criteria"], (
        "most passages set no guidance at all; without the escape hatch every one gets a label"
    )
    assert all(len(text) > 15 for text in app.GUIDANCE_MOVES.values())
    assert all(len(level) > 15 for level in app.COMMITMENT_LEVELS)
    assert questions["guidance_0"]["type"] == "choice"
    assert questions["hedging_0"]["type"] == "noul" and questions["deflection_0"]["type"] == "noul"
    assert questions["commitment_0"]["type"] == "score"
    assert app.NO_GUIDANCE not in app.GUIDANCE_MOVED

    # -- flagging policy --
    assert app.notable("range_widened", 0.9, 0.1, 0.6, 0.6) == "softened_and_hedged"
    assert app.notable("withdrawn", 0.9, 0.1, 0.6, 0.6) == "softened_and_hedged"
    assert app.notable("range_widened", 0.2, 0.1, 0.6, 0.6) == "guidance_moved", (
        "a widened range stated plainly is still a guidance move"
    )
    assert app.notable("raised", 0.9, 0.1, 0.6, 0.6) == "guidance_moved", (
        "a raise is never 'softened', however hedged the delivery"
    )
    assert app.notable(app.NO_GUIDANCE, 0.9, 0.9, 0.6, 0.6) == "hedged_non_answer"
    assert app.notable(app.NO_GUIDANCE, 0.1, 0.9, 0.6, 0.6) == "non_answer"
    assert app.notable(app.NO_GUIDANCE, 0.6, 0.6, 0.6, 0.6) == "hedged_non_answer", "gates are inclusive"
    assert app.notable(app.NO_GUIDANCE, 0.1, 0.1, 0.6, 0.6) == ""
    assert app.notable("reaffirmed", 0.1, 0.1, 0.6, 0.6) == "", "a plain reaffirmation is not news"

    # -- the shift is a subtraction, done here, never asked of the model --
    frame = pd.DataFrame(
        [
            {"speaker": "A", "section": "prepared_remarks", "hedging": 0.2},
            {"speaker": "A", "section": "prepared_remarks", "hedging": 0.4},
            {"speaker": "A", "section": "qa", "hedging": 0.9},
            {"speaker": "B", "section": "prepared_remarks", "hedging": 0.5},
            {"speaker": "B", "section": "qa", "hedging": 0.3},
        ]
    )
    shift = app.section_shift(frame, "hedging")
    assert shift.loc["A", "shift"] == 0.6, shift
    assert shift.loc["B", "shift"] == -0.2
    assert list(shift.index) == ["A", "B"], "sorted by shift, loudest first"

    # a speaker who only appears in one section must not blow up
    one_sided = pd.DataFrame([{"speaker": "C", "section": "qa", "hedging": 0.5}])
    assert pd.isna(app.section_shift(one_sided, "hedging").loc["C", "shift"])


if __name__ == "__main__":
    check()
    print("ok")
