#!/usr/bin/env python3
"""Five minutes to your first Jev answer. Start here.

    uv run --with httpx python 00-primitives/tour.py

Jev answers three kinds of question and nothing else. There is no prompt to
engineer, no JSON to coax out, no parser to write. You hand it state and typed
questions; you get typed answers with calibrated probabilities.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

from jev_provider import JevClient, JevError, choice, load_provider, noul, score  # noqa: E402

TICKET = {
    "customer": {"plan": "growth", "tenure_months": 14},
    "messages": [
        {"from": "customer", "text": "Payouts have been failing for 3 days. No reply to 2 emails."},
        {"from": "customer", "text": "If this isn't fixed today we're moving to another provider."},
    ],
}


def banner(text: str) -> None:
    print(f"\n{'─' * 68}\n{text}\n{'─' * 68}")


def main() -> int:
    try:
        provider = load_provider()
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    print(f"Talking to {provider.name} ({provider.model})")

    # ── The three primitives, asked together ────────────────────────────────
    #
    # These are INDEPENDENT questions over the SAME state, so they go in one
    # request. They run in parallel and cannot see each other's answers. If a
    # question needs an earlier answer, that is a second request -- but far
    # fewer questions need that than you would guess, so batch by default.

    questions = {
        # NOUL -- probability a condition holds. No separate confidence:
        # 0.5 means genuinely split, NOT "medium intensity".
        "churn_risk": noul(
            "Is this customer threatening to leave?",
            true="States or strongly implies they will switch providers",
            false="Frustrated but no indication of leaving",
        ),
        # Use one Noul per label when several can be true at once. Do not
        # collapse independent properties into one rubric.
        "sla_breach": noul("Has the customer waited more than 48 hours without a reply?"),
        # CHOICE -- exactly one option wins. Always give it somewhere to put
        # cases that match nothing, or it must pick the least-wrong option.
        "team": choice(
            "Which team should own this ticket?",
            {
                "payments": "Payouts, transfers, settlement, payment rails",
                "platform": "API errors, outages, integrations, infrastructure",
                "success": "Account health, retention, commercial conversations",
                "unclear": "Not enough information to route confidently",
            },
        ),
        # SCORE -- position on an ORDERED rubric. Levels must describe concrete
        # situations and stand on their own; "medium" is not a level.
        "severity": score(
            "How severe is the business impact on this customer?",
            [
                "Cosmetic or curiosity -- no money or workflow affected",
                "Workflow degraded but they can still operate",
                "Revenue actively blocked right now",
                "Revenue blocked and the relationship is at risk",
            ],
        ),
    }

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(TICKET, questions)
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    banner("NOUL — probability that a condition holds")
    print(f"  churn_risk  P(yes) = {answers.noul('churn_risk'):.3f}")
    print(f"  sla_breach  P(yes) = {answers.noul('sla_breach'):.3f}")

    banner("CHOICE — one option wins, with the full distribution")
    print(f"  team = {answers.choice('team')}   confidence = {answers.confidence('team'):.3f}")
    for option, probability in sorted(answers.probabilities("team").items(), key=lambda kv: -kv[1]):
        bar = "█" * int(probability * 40)
        print(f"    {option:<10} {probability:>6.3f}  {bar}")

    banner("SCORE — probability-weighted position on an ordered rubric")
    raw = answers.raw["answers"]["severity"]
    print(f"  severity = {answers.score('severity'):.2f}   confidence = {answers.confidence('severity'):.3f}")
    for level, probability in sorted(raw.get("probabilities", {}).items()):
        print(f"    [{level}] {probability:>6.3f}  {raw.get('legend', {}).get(level, '')}")
    print("\n  Note the score can land BETWEEN levels. That is the point --")
    print("  it is a weighted position, not a bucket.")

    # ── Policy lives in code, not in the model ──────────────────────────────
    #
    # Jev gives you calibrated evidence. What to DO about it is your decision,
    # and it belongs here where you can read it, test it, and change it without
    # re-running inference.

    banner("YOUR CODE decides what to do with the evidence")
    escalate = answers.noul("churn_risk") > 0.7 and answers.score("severity") > 2.0
    route = answers.choice("team")
    if answers.confidence("team") < 0.6 or route == "unclear":
        route = "human-triage"  # low confidence is an abstain signal, not a label

    print(f"  route    -> {route}")
    print(f"  escalate -> {escalate}")
    print("\n  Change those thresholds and nothing needs re-running. The judgments")
    print("  are reusable; only the policy moved.")

    banner("What that cost")
    print(f"  {len(questions)} questions, 1 request")
    print(f"  {answers.input_tokens:,} input tokens (output tokens are free on Jev)")
    print(f"  ${answers.cost_usd:.8f}  in {answers.elapsed_s * 1000:.0f} ms")
    print(f"\n  As {len(questions)} separate calls this would have cost roughly")
    print(f"  {len(questions)}x the tokens, because the state is re-sent every time.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
