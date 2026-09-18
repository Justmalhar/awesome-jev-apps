"""Offline checks for address-normalizer."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # ── the enumerator: candidates are generated, never generated *text* ──
    assert app.candidate_parses("") == []
    assert app.candidate_parses("   ,, ") == []

    ambiguous = app.candidate_parses("Flat 2 12 High St, London, SW1A 1AA")
    assert 3 <= len(ambiguous) <= 6, len(ambiguous)
    assert all(set(parse) == set(app.FIELDS) for parse in ambiguous)
    # both readings of the leading numbers must be on the table for a model to rank
    assert {"Flat 2"} <= {p["unit"] for p in ambiguous}
    assert {"12"} <= {p["house_number"] for p in ambiguous}
    assert any(p["street"] == "12 High St" for p in ambiguous), "unit-only reading missing"
    assert all(p["postcode"] == "SW1A 1AA" for p in ambiguous)

    # a unit that trails the street, and a house number that trails it too
    assert any(p["unit"] == "Ste 7500" and p["house_number"] == "350"
               for p in app.candidate_parses("350 5th Ave Ste 7500, New York, NY 10118"))
    assert any(p["house_number"] == "175" and p["street"] == "Rue de la Loi"
               for p in app.candidate_parses("Rue de la Loi 175, 1048 Bruxelles"))
    # the numeric block that could be a postcode or part of the settlement name
    berlin = app.candidate_parses("Musterstrasse 12, 10115 Berlin")
    assert {"10115", ""} <= {p["postcode"] for p in berlin}, berlin

    assert app.candidate_parses("12 High St, York", limit=3) == app.candidate_parses("12 High St, York")[:3]

    # ── question design ──
    items = [
        {"raw": "9 Mill Rd, Springfield",
         "candidates": {f"p{k}": parse
                        for k, parse in enumerate(app.candidate_parses("9 Mill Rd, Springfield"))}},
        {"raw": "n/a", "candidates": {"p0": {f: "" for f in app.FIELDS}}},
    ]
    questions = app.build_questions(items)
    assert len(questions) == 6, "one Choice + two independent Nouls per address"
    assert {q["type"] for q in questions.values()} == {"choice", "noul"}

    for index, item in enumerate(items):
        criteria = questions[f"parse_{index}"]["criteria"]
        # every Choice needs its escape hatch, and it must be a real description
        assert "none_correct" in criteria
        assert len(criteria["none_correct"]) > 40
        assert set(criteria) - {"none_correct"} == set(item["candidates"])
        assert all(c for c in criteria.values())
    # the two Nouls are separate properties, not levels on one rubric
    assert questions["incomplete_0"]["type"] == "noul"
    assert questions["nonphysical_0"]["type"] == "noul"
    assert questions["incomplete_0"]["criteria"].keys() == {"true", "false"}

    # ── batching is sized against the provider window, not a constant ──
    many = [{"raw": "x" * 400, "candidates": {}} for _ in range(50)]
    wide = app.batch_items(many, context_tokens=64000)
    narrow = app.batch_items(many, context_tokens=4000)
    assert len(wide) < len(narrow), "a smaller window must produce more requests"
    assert sum(len(b) for b in narrow) == 50, "no item may be dropped"
    assert app.batch_items([], 64000) == []
    assert all(b for b in narrow), "no empty batch"

    # ── policy, at its boundaries ──
    assert app.decide("none_correct", 0.99, 0.7) == "unparseable", "no-match wins over confidence"
    assert app.decide("p0", 0.70, 0.7) == "auto_accept", "the gate is inclusive at equality"
    assert app.decide("p0", 0.6999, 0.7) == "curator"
    assert app.decide("p0", 0.0, 0.0) == "auto_accept"
    assert app.decide("p0", 0.5, 1.0) == "curator"

    assert app.describe({f: "" for f in app.FIELDS}) == "every field left empty"
    assert "house_number=12" in app.describe(dict.fromkeys(app.FIELDS, "") | {"house_number": "12"})

    # ── sample data ships, and survives the "n/a is not a null" trap ──
    sample = HERE / "sample_addresses.csv"
    assert sample.is_file()
    frame = app.load_frame(None)
    assert len(frame) >= 30, len(frame)
    assert "raw_address" in frame.columns
    assert (frame["raw_address"] == "n/a").any(), "n/a must survive as a string"
    assert frame["raw_address"].map(lambda s: bool(app.candidate_parses(s))).all()


if __name__ == "__main__":
    check()
    print("ok")
