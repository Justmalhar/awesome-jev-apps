#!/usr/bin/env python3
"""context-compressor -- choose which context items to keep when the window is
tight. SELECTION, never paraphrase.

    uv run --with httpx python app.py --budget 900
    uv run --with httpx python app.py --context my_context.json --json

Every "context compression" tool on the market summarises. Summarising is
lossy in exactly the wrong place: the constraint buried in turn three ("the
deploy role there can't touch RDS") is short, boring, and the first thing a
summariser drops -- and it is the one line that decides whether the next action
is correct.

Jev cannot generate. That is the feature here, not the limitation. Every item
that survives is the user's original bytes, unaltered, and the only decision
being made is keep or drop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_context.json"
CHARS_PER_TOKEN = 4

PROPERTIES = {
    "needed_now": {
        "task": "Is this context item needed to act correctly on the current turn?",
        "true": "The next action depends on something only this item supplies",
        "false": "The next action would be the same whether or not this item were present",
    },
    "binding_constraint": {
        "task": "Does this item impose a rule, limit or permission boundary that continues to apply?",
        "true": "It states something the assistant must keep honouring on later turns, not just this one",
        "false": "It reports a fact or an observation that binds nothing",
    },
    "superseded": {
        "task": "Has a later item in this context already replaced what this one says?",
        "true": "Another item covers the same ground and is the version now in force",
        "false": "Nothing else in the context overrides it",
    },
    "only_source": {
        "task": "Is this the only item carrying the detail it contains?",
        "true": "Dropping it removes information that appears nowhere else in the context",
        "false": "The same detail is available from another item",
    },
}

LOSS_LEVELS = [
    "Dropping this item changes nothing a reader would notice",
    "Dropping this item costs some helpful colour but no correctness",
    "Dropping this item makes the next reply noticeably worse or vaguer",
    "Dropping this item makes the assistant take a wrong or unsafe action",
]

KINDS_PINNED = ("system",)


def load_context(path: Path | None) -> dict:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def token_cost(item: dict) -> int:
    """Token estimate from character length. Python's job, never the model's."""
    return max(1, len(item.get("text", "")) // CHARS_PER_TOKEN)


def batch(items: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for item in items:
        size = max(1, len(json.dumps(item)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(item)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(items: list[dict]) -> dict:
    """Five judgments per item, all in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(items)):
        reference = {"item": f"`items[{index}].text`",
                     "item_kind": f"`items[{index}].kind`",
                     "the_turn_to_answer": "`current_turn`"}
        for name, spec in PROPERTIES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "target": reference},
                true=spec["true"], false=spec["false"],
            )
        questions[f"loss_{index}"] = score(
            {"task": "What is lost if this item is removed from the context before the assistant replies?",
             "target": reference},
            LOSS_LEVELS,
        )
    return questions


def value(props: dict[str, float], loss: float, *, constraint_bonus: float) -> float:
    """Keep-worthiness. Weights are arguments, so tuning re-runs no inference."""
    base = loss * max(props["needed_now"], props["only_source"] * 0.6)
    base += constraint_bonus * props["binding_constraint"]
    return base * (1.0 - props["superseded"])


def select(rows: list[dict], budget_tokens: int, *, constraint_bonus: float,
           pin_gate: float) -> list[dict]:
    """Greedy pick by value density under a token budget.

    Pinned first: the system prompt, and anything that binds future turns. A
    standing constraint that loses a knapsack race to a big juicy tool output is
    how agents quietly forget they were told not to restart production.
    """
    scored = []
    for row in rows:
        row = {**row,
               "tokens": token_cost(row),
               "value": value(row["properties"], row["loss"], constraint_bonus=constraint_bonus)}
        row["pinned"] = (row.get("kind") in KINDS_PINNED
                         or (row["properties"]["binding_constraint"] >= pin_gate
                             and row["properties"]["superseded"] < 0.5))
        scored.append(row)

    kept: list[dict] = []
    used = 0
    for row in [r for r in scored if r["pinned"]]:
        kept.append(row)
        used += row["tokens"]

    candidates = sorted((r for r in scored if not r["pinned"]),
                        key=lambda r: -(r["value"] / r["tokens"]))
    for row in candidates:
        if used + row["tokens"] <= budget_tokens:
            kept.append(row)
            used += row["tokens"]

    order = {row["id"]: i for i, row in enumerate(rows)}
    kept.sort(key=lambda r: order[r["id"]])
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Select which context items survive a tight window.")
    parser.add_argument("--context", type=Path, help="JSON: {current_turn, items[]}")
    parser.add_argument("--budget", type=int, default=450, help="token budget for kept items")
    parser.add_argument("--constraint-bonus", type=float, default=1.5)
    parser.add_argument("--pin-gate", type=float, default=0.7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    data = load_context(args.context)
    items = data["items"]
    if not items:
        print("no context items", file=sys.stderr)
        return 2

    groups = batch(items, int(provider.context_tokens * 0.4))
    print(f"judging {len(items)} context items via {provider.name} "
          f"({len(groups)} request(s), {len(items) * (len(PROPERTIES) + 1)} questions)",
          file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                state = {"current_turn": data["current_turn"], "items": group}
                answers = client.ask(state, build_questions(group))
                for index, item in enumerate(group):
                    rows.append({
                        **item,
                        "properties": {n: answers.noul(f"{n}_{index}") for n in PROPERTIES},
                        "loss": answers.score(f"loss_{index}"),
                    })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    kept = select(rows, args.budget, constraint_bonus=args.constraint_bonus,
                  pin_gate=args.pin_gate)
    kept_ids = {row["id"] for row in kept}
    before = sum(token_cost(item) for item in items)
    after = sum(row["tokens"] for row in kept)

    if args.json:
        print(json.dumps({"kept": kept, "dropped_ids": [i["id"] for i in items
                                                        if i["id"] not in kept_ids],
                          "tokens_before": before, "tokens_after": after,
                          "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 0

    print(f"\n── kept ({len(kept)} items, ~{after} tokens of ~{args.budget} budget) ──")
    for row in kept:
        pin = "📌" if row["pinned"] else "  "
        print(f"{pin} {row['id']:<8} {row['kind']:<15} value {row['value']:.2f} "
              f"loss {row['loss']:.2f}  {row['tokens']:>4}t")
        print(f"     {row['text'][:100]}")

    print(f"\n── dropped ({len(items) - len(kept)} items) ──")
    for row in sorted((r for r in rows if r["id"] not in kept_ids),
                      key=lambda r: r["loss"]):
        print(f"   {row['id']:<8} {row['kind']:<15} loss {row['loss']:.2f}  {row['text'][:80]}")

    print(f"\n~{before} tokens -> ~{after} tokens kept, verbatim. Nothing was rewritten.")
    print(f"{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured")
    print("Budget and weights are Python arguments: re-run --budget on the same --json "
          "output without paying for inference again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
