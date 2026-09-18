"""Offline checks for catalog-normalizer."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import pandas as pd

    import app

    vocabulary = app.load_vocabulary()
    assert {"color", "size", "material", "volume", "weight"} <= set(vocabulary)

    # ── units are arithmetic, so a regex owns them end to end ──
    assert app.parse_measure("500ml") == (500.0, "ml")
    assert app.parse_measure("0.5 L") == (0.5, "l")
    assert app.parse_measure("50cl") == (50.0, "cl")
    assert app.parse_measure("12 fl oz") == (12.0, "fl oz")
    assert app.parse_measure("1,5 kg") == (1.5, "kg")
    assert app.parse_measure("250 g.") == (250.0, "g")
    for not_a_measure in ("Medium", "Jet Black", "100% Cotton", "XXS", "", "42"):
        assert app.parse_measure(not_a_measure) is None, not_a_measure

    # three spellings of the same capacity land on the same canonical number,
    # and the model was never shown a conversion
    volume_item = lambda raw: {"measure": app.parse_measure(raw)}
    assert app.resolve_value(volume_item("500ml"), "volume", None, vocabulary) == "500 ml"
    assert app.resolve_value(volume_item("0.5 L"), "volume", None, vocabulary) == "500 ml"
    assert app.resolve_value(volume_item("50cl"), "volume", None, vocabulary) == "500 ml"

    # the ambiguity that makes the attribute question worth asking: same string,
    # two attributes, two very different numbers -- and Python applies both factors
    ounces = volume_item("16 oz")
    assert app.resolve_value(ounces, "volume", None, vocabulary) == "473.176 ml"
    assert app.resolve_value(ounces, "weight", None, vocabulary) == "453.592 g"

    assert app.resolve_value(volume_item("16 furlongs"), "volume", None, vocabulary) is None
    assert app.resolve_value({"measure": None}, "volume", None, vocabulary) is None
    assert app.resolve_value({"measure": None}, "color", "color:Black", vocabulary) == "Black"
    # a term from a different attribute is not a value for this one
    assert app.resolve_value({"measure": None}, "color", "size:Medium", vocabulary) is None
    assert app.resolve_value({"measure": None}, "nonsense", None, vocabulary) is None

    # ── question design ──
    assert app.NO_ATTRIBUTE in app.attribute_options(vocabulary)
    terms = app.term_options(vocabulary)
    assert app.NO_TERM in terms
    assert len(terms[app.NO_TERM]) > 60, "the escape hatch needs a real description"
    assert "color:Black" in terms and "size:Medium" in terms
    assert all(":" in key for key in terms if key != app.NO_TERM)
    # measure attributes contribute no terms -- their values are numbers, not words
    assert not any(key.startswith(("volume:", "weight:")) for key in terms)
    assert all(description for description in terms.values())

    items = [
        {"labelled_attribute": "color", "raw_value": "Blk",
         "seen_in_products": ["Classic Cotton T-Shirt"], "row_count": 4,
         "measure": app.parse_measure("Blk")},
        {"labelled_attribute": "volume", "raw_value": "0.5 L",
         "seen_in_products": ["Insulated Steel Bottle"], "row_count": 2,
         "measure": app.parse_measure("0.5 L")},
    ]
    questions = app.build_questions(items, vocabulary)
    assert set(questions) == {"attr_0", "term_0", "attr_1"}, "a measurement needs no term question"
    assert all(q["type"] == "choice" for q in questions.values())
    assert app.NO_ATTRIBUTE in questions["attr_0"]["criteria"]
    assert app.NO_ATTRIBUTE in questions["attr_1"]["criteria"]
    assert app.NO_TERM in questions["term_0"]["criteria"]

    # ── policy, at its boundaries ──
    decide = app.decide
    assert decide("color", "color:Black", "Black", 0.95, "color", 0.7) == "auto_accept"
    assert decide("color", "color:Black", "Black", 0.70, "color", 0.7) == "auto_accept", "inclusive at equality"
    assert decide("color", "color:Black", "Black", 0.6999, "color", 0.7) == "curator"
    # the two outcomes a human must see regardless of how sure the model was
    assert decide("material", "material:Cotton", "Cotton", 0.99, "color", 0.7) == "relabel"
    assert decide("color", app.NO_TERM, None, 0.99, "color", 0.7) == "extend_vocabulary"
    assert decide(app.NO_ATTRIBUTE, None, None, 0.99, "color", 0.7) == "reject"
    # an unconvertible unit is Python failing, not the model
    assert decide("volume", None, None, 0.99, "volume", 0.7) == "curator"
    assert decide("volume", None, "500 ml", 0.99, "volume", 0.7) == "auto_accept"

    # ── batching against the provider window, and deduplication ──
    many = [{"raw_value": "x" * 300, "measure": None} for _ in range(40)]
    assert len(app.batch_items(many, 64000)) < len(app.batch_items(many, 4000))
    assert sum(len(b) for b in app.batch_items(many, 4000)) == 40
    assert app.batch_items([], 64000) == []

    assert (HERE / "sample_supplier_feed.csv").is_file()
    assert (HERE / "sample_vocabulary.json").is_file()
    frame = app.load_feed(None)
    assert set(app.FEED_COLUMNS) <= set(frame.columns)
    assert frame["supplier_id"].nunique() == 3
    resolved = app.distinct_items(frame)
    assert len(resolved) < len(frame), "the sample must actually demonstrate the saving"
    assert sum(item["row_count"] for item in resolved) == len(frame), "every row must be covered once"
    values = {(item["labelled_attribute"], item["raw_value"]) for item in resolved}
    assert len(values) == len(resolved), "distinct means distinct"
    # the planted cases the app exists to show
    assert ("color", "Cotton") in values, "a value filed under the wrong attribute"
    assert ("material", "Merino Wool") in values, "a value no canonical term covers"
    assert ("weight", "16 oz") in values and ("volume", "16 oz") in values, "the unit ambiguity"
    assert sum(1 for item in resolved if item["measure"] is not None) >= 5


if __name__ == "__main__":
    check()
    print("ok")
