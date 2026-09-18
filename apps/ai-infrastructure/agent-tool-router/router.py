#!/usr/bin/env python3
"""agent-tool-router -- pick the right tool from a large catalog without
stuffing every schema into the model's context.

    uv run --with httpx python router.py "what's broken in production right now?"
    uv run --with httpx python router.py "remind me what we agreed in the kickoff" --top 5

The problem: an agent with 300 MCP tools pays for every schema on every turn,
and selection quality DROPS as the catalog grows. The usual fixes -- embedding
search over tool descriptions, or a smaller "router" LLM -- either miss on
negation and intent, or cost another frontier call per turn.

Two stages, and the second one is genuinely warranted:

  1. ONE request over one-line summaries of every tool. A Choice ranks the whole
     catalog, and a companion Noul asks whether this turn needs a tool at all.
  2. ONE request over the FULL schemas of only the top-k survivors, which can
     still reject all of them.

Stage 2 is a second request rather than more questions in the first because it
needs stage 1's answer to decide what state to send -- the documented reason a
second round trip is justified. Everything else stays in one call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, choice  # noqa: E402

CATALOG = json.loads((HERE / "tool_catalog.json").read_text(encoding="utf-8"))
CHARS_PER_TOKEN = 4


def stage_one(client: JevClient, turn: str) -> tuple[list[tuple[str, float]], float]:
    """Rank the entire catalog from one-line summaries, in a single request."""
    options = {tool["name"]: tool["summary"] for tool in CATALOG}
    options["__none__"] = "No tool in this catalog is appropriate for this request."

    answers = client.ask(
        {"user_turn": turn, "tool_count": len(CATALOG)},
        {
            "best_tool": choice(
                {
                    "task": "Which tool should handle this user request?",
                    "user_turn": turn,
                    "note": "Choose __none__ if the request needs no tool, or no tool fits.",
                },
                options,
            ),
            # Without this, a Choice must nominate a tool for "hello" and
            # "what do you think about X?" alike.
            "needs_tool": noul(
                {
                    "task": "Does answering this turn require calling a tool at all?",
                    "user_turn": turn,
                },
                true="Requires fetching, changing, or acting on external state",
                false="Can be answered from conversation or general knowledge alone",
            ),
        },
    )
    ranked = sorted(
        ((name, p) for name, p in answers.probabilities("best_tool").items() if name != "__none__"),
        key=lambda pair: -pair[1],
    )
    return ranked, answers.noul("needs_tool"), answers


def stage_two(client: JevClient, turn: str, shortlist: list[str]):
    """Re-decide against FULL schemas -- with a real option to reject all of them."""
    detailed = [tool for tool in CATALOG if tool["name"] in shortlist]
    options = {
        tool["name"]: f"{tool['summary']} | parameters: {json.dumps(tool['parameters'])}"
        for tool in detailed
    }
    options["__none__"] = "Having read the full schemas, none of these actually fits."

    return client.ask(
        {"user_turn": turn, "candidate_tools": detailed},
        {
            "final_tool": choice(
                {
                    "task": "Now that you can see full parameter schemas, which tool actually fits?",
                    "user_turn": turn,
                    "note": "Choose __none__ if none of them can serve this request.",
                },
                options,
            )
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Route a turn to one tool from a large catalog.")
    parser.add_argument("turn", help="the user's message")
    parser.add_argument("--top", type=int, default=3, help="how many survive to stage 2")
    parser.add_argument("--needs-tool-threshold", type=float, default=0.5)
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    print(f'turn    : "{args.turn}"')
    print(f"catalog : {len(CATALOG)} tools via {provider.name}\n")

    try:
        with JevClient(provider=provider) as client:
            ranked, needs_tool, first = stage_one(client, args.turn)

            print(f"── stage 1: ranked {len(CATALOG)} summaries in 1 request ──")
            print(f"  needs a tool at all?  P = {needs_tool:.3f}")
            for name, probability in ranked[: args.top + 2]:
                bar = "█" * int(probability * 40)
                print(f"    {name:<28} {probability:>6.3f}  {bar}")

            if needs_tool < args.needs_tool_threshold:
                print(f"\n→ answer directly, no tool call (P={needs_tool:.2f} below threshold)")
                print(f"\n1 request · {first.input_tokens:,} tokens · ${first.cost_usd:.6f}")
                return 0

            shortlist = [name for name, _ in ranked[: args.top]]
            second = stage_two(client, args.turn, shortlist)
            chosen = second.choice("final_tool")

            print(f"\n── stage 2: full schemas for {len(shortlist)} candidates ──")
            for name, probability in sorted(
                second.probabilities("final_tool").items(), key=lambda pair: -pair[1]
            ):
                print(f"    {name:<28} {probability:>6.3f}")

            if chosen == "__none__":
                print("\n→ rejected all candidates after reading the schemas")
            else:
                print(f"\n→ call {chosen}()  (confidence {second.confidence('final_tool'):.3f})")

            # What the naive approach would have cost on this single turn.
            all_schemas = sum(
                len(json.dumps(tool)) for tool in CATALOG
            ) // CHARS_PER_TOKEN
            used = first.input_tokens + second.input_tokens
            print(
                f"\n2 requests · {used:,} tokens · "
                f"${first.cost_usd + second.cost_usd:.6f} · "
                f"{first.elapsed_s + second.elapsed_s:.2f}s"
            )
            print(
                f"Stuffing all {len(CATALOG)} full schemas into a frontier model's context "
                f"is ~{all_schemas:,} tokens EVERY turn, before it reasons about any of them."
            )
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
