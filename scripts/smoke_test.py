#!/usr/bin/env python3
"""One live call against the configured provider. Run this FIRST.

    uv run --with httpx python scripts/smoke_test.py
    JEV_PROVIDER=openrouter uv run --with httpx python scripts/smoke_test.py

Costs a fraction of a cent. Verifies the key, the endpoint, and all three
primitives end to end, then prints what the call actually cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

from jev_provider import JevClient, JevError, choice, load_provider, noul, score  # noqa: E402

STATE = "Help! My payouts have been failing for 3 days and nobody has replied to my emails."


def main() -> int:
    try:
        provider = load_provider()
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    print(f"provider : {provider.name}")
    print(f"endpoint : {provider.base_url}")
    print(f"model    : {provider.model}")
    print(f"context  : {provider.context_tokens:,} tokens\n")

    questions = {
        "is_urgent": noul(
            "Does this message convey time-sensitive urgency?",
            true="Explicitly time-sensitive or escalating",
            false="No urgency expressed",
        ),
        "team": choice(
            "Which team should handle this?",
            {
                "billing": "Payments, payouts, invoicing, refunds",
                "technical": "Bugs, outages, integrations, API errors",
                "sales": "Pricing, upgrades, new accounts",
            },
        ),
        "frustration": score(
            "How frustrated is this customer?",
            ["Calm and matter-of-fact", "Visibly frustrated", "Angry, threatening to leave"],
        ),
    }

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(STATE, questions)
    except JevError as exc:
        print(f"\nrequest failed: {exc}", file=sys.stderr)
        return 1

    print(f'state: "{STATE}"\n')
    print(f"  is_urgent   P(yes) = {answers.noul('is_urgent'):.3f}")
    print(f"  team        = {answers.choice('team')}  (confidence {answers.confidence('team'):.3f})")
    for option, probability in sorted(answers.probabilities("team").items(), key=lambda kv: -kv[1]):
        print(f"                {option:<12} {probability:.3f}")
    print(f"  frustration = {answers.score('frustration'):.2f} / 2.00")

    print(f"\n  {answers.input_tokens:,} input tokens")
    print(f"  ${answers.cost_usd:.8f}  ({answers.elapsed_s * 1000:.0f} ms, 3 questions in 1 call)")
    print("\nsmoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
