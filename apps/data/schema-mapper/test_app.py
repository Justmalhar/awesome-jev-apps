"""Offline checks for schema-mapper."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    source = json.loads((HERE / "sample_source_schema.json").read_text(encoding="utf-8"))["fields"]
    target = json.loads((HERE / "sample_target_schema.json").read_text(encoding="utf-8"))["fields"]
    assert len(source) == 40 and len(target) == 35, "the bundled sample is the 40x35 migration"
    for field in source + target:
        assert {"name", "type", "samples"} <= set(field), f"state needs samples: {field}"

    # Score levels are the three actions, not a similarity dial.
    assert len(app.LEVELS) == 3
    assert all(len(level) > 15 for level in app.LEVELS)

    # Abbreviation expansion is what lets the lexical prefilter reach across a rename.
    assert app.normalize("cust_nm") == "customer name"
    assert app.normalize("dt_created") == "created date"
    assert app.normalize("addr_ln1") == "1 address line"

    # Free text may hold anything; numbers and booleans may not be each other.
    assert app.type_compatible("string", "timestamp")
    assert app.type_compatible("integer", "float")
    assert not app.type_compatible("integer", "boolean")

    # The prefilter must throw away most of the grid and still keep every real mapping.
    pairs = app.candidate_pairs(source, target, 0.35)
    assert 0 < len(pairs) < len(source) * len(target) / 2, len(pairs)
    kept = {(source[i]["name"], target[j]["name"]) for i, j, _ in pairs}
    for known in [
        ("cust_nm", "customer_full_name"),
        ("dt_created", "created_at"),
        ("billing_email", "invoice_email"),
        ("mobile_phone", "mobile_number"),
        ("lead_src", "acquisition_source"),
    ]:
        assert known in kept, f"prefilter dropped a real mapping: {known}"
    # At the top of the range only the pairs whose expanded names are identical survive.
    tight = app.candidate_pairs(source, target, 0.99)
    assert 0 < len(tight) < 10, len(tight)
    assert ("emp_cnt", "employee_count") in {
        (source[i]["name"], target[j]["name"]) for i, j, _ in tight
    }

    # One question per surviving pair, and every pair's state carries both sides.
    questions = app.build_questions(pairs)
    assert len(questions) == len(pairs)
    assert all(q["type"] == "score" and q["criteria"] == app.LEVELS for q in questions.values())
    state = app.pair_state(source, target, pairs[:2])
    assert state["pairs"][0]["source"]["samples"] and state["pairs"][0]["target"]["samples"]

    # Batching reads the provider's context window; a smaller window means more requests.
    wide = app.batch_pairs(source, target, pairs, 64000)
    narrow = app.batch_pairs(source, target, pairs, 32000)
    assert sum(len(b) for b in wide) == len(pairs) == sum(len(b) for b in narrow)
    assert len(narrow) > len(wide), "32k must split into more requests than 64k"

    # Greedy one-to-one: a target field receives exactly one source column.
    scored = [(0, 0, 1.9), (1, 0, 1.8), (1, 1, 1.2), (2, 2, 0.4)]
    chosen = app.assign(scored, floor=0.5)
    assert chosen == [(0, 0, 1.9), (1, 1, 1.2)], chosen
    assert [j for _, j, _ in chosen] == sorted({j for _, j, _ in chosen}), "no target used twice"
    assert app.assign(scored, floor=1.85) == [(0, 0, 1.9)], "floor drops the rest"
    assert app.assign([], floor=0.5) == []


if __name__ == "__main__":
    check()
    print("ok")
