"""Offline checks for prompt-injection-detector. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    chunks = app.load_chunks(None)
    assert len(chunks) >= 10, "sample must cover several injection shapes plus benign text"
    assert {"id", "source", "channel", "text"} <= set(chunks[0])

    # The mechanical scan is Python's job, and it must actually find the carriers
    # the sample smuggles: zero-width characters, an HTML comment, a blind channel.
    enriched = app.enrich(chunks)
    assert any(c["invisible_control_characters"] for c in enriched), "sample lost its hidden text"
    assert any(c["html_comment_present"] for c in enriched), "sample lost its HTML comment"
    assert any(not c["shown_to_the_user"] for c in enriched), "sample lost its blind channel"
    assert not app.enrich([{"text": "plain text"}])[0]["invisible_control_characters"]
    assert app.enrich([{"text": "a‮b"}])[0]["invisible_control_characters"], "bidi override"

    questions = app.build_questions(enriched)
    assert len(questions) == len(enriched) * (len(app.PROPERTIES) + 1)

    # Independent properties are separate Nouls, never levels on one rubric.
    for name in app.PROPERTIES:
        assert questions[f"{name}_0"]["type"] == "noul", name
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["targeting_0"]["type"] == "score"
    assert questions["targeting_0"]["criteria"] == app.TARGETING_LEVELS

    # Score levels must be concrete situations, not labels.
    assert all(len(level) > 15 for level in app.TARGETING_LEVELS)
    assert len(set(app.TARGETING_LEVELS)) == len(app.TARGETING_LEVELS)

    # The exoneration Noul must not be counted as evidence of an attack.
    assert "discusses_rather_than_attempts" not in app.ATTACK_PROPERTIES
    assert len(app.ATTACK_PROPERTIES) == len(app.PROPERTIES) - 1

    gates = {"quarantine_p": 0.70, "strip_p": 0.40, "discuss_p": 0.60, "targeting_floor": 1.5}
    clean = dict.fromkeys(app.PROPERTIES, 0.02)
    plain = {"invisible_control_characters": False, "html_comment_present": False}

    def verdict(signals, targeting, chunk=plain):
        return app.decide({**clean, **signals}, targeting, chunk, **gates)[0]

    assert verdict({"overrides_operating_rules": 0.9}, 3.0) == "quarantine"
    # aimed at a human, not the agent: not a quarantine even with attack signal
    assert verdict({"overrides_operating_rules": 0.9}, 1.0) == "strip"
    # the hard negative: a blog post that quotes an attack is allowed through
    assert verdict({"addresses_the_assistant": 0.5,
                    "discusses_rather_than_attempts": 0.9}, 2.0) == "allow"
    # ...but a real attack that also reads as commentary still gets caught
    assert verdict({"requests_exfiltration": 0.85,
                    "discusses_rather_than_attempts": 0.9}, 3.0) == "quarantine"
    assert verdict({"contradicts_user_intent": 0.45}, 0.5) == "strip"
    assert verdict({"contradicts_user_intent": 0.39}, 0.5) == "allow"
    # hidden carriers lower the bar, but never on their own
    hidden = {"invisible_control_characters": True, "html_comment_present": False}
    assert verdict({"addresses_the_assistant": 0.25}, 0.5, hidden) == "strip"
    assert verdict({"addresses_the_assistant": 0.05}, 0.5, hidden) == "allow"
    assert verdict({"addresses_the_assistant": 0.25}, 0.5, plain) == "allow"

    # Every verdict the CLI prints must have an icon and be a known verdict.
    for signals, targeting in (({"overrides_operating_rules": 0.9}, 3.0), ({}, 0.0)):
        assert app.decide({**clean, **signals}, targeting, plain, **gates)[0] in app.VERDICTS

    # Batching respects a context budget rather than hardcoding one.
    assert len(app.batch(enriched, 10**6)) == 1
    assert sum(len(g) for g in app.batch(enriched, 120)) == len(enriched)
    assert all(len(g) == 1 for g in app.batch(enriched, 1))


if __name__ == "__main__":
    check()
    print("ok")
