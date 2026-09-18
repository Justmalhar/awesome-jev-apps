"""Offline checks for deep-classifier. The beam search runs entirely on fake
distributions here -- no key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    taxonomy = json.loads((HERE / "sample_taxonomy.json").read_text(encoding="utf-8"))
    items = json.loads((HERE / "sample_products.json").read_text(encoding="utf-8"))

    # ── The bundled taxonomy must actually be three levels deep. ──
    assert app.taxonomy_depth(taxonomy) == 3, app.taxonomy_depth(taxonomy)
    leaves = app.leaf_paths(taxonomy)
    assert len(leaves) >= 30, f"a shallow taxonomy makes beam search pointless: {len(leaves)}"
    assert all(len(path) == 3 for path in leaves), "every leaf must sit at full depth"
    assert len(items) >= 20 and all("title" in item for item in items)

    # ── Tree navigation, including the paths that do not exist. ──
    assert app.node_at(taxonomy, ())["name"] == "catalog"
    assert app.node_at(taxonomy, ("electronics", "audio"))["name"] == "audio"
    assert app.node_at(taxonomy, ("electronics", "nope")) is None
    assert app.node_at(taxonomy, ("electronics", "audio", "headphones")).get("children") is None

    # ── Every Choice needs its escape hatch, and every option a description. ──
    options = app.branch_options(app.node_at(taxonomy, ("electronics",)))
    assert app.NO_MATCH in options, "a Choice without a no-match option nominates the least-wrong branch"
    assert len(app.NO_MATCH_TEXT) > 60
    assert all(len(text) > 15 for text in options.values()), "options must describe, not label"
    for path in [(), ("electronics",), ("electronics", "audio")]:
        assert app.NO_MATCH in app.branch_options(app.node_at(taxonomy, path))

    # ── One question per OPEN beam, all in one ask(); closed beams cost nothing. ──
    beams = [
        app.Beam(item=0, path=(), probability=1.0),
        app.Beam(item=1, path=("electronics",), probability=0.6),
        app.Beam(item=2, path=("apparel",), probability=0.4, open=False),
        app.Beam(item=3, path=("electronics", "audio", "headphones"), probability=0.5),
    ]
    questions = app.build_level_questions(beams, taxonomy)
    assert set(questions) == {"beam_0", "beam_1"}, \
        "closed beams and leaf beams must not be asked about"
    assert questions["beam_0"]["type"] == "choice"
    assert questions["beam_1"]["instructions"]["parent_category"] == "electronics"
    assert questions["beam_0"]["instructions"]["item"] == "`items[0]`", "state by path"
    assert app.build_level_questions([], taxonomy) == {}

    # ── Expansion: cumulative probability is a PRODUCT, computed in Python. ──
    start = [app.Beam(item=0, path=(), probability=1.0)]
    expanded = app.expand(start, {"beam_0": {"electronics": 0.55, "apparel": 0.35, app.NO_MATCH: 0.10}})
    assert len(expanded) == 3
    by_path = {beam.path: beam for beam in expanded}
    assert abs(by_path[("electronics",)].probability - 0.55) < 1e-9
    assert by_path[("electronics",)].open is True
    # NO_MATCH keeps the path where it is and closes the beam.
    assert by_path[()].open is False and abs(by_path[()].probability - 0.10) < 1e-9

    # Second level multiplies in, it does not replace.
    second = app.expand(
        [app.Beam(item=0, path=("electronics",), probability=0.55)],
        {"beam_0": {"audio": 0.8, "computing": 0.2}},
    )
    assert abs(next(b for b in second if b.path[-1] == "audio").probability - 0.44) < 1e-9

    # A closed beam survives expansion untouched -- no question, no change.
    closed = app.Beam(item=0, path=("apparel",), probability=0.3, open=False)
    assert app.expand([closed], {}) == [closed]

    # ── THE POINT OF THE APP: a level-2 landslide must beat a level-1 lead. ──
    # greedy would commit to `apparel` (0.55 > 0.35) and never recover.
    level1 = app.expand(
        [app.Beam(item=0, path=(), probability=1.0)],
        {"beam_0": {"apparel": 0.55, "sports_and_outdoors": 0.35, app.NO_MATCH: 0.10}},
    )
    kept = app.prune(level1, width=2, floor=0.0)
    assert len(kept) == 2 and {b.path for b in kept} == {("apparel",), ("sports_and_outdoors",)}
    level2 = app.expand(
        kept,
        {
            "beam_0": {"mens": 0.40, "footwear": 0.35, "womens": 0.25},        # apparel: split
            "beam_1": {"cycling": 0.95, "fitness": 0.03, "camping": 0.02},     # cycling: certain
        },
    )
    winner = app.best_per_item(level2)[0]
    assert winner.path == ("sports_and_outdoors", "cycling"), winner
    assert abs(winner.probability - 0.3325) < 1e-9, "0.35 * 0.95 must beat 0.55 * 0.40"

    # ── Pruning: per item, top-k, floored, and never empty. ──
    mixed = [
        app.Beam(item=0, path=("a",), probability=0.5),
        app.Beam(item=0, path=("b",), probability=0.3),
        app.Beam(item=0, path=("c",), probability=0.2),
        app.Beam(item=1, path=("d",), probability=0.9),
    ]
    assert len(app.prune(mixed, width=2, floor=0.0)) == 3, "top-2 for item 0, top-1 for item 1"
    assert {b.path for b in app.prune(mixed, width=2, floor=0.0) if b.item == 0} == {("a",), ("b",)}
    assert len(app.prune(mixed, width=10, floor=0.35)) == 2, "the floor drops weak beams"
    # Floor above everything must still leave each item its best beam.
    survivors = app.prune(mixed, width=3, floor=0.99)
    assert {beam.item for beam in survivors} == {0, 1}, "no item may fall out of the search"
    assert app.prune([], 3, 0.0) == []

    # ── Greedy comparison must read the level-1 argmax, ignoring the escape hatch. ──
    roots = app.greedy_roots(
        [app.Beam(item=7, path=(), probability=1.0)],
        {"beam_0": {app.NO_MATCH: 0.6, "apparel": 0.25, "electronics": 0.15}},
    )
    assert roots == {7: "apparel"}, "no-match is not a branch greedy could descend"
    assert app.greedy_roots([app.Beam(item=0, path=(), probability=1.0)], {}) == {}

    # ── Policy is pure and exact at its boundaries. ──
    full = app.Beam(item=0, path=("a", "b", "c"), probability=0.35)
    assert app.decide(full, 3, 0.35) == "filed", "the threshold is inclusive"
    assert app.decide(full, 3, 0.36) == "review"
    assert app.decide(app.Beam(0, ("a",), 0.99), 3, 0.1) == "partial", \
        "an early termination is not a filing, however confident"

    # ── Request splitting follows the provider's context, never a constant. ──
    many = {f"beam_{i}": {} for i in range(400)}
    small = app.split_questions(many, items, 32_000)
    large = app.split_questions(many, items, 64_000)
    assert sum(len(group) for group in small) == 400, "no question may be dropped"
    assert len(small) >= len(large) >= 1
    assert all(group for group in small)
    assert app.split_questions({}, items, 64_000) == []


if __name__ == "__main__":
    check()
    print("ok")
