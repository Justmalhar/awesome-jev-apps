#!/usr/bin/env python3
"""text-adventure -- a referee that judges improvised actions, in a game loop.

    uv run --with httpx python adventure.py                      # interactive
    uv run --with httpx python adventure.py --script sample_playthrough.txt
    uv run --with httpx python adventure.py --script sample_playthrough.txt --json

The player types whatever they want. ONE request per turn asks whether that
action plausibly works in this scene, what it operates on, and whether it hurts.
Jev does NOT write the story -- it returns a judgment, and Python narrates from
a table. The world rules (inventory, exits, locks) are Python's, always: the
model supplies common sense, not bookkeeping.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, choice, load_provider, noul  # noqa: E402

WORLD = json.loads((HERE / "sample_world.json").read_text(encoding="utf-8"))
NO_TARGET = "nothing"
UNCLEAR = "unclear"

RESOLUTIONS = {
    "succeeds": "What the player described would plainly work with what is present in this scene.",
    "blocked": "Reasonable here, but something needed for it is missing, out of reach, or still locked.",
    "impossible": "Nothing in this scene could make it work, however determined the player is.",
    "no_effect": "It would happen, and the scene would be exactly as it was afterwards.",
    UNCLEAR: "The typed text is not an attempted action in this scene at all.",
}

# Python writes the prose. The model picked a branch; these are the branches.
NARRATION = {
    "succeeds": "That works.",
    "blocked": "You get partway, and stop. Something you need is missing.",
    "impossible": "No. Not here, not with what you have.",
    "no_effect": "You do it. Nothing about the room is different afterwards.",
    UNCLEAR: "Nothing about that is something you can do here.",
}


def scene_state(state: dict, action: str) -> dict:
    """The whole per-turn state: one room, one inventory, one typed line."""
    room = WORLD["rooms"][state["room"]]
    return {
        "room_name": room["name"],
        "scene": room["description"],
        "visible_objects": room["objects"],
        "carrying": state["inventory"] or ["nothing at all"],
        "player_action": action,
    }


def build_questions(state: dict, action: str) -> dict:
    """ONE request per turn: does it work, what does it touch, does it hurt."""
    room = WORLD["rooms"][state["room"]]
    targets = {name: text for name, text in room["objects"].items()}
    for item in state["inventory"]:
        targets.setdefault(item, "Carried by the player.")
    targets[NO_TARGET] = "The action does not operate on any object listed here."

    return {
        "resolution": choice(
            {
                "task": "Judge whether the player's action would work in this scene.",
                "scene": "`scene`",
                "carrying": "`carrying`",
                "player_action": "`player_action`",
                "note": f"Choose '{UNCLEAR}' when the text is not an attempted action.",
            },
            RESOLUTIONS,
        ),
        "target": choice(
            {
                "task": "Which object does the player's action operate on?",
                "player_action": "`player_action`",
                "note": f"Choose '{NO_TARGET}' when it operates on no listed object.",
            },
            targets,
        ),
        # Independent of whether it works -- a successful action can still hurt.
        "dangerous": noul(
            {
                "task": "Would attempting this action plausibly injure the player?",
                "scene": "`scene`",
                "player_action": "`player_action`",
            },
            true="Sharp edges, a fall, fire, or force applied to the player's own body",
            false="Nothing about it would leave a mark",
        ),
    }


def apply_turn(state: dict, resolution: str, target: str, dangerous: float, hurt_at: float) -> tuple[dict, list[str]]:
    """World rules are Python's. Jev judged plausibility; this owns the state.

    Returns the new state and the narration lines. Note the ordering: a lock is
    checked HERE, so the model can never talk its way through a locked door by
    being confident, and a wrong `target` can never invent an item.
    """
    room = WORLD["rooms"][state["room"]]
    new_state = {"room": state["room"], "inventory": list(state["inventory"]), "scrapes": state["scrapes"]}
    lines = [NARRATION[resolution]]

    if resolution == "succeeds":
        exit_spec = room["exits"].get(target)
        if exit_spec is not None:
            required = exit_spec["requires"]
            if required and required not in new_state["inventory"]:
                lines = [f"You try, but the {target} does not give. You need something to lever it."]
            else:
                new_state["room"] = exit_spec["to"]
                lines = [f"The {target} gives way.", WORLD["rooms"][exit_spec["to"]]["description"]]
        elif target in room["takeable"] and target not in new_state["inventory"]:
            new_state["inventory"].append(target)
            lines = [f"You take the {target}."]
        elif target in new_state["inventory"]:
            lines = [f"You use the {target}. It stays in your hands."]

    if dangerous >= hurt_at:
        new_state["scrapes"] += 1  # arithmetic is Python's, always
        lines.append(f"It costs you skin. ({new_state['scrapes']} scrape(s) so far.)")

    return new_state, lines


def play(client: JevClient, actions: list[str] | None, hurt_at: float, as_json: bool) -> int:
    state = {"room": WORLD["start"], "inventory": [], "scrapes": 0}
    latencies: list[float] = []
    records: list[dict] = []
    scripted = list(actions or [])

    if not as_json:
        print(WORLD["rooms"][state["room"]]["description"] + "\n")

    while True:
        if scripted:
            action = scripted.pop(0)
            if not as_json:
                print(f"> {action}")
        elif actions is not None:
            break
        else:
            try:
                action = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if action.lower() in {"quit", "exit", ""}:
                break

        try:
            answers = client.ask(scene_state(state, action), build_questions(state, action))
        except JevError as exc:
            print(f"request failed: {exc}", file=sys.stderr)
            return 1

        resolution = answers.choice("resolution")
        target = answers.choice("target")
        dangerous = answers.noul("dangerous")
        state, lines = apply_turn(state, resolution, target, dangerous, hurt_at)
        latencies.append(answers.elapsed_s * 1000)
        records.append(
            {
                "action": action,
                "resolution": resolution,
                "target": target,
                "dangerous": round(dangerous, 3),
                "room": state["room"],
                "ms": round(latencies[-1]),
                "narration": lines,
            }
        )

        if not as_json:
            for line in lines:
                print(f"  {line}")
            print(
                f"  [{resolution} on {target} · danger {dangerous:.2f} · "
                f"{latencies[-1]:.0f} ms · in {WORLD['rooms'][state['room']]['name']}]\n"
            )

    if not latencies:
        print("no turns played", file=sys.stderr)
        return 0

    summary = {
        "turns": len(latencies),
        "median_ms": round(statistics.median(latencies)),
        "slowest_ms": round(max(latencies)),
        "total_cost_usd": round(client.total_cost_usd, 6),
        "input_tokens": client.total_input_tokens,
        "final_room": state["room"],
        "inventory": state["inventory"],
    }
    if as_json:
        print(json.dumps({"turns": records, "summary": summary}, indent=2))
    else:
        print(
            f"{summary['turns']} turns · 3 questions each · median {summary['median_ms']} ms · "
            f"slowest {summary['slowest_ms']} ms · {summary['input_tokens']:,} tokens · "
            f"${summary['total_cost_usd']:.6f}"
        )
        print("A referee that answers inside a turn is a referee the player never waits for.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A text adventure refereed by Jev.")
    parser.add_argument("--script", type=Path, help="file of actions, one per line")
    parser.add_argument("--hurt-threshold", type=float, default=0.7)
    parser.add_argument("--json", action="store_true", help="emit the whole run as JSON")
    args = parser.parse_args()

    actions = None
    if args.script:
        path = args.script if args.script.is_absolute() else HERE / args.script
        actions = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"refereed by {provider.name} / {provider.model}\n")
    with JevClient(provider=provider) as client:
        return play(client, actions, args.hurt_threshold, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
