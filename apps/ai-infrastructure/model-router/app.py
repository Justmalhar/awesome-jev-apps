#!/usr/bin/env python3
"""model-router -- send easy turns to a cheap model and hard ones to a frontier
model, and abstain when the turn is too underspecified to route at all.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --turns my_turns.json --json

Difficulty and stakes are two Scores, not one. "How much paracetamol for a 22kg
child?" is trivially easy and extremely expensive to get wrong; "explain a
mutex" is moderately involved and harmless either way. A single "complexity"
rubric cannot express that, and routing on it sends the dangerous-but-easy turn
to the cheap model.

The projected saving is computed in Python from the price table below. Jev is
never asked to do arithmetic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_turns.json"
CHARS_PER_TOKEN = 4

# List prices per million tokens. Edit freely -- nothing here reaches the model.
MODELS = {
    "cheap": {"name": "small-8b", "input_per_mtok": 0.15, "output_per_mtok": 0.60},
    "frontier": {"name": "frontier-xl", "input_per_mtok": 3.00, "output_per_mtok": 15.00},
}
# A rough shape for what a reply costs, so the projection is not fiction-free.
REPLY_TOKENS = {"cheap": 350, "frontier": 700}

DIFFICULTY_LEVELS = [
    "Mechanical: a fixed transformation or a fact any model already holds",
    "Ordinary: one clear task, no conflicting constraints, one pass is enough",
    "Involved: several constraints interact and the reply has to reconcile them",
    "Hard: requires holding a chain of dependent conclusions that a weak model will drop",
]

CONSEQUENCE_LEVELS = [
    "Nobody is harmed by a wrong reply; the user notices and moves on",
    "A wrong reply wastes the user's time or needs a visible correction",
    "A wrong reply costs real money, breaks production, or damages trust",
    "A wrong reply can hurt someone's health, safety, legal position or finances",
]

FLAGS = {
    "multi_step": {
        "task": "Does answering this turn require chaining several dependent conclusions together?",
        "true": "The answer depends on a conclusion the reply must reach first, more than once over",
        "false": "One retrieval, one transformation, or one explanation is enough",
    },
    "underspecified": {
        "task": "Is this turn too vague to act on without asking the user something first?",
        "true": "The referent, the goal, or the success criterion is missing and cannot be guessed safely",
        "false": "A competent assistant could act on it without checking back",
    },
    "needs_fresh_facts": {
        "task": "Does a correct reply depend on information that changes after training?",
        "true": "Depends on live prices, current events, today's system state, or the user's private data",
        "false": "Stable knowledge, or content supplied in the turn itself",
    },
}


def load_turns(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(turns: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for turn in turns:
        size = max(1, len(json.dumps(turn)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(turn)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(turns: list[dict]) -> dict:
    """Five judgments per turn, all in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(turns)):
        reference = {"text": f"`turns[{index}].text`"}
        questions[f"difficulty_{index}"] = score(
            {"task": "What does producing a correct reply to this turn demand of a model?",
             "turn": reference},
            DIFFICULTY_LEVELS,
        )
        # Deliberately a SECOND score: stakes are independent of difficulty.
        questions[f"consequence_{index}"] = score(
            {"task": "If the reply to this turn were wrong, what happens to the person who acts on it?",
             "turn": reference},
            CONSEQUENCE_LEVELS,
        )
        for name, spec in FLAGS.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "turn": reference},
                true=spec["true"], false=spec["false"],
            )
    return questions


def route(difficulty: float, consequence: float, flags: dict[str, float], *,
          difficulty_gate: float, consequence_gate: float, abstain_p: float) -> str:
    """Policy in Python: 'cheap', 'frontier' or 'abstain'."""
    if flags["underspecified"] >= abstain_p:
        return "abstain"
    if difficulty >= difficulty_gate or consequence >= consequence_gate:
        return "frontier"
    if flags["multi_step"] >= 0.5 or flags["needs_fresh_facts"] >= 0.5:
        return "frontier"
    return "cheap"


def turn_cost(turn_chars: int, tier: str) -> float:
    """Projected $ for one turn on one tier. Pure arithmetic, pure Python."""
    model = MODELS[tier]
    prompt_tokens = max(1, turn_chars // CHARS_PER_TOKEN) + 800  # + a system prompt
    return (prompt_tokens / 1e6 * model["input_per_mtok"]
            + REPLY_TOKENS[tier] / 1e6 * model["output_per_mtok"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Route turns between a cheap and a frontier model.")
    parser.add_argument("--turns", type=Path, help="JSON list of {id, text}")
    parser.add_argument("--difficulty-gate", type=float, default=2.0,
                        help=f"0..{len(DIFFICULTY_LEVELS) - 1}")
    parser.add_argument("--consequence-gate", type=float, default=2.0,
                        help=f"0..{len(CONSEQUENCE_LEVELS) - 1}")
    parser.add_argument("--abstain-p", type=float, default=0.65)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    turns = load_turns(args.turns)
    if not turns:
        print("no turns to route", file=sys.stderr)
        return 2

    groups = batch(turns, int(provider.context_tokens * 0.5))
    print(f"routing {len(turns)} turns via {provider.name} "
          f"({len(groups)} request(s), {len(turns) * (2 + len(FLAGS))} questions)", file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                answers = client.ask({"turns": group}, build_questions(group))
                for index, turn in enumerate(group):
                    flags = {name: answers.noul(f"{name}_{index}") for name in FLAGS}
                    difficulty = answers.score(f"difficulty_{index}")
                    consequence = answers.score(f"consequence_{index}")
                    rows.append({
                        "id": turn.get("id", f"#{index}"),
                        "text": turn.get("text", ""),
                        "difficulty": difficulty,
                        "consequence": consequence,
                        "flags": flags,
                        "tier": route(difficulty, consequence, flags,
                                      difficulty_gate=args.difficulty_gate,
                                      consequence_gate=args.consequence_gate,
                                      abstain_p=args.abstain_p),
                    })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    # Projection, in Python. An abstained turn costs nothing downstream.
    routed_spend = sum(turn_cost(len(r["text"]), r["tier"])
                       for r in rows if r["tier"] != "abstain")
    frontier_spend = sum(turn_cost(len(r["text"]), "frontier") for r in rows)
    saved = frontier_spend - routed_spend - cost

    if args.json:
        print(json.dumps({"rows": rows, "jev_cost_usd": cost,
                          "projected_routed_usd": routed_spend,
                          "projected_frontier_only_usd": frontier_spend,
                          "projected_saving_usd": saved}, indent=2))
        return 0

    for row in rows:
        mark = {"cheap": "💸", "frontier": "🧠", "abstain": "🙋"}[row["tier"]]
        print(f"{mark} {row['id']}  {row['tier']:<8} difficulty {row['difficulty']:.2f} "
              f"stakes {row['consequence']:.2f}  "
              f"[{' '.join(f'{k}={v:.2f}' for k, v in row['flags'].items())}]")
        print(f"     {row['text'][:100]}")

    tiers = {t: sum(1 for r in rows if r["tier"] == t) for t in ("cheap", "frontier", "abstain")}
    print(f"\n{tiers['cheap']} cheap · {tiers['frontier']} frontier · {tiers['abstain']} abstained")
    print(f"MEASURED   Jev: {calls} request(s) · {tokens:,} tokens · ${cost:.6f}")
    print(f"PROJECTED  routed ${routed_spend:.4f} vs frontier-only ${frontier_spend:.4f} "
          f"-> ${saved:.4f} saved on {len(rows)} turns "
          f"({saved / max(1e-9, frontier_spend) * 100:.0f}%)")
    print("The projection uses the MODELS price table in app.py and a token estimate from "
          "character length -- change either and re-run without new inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
