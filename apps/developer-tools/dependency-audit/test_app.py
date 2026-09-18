"""Offline checks for dependency-audit. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    # -- the changelog is split by Python, per bullet, under its heading ----
    entries = app.split_changelog((HERE / "sample_changelog.md").read_text(encoding="utf-8"))
    assert len(entries) >= 12, len(entries)
    headings = {e.heading for e in entries}
    assert {"Breaking", "Changed", "Fixed", "Security", "Deprecated", "Internal"} <= headings, headings
    assert any("retries" in e.text and e.heading == "Changed" for e in entries)
    assert all(e.text and not e.text.startswith("-") for e in entries)
    assert all(len(e.text) <= app.ENTRY_CHARS for e in entries)
    assert app.split_changelog("") == []
    assert app.split_changelog("# Only a heading") == []

    # -- our call sites come from ast, exactly ------------------------------
    usage = app.extract_usage(
        (HERE / "sample_usage.py").read_text(encoding="utf-8"), "shipyard", "sample_usage.py")
    assert "shipyard" in usage.imported and "shipyard.fetch" in usage.imported
    symbols = {s.source for s in usage.sites}
    assert any("shipyard.connect(" in s for s in symbols), symbols
    assert any("fetch(url)" in s for s in symbols)
    # an object built from the package keeps counting as usage
    assert any("client.send(message, blocking=True)" in s for s in symbols), symbols
    assert usage.sites == sorted(usage.sites, key=lambda s: s.line)
    assert all(s.file == "sample_usage.py" for s in usage.sites)
    # a package we do not import produces no call sites at all
    empty = app.extract_usage((HERE / "sample_usage.py").read_text(encoding="utf-8"), "requests")
    assert empty.sites == [] and empty.imported == []
    # the summary is what actually reaches the model
    assert set(usage.summary()) == {"package", "imported_names", "call_sites"}

    # -- three judgments per entry, one request -----------------------------
    questions = app.build_questions(entries)
    assert len(questions) == 3 * len(entries), len(questions)
    assert questions["relevant_0"]["type"] == "noul"
    assert questions["action_needed_0"]["type"] == "noul"
    assert questions["urgency_0"]["type"] == "score"

    # -- score levels are concrete situations -------------------------------
    assert len(app.URGENCY_LEVELS) == 4
    assert all(len(level) > 15 for level in app.URGENCY_LEVELS)
    assert questions["urgency_0"]["criteria"] == app.URGENCY_LEVELS
    # both nouls carry both sides of the boundary
    for qid in ("relevant_0", "action_needed_0"):
        assert set(questions[qid]["criteria"]) == {"true", "false"}

    # -- policy boundaries, pure Python -------------------------------------
    gates = {"relevance_gate": 0.6, "action_gate": 0.5, "urgency_gate": 2.0}
    assert app.verdict(0.9, 0.9, 2.0, **gates) == "blocks_upgrade"
    # urgency alone blocks, even when relevance looks low
    assert app.verdict(0.1, 0.1, 2.5, **gates) == "blocks_upgrade"
    assert app.verdict(0.6, 0.5, 1.99, **gates) == "needs_work"
    assert app.verdict(0.6, 0.49, 1.0, **gates) == "read_before_upgrading"
    assert app.verdict(0.59, 0.9, 1.0, **gates) == "ignore"
    # retuning re-decides stored floats with no inference
    assert app.verdict(0.59, 0.9, 1.0, relevance_gate=0.5, action_gate=0.5,
                       urgency_gate=2.0) == "needs_work"


if __name__ == "__main__":
    check()
    print("ok")
