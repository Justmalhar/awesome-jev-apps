"""Offline checks for translation-qa. No key, no network."""

from __future__ import annotations

import json
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

    # --- Score levels must be concrete situations, not labels -------------
    assert len(app.SEVERITY_LEVELS) == 3
    assert all(len(level) > 15 for level in app.SEVERITY_LEVELS)
    assert not {"low", "medium", "high"} & {l.lower() for l in app.SEVERITY_LEVELS}

    # --- Sample data parses and carries the seeded defects ----------------
    raw = (HERE / "sample_segments.json").read_text(encoding="utf-8")
    segments = app.load_segments(raw)
    assert len(segments) == 12, len(segments)
    assert [s["id"] for s in segments] == [f"s{i:02d}" for i in range(1, 13)]
    assert all(s["source"] and s["target"] for s in segments)

    for bad in ('{"not": "a list"}', '[{"source": "only"}]'):
        try:
            app.load_segments(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"load_segments accepted {bad!r}")
    try:
        app.load_segments("not json at all")
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("load_segments accepted non-JSON")

    # --- Question design ---------------------------------------------------
    questions = app.build_questions(segments)
    assert len(questions) == 6 * len(segments) == 72, len(questions)
    # five independent Nouls + one Score per segment; nothing collapsed
    per_segment = [q for q in questions if q.endswith("_0")]
    assert len(per_segment) == 6, per_segment
    assert sum(1 for q in per_segment if questions[q]["type"] == "noul") == 5
    assert sum(1 for q in per_segment if questions[q]["type"] == "score") == 1
    for hazard in app.HAZARDS:
        assert questions[f"{hazard}_0"]["type"] == "noul", hazard
    assert questions["stylistic_only_0"]["type"] == "noul"

    for qid, question in questions.items():
        if question["type"] == "choice":
            # every Choice must carry an escape hatch
            assert {"none", "other", "unclear"} & set(question["criteria"]), qid
        elif question["type"] == "score":
            assert all(len(level) > 15 for level in question["criteria"]), qid
        else:
            assert {"true", "false"} <= set(question["criteria"]), qid

    assert set(app.DEFAULT_THRESHOLDS) == set(app.HAZARDS) | {"stylistic_only"}
    assert set(app.HAZARD_LABEL) == set(app.HAZARDS)
    assert set(app.VERDICT_ORDER) == set(app.VERDICT_ICON) == {"defect", "review", "pass"}

    # --- Policy boundaries -------------------------------------------------
    t = dict(app.DEFAULT_THRESHOLDS)
    clean = {h: 0.02 for h in app.HAZARDS}

    # a good idiom: nothing fires, and the stylistic guard suppresses
    idiomatic = {**clean, "stylistic_only": 0.95}
    assert app.decide(idiomatic, 0.1, t, 1.2)[0] == "pass"

    # a confident "it's just idiom" must NOT override a confident meaning change
    lying = {**clean, "meaning_changed": 0.93, "stylistic_only": 0.95}
    verdict, why = app.decide(lying, 1.9, t, 1.2)
    assert verdict == "defect" and "meaning changed" in why, (verdict, why)

    # one hazard over its own line is enough, even with everything else clean
    for hazard in app.HAZARDS:
        fired = {**clean, "stylistic_only": 0.0, hazard: t[hazard]}
        assert app.decide(fired, 0.0, t, 1.2)[0] == "defect", hazard

    # just under every line, but a severe-looking segment still gets a human
    borderline = {h: t[h] - 0.01 for h in app.HAZARDS}
    borderline["stylistic_only"] = 0.0
    assert app.decide(borderline, 1.2, t, 1.2)[0] == "review"
    assert app.decide(borderline, 1.19, t, 1.2)[0] == "pass"

    # raising a threshold must be able to silence a hazard with no new inference
    fired = {**clean, "stylistic_only": 0.0, "omission": 0.70}
    assert app.decide(fired, 0.0, t, 1.2)[0] == "defect"
    assert app.decide(fired, 0.0, {**t, "omission": 0.9}, 1.2)[0] == "pass"


if __name__ == "__main__":
    check()
    print("ok")
