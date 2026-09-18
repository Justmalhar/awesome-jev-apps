#!/usr/bin/env python3
"""memory-filter -- which stored memories belong in the context for THIS turn.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --turn t-2 --budget 200 --json

Exit code 1 when the budget forced out a memory that was worth keeping, which is
the signal that the budget, not the ranking, is the constraint.

The whole memory store is ranked in ONE request. That is the point: an
embedding-plus-cross-encoder pipeline pays per pair, so ranking 20 memories
costs 20 scoring passes and ranking 2000 costs 2000. Here the store is state,
ingested once, and every memory's questions are answered against it in parallel.

Four properties, four Nouls, because they are independent. A standing preference
("British English") is always relevant and would rarely change the substance of
a reply. A superseded fact is highly relevant and actively harmful. One
relevance score cannot hold both of those apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_memory.json"
CHARS_PER_TOKEN = 4

PROPERTIES = {
    "relevant": {
        "task": "Is this stored memory about the same subject as the user's current message?",
        "true": "It concerns the system, decision, person or preference the current message is about",
        "false": "It is about something else entirely",
    },
    "would_change_reply": {
        "task": "Would a reply written without this memory be materially different from one written with it?",
        "true": "Knowing this changes what the assistant should recommend, assume or avoid",
        "false": "The reply comes out the same whether or not the assistant knows this",
    },
    "superseded": {
        "task": "Does a later memory in this store replace what this one says?",
        "true": "Another stored memory states a newer decision or fact that overrides this one",
        "false": "Nothing else in the store overrides it",
    },
    "standing_preference": {
        "task": "Is this a standing instruction about how the user wants to be worked with, rather than a fact about a subject?",
        "true": "It states an ongoing preference for style, tooling or process that applies to any reply",
        "false": "It records a fact, an event or a one-off request",
    },
}

OMISSION_LEVELS = [
    "Leaving this out changes nothing about the reply",
    "Leaving this out makes the reply slightly less tailored, and the user would not notice",
    "Leaving this out makes the assistant give advice the user has already rejected or ruled out",
    "Leaving this out makes the assistant contradict a hard constraint the user depends on",
]


def load_store(path: Path | None) -> dict:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def pick_turn(store: dict, turn_id: str | None) -> dict:
    turns = store["turns"]
    if turn_id is None:
        return turns[0]
    for turn in turns:
        if turn.get("id") == turn_id:
            return turn
    raise KeyError(f"no turn {turn_id!r}; store has {[t.get('id') for t in turns]}")


def tokens_of(memory: dict) -> int:
    """Estimated context cost of carrying one memory. Python's job, not the model's."""
    return max(1, len(memory.get("text", "")) // CHARS_PER_TOKEN)


def build_questions(memories: list[dict]) -> dict:
    """Five judgments per memory, the entire store in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(memories)):
        reference = {
            "memory": f"`memories[{index}].text`",
            "current_message": "`current_turn`",
            "other_stored_memories": "`memories`",
        }
        for name, spec in PROPERTIES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "subject": reference},
                true=spec["true"], false=spec["false"],
            )
        questions[f"omission_{index}"] = score(
            {"task": "What happens to the assistant's reply if this memory is left out of its context?",
             "subject": reference},
            OMISSION_LEVELS,
        )
    return questions


def rank_of(props: dict[str, float], omission: float, *, standing_bonus: float) -> float:
    """Blend the properties into one ordering. Weights live here so retuning
    costs nothing, and the rank is only ever used for ordering."""
    base = (props["relevant"] + props["would_change_reply"]) / 2 + omission / 3
    if props["standing_preference"] >= 0.5:
        base += standing_bonus
    return base


def select(rows: list[dict], *, budget_tokens: int, min_rank: float,
           superseded_p: float) -> tuple[list[dict], list[dict]]:
    """Greedy fill under a token budget, highest rank first. Returns (kept, dropped)
    with a reason on each dropped row."""
    ordered = sorted(rows, key=lambda r: -r["rank"])
    kept: list[dict] = []
    dropped: list[dict] = []
    used = 0
    for row in ordered:
        if row["properties"]["superseded"] >= superseded_p:
            dropped.append({**row, "reason": "superseded"})
        elif row["rank"] < min_rank:
            dropped.append({**row, "reason": "below-rank"})
        elif used + row["tokens"] > budget_tokens:
            dropped.append({**row, "reason": "no-budget"})
        else:
            kept.append(row)
            used += row["tokens"]
    return kept, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="Pick the memories worth putting in context.")
    parser.add_argument("--store", type=Path, help="JSON: {memories[], turns[]}")
    parser.add_argument("--turn", help="id of the turn to filter for (default: the first)")
    parser.add_argument("--budget", type=int, default=120, help="token budget for memories")
    parser.add_argument("--min-rank", type=float, default=0.55)
    parser.add_argument("--standing-bonus", type=float, default=0.45)
    parser.add_argument("--superseded-p", type=float, default=0.60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    store = load_store(args.store)
    memories = store["memories"]
    try:
        turn = pick_turn(store, args.turn)
    except KeyError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if not memories:
        print("empty memory store", file=sys.stderr)
        return 2

    print(f"ranking {len(memories)} memories for turn {turn.get('id')} via {provider.name} "
          f"(1 request, {len(memories) * (len(PROPERTIES) + 1)} questions)", file=sys.stderr)

    try:
        with JevClient(provider=provider) as client:
            state = {"current_turn": turn["text"], "memories": memories}
            answers = client.ask(state, build_questions(memories))
            rows = []
            for index, memory in enumerate(memories):
                props = {name: answers.noul(f"{name}_{index}") for name in PROPERTIES}
                omission = answers.score(f"omission_{index}")
                rows.append({
                    **memory,
                    "properties": props,
                    "omission": omission,
                    "tokens": tokens_of(memory),
                    "rank": rank_of(props, omission, standing_bonus=args.standing_bonus),
                })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    kept, dropped = select(rows, budget_tokens=args.budget, min_rank=args.min_rank,
                           superseded_p=args.superseded_p)
    squeezed = [r for r in dropped if r["reason"] == "no-budget"]
    used = sum(r["tokens"] for r in kept)

    if args.json:
        print(json.dumps({"turn": turn, "kept": kept, "dropped": dropped,
                          "budget_tokens": args.budget, "used_tokens": used,
                          "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if squeezed else 0

    print(f"\nturn {turn.get('id')}: {turn['text']}")
    print(f"\n── kept ({used}/{args.budget} tokens) ──")
    for row in kept:
        mark = "📌" if row["properties"]["standing_preference"] >= 0.5 else "  "
        print(f"{mark} {row['id']}  rank {row['rank']:.2f}  omit-cost {row['omission']:.2f}  "
              f"{row['tokens']:>3}tok  {row['text'][:78]}")
    print("\n── dropped ──")
    for row in sorted(dropped, key=lambda r: -r["rank"]):
        print(f"   {row['id']}  rank {row['rank']:.2f}  {row['reason']:<11} {row['text'][:70]}")

    print(f"\n{len(kept)} kept · {len(dropped)} dropped "
          f"({len(squeezed)} of them only for lack of budget)")
    print(f"{calls} request ranking the WHOLE store · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(memories)) * 1e6:,.2f} per million memories ranked")
    if squeezed:
        print(f"Budget-bound: raise --budget above {args.budget} to keep "
              f"{', '.join(r['id'] for r in squeezed)}.")
    return 1 if squeezed else 0


if __name__ == "__main__":
    raise SystemExit(main())
