"""Offline checks for api-breaking-change. No key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import api_break as app

    old = app.extract_signatures((HERE / "sample_api_old.py").read_text(encoding="utf-8"))
    new = app.extract_signatures((HERE / "sample_api_new.py").read_text(encoding="utf-8"))

    # -- the parser reads signatures exactly; the model is never asked to ---
    assert set(old) >= {"connect", "fetch", "parse_legacy", "serialize", "_cache_key",
                        "Client.send", "Client.close", "Client._reconnect"}, sorted(old)
    assert old["connect"].params == ["host", "port"]
    assert old["connect"].required == ["host"]
    assert old["connect"].defaults == {"port": "5432"}
    assert old["Client.send"].params == ["message", "blocking"], old["Client.send"].params
    assert old["Client.send"].kind == "method"
    assert old["Client.send"].public and not old["Client._reconnect"].public
    assert not old["_cache_key"].public

    # -- the diff is set arithmetic in Python -------------------------------
    changes = app.diff_signatures(old, new)
    found = {(c.symbol, c.kind) for c in changes}
    assert ("parse_legacy", "removed") in found
    assert ("connect", "param_added") in found
    assert ("fetch", "default_changed") in found
    assert ("Client.send", "param_renamed") in found, sorted(found)
    assert ("_cache_key", "param_added") in found
    # a rename is one change, not an add plus a remove
    assert not [c for c in changes if c.symbol == "Client.send" and c.kind == "param_removed"]
    # identical inputs produce no changes at all
    assert app.diff_signatures(old, old) == []
    assert all(c.detail for c in changes)

    # -- three judgments per change, one request ----------------------------
    questions = app.build_questions(changes)
    assert len(questions) == 3 * len(changes), len(questions)
    assert questions["impact_0"]["type"] == "score"
    assert questions["mechanical_0"]["type"] == "noul"
    assert questions["documented_0"]["type"] == "noul"

    # -- levels are concrete situations, not labels -------------------------
    assert len(app.IMPACT_LEVELS) == 4
    assert all(len(level) > 15 for level in app.IMPACT_LEVELS)
    assert questions["impact_0"]["criteria"] == app.IMPACT_LEVELS

    # -- policy boundaries, pure Python -------------------------------------
    gates = {"gate": 2.0, "public_gate": 0.5}
    assert app.verdict(3.0, 0.9, **gates) == "breaking"
    assert app.verdict(2.0, 0.9, **gates) == "breaking"
    assert app.verdict(1.99, 0.9, **gates) == "risky"
    assert app.verdict(1.0, 0.9, **gates) == "risky"
    assert app.verdict(0.99, 0.9, **gates) == "compatible"
    # a loud break to something nobody was meant to call is still internal
    assert app.verdict(3.0, 0.2, **gates) == "internal"
    # retuning the gate re-decides a stored score with no inference
    assert app.verdict(1.5, 0.9, gate=1.5, public_gate=0.5) == "breaking"


if __name__ == "__main__":
    check()
    print("ok")
