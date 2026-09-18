#!/usr/bin/env python3
"""meeting-copilot -- flag commitments and dodged questions DURING the call.

    uv run --with httpx python copilot.py
    uv run --with httpx python copilot.py --speed 0 --json     # as fast as it will go

One request per utterance, four independent Nouls. The point is the comparison
the footer prints: the judgment lands in a fraction of the time the sentence
took to say, so the flag appears while the speaker is still talking rather than
in an email the next morning.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul  # noqa: E402

TRANSCRIPT = json.loads((HERE / "sample_transcript.json").read_text(encoding="utf-8"))
WORDS_PER_MINUTE = 150.0

FLAGS = {
    "commitment": ("commitment", "📌"),
    "dodges_question": ("dodged question", "🚩"),
    "decision": ("decision", "✅"),
    "raises_blocker": ("blocker", "⚠️"),
}


def speaking_seconds(text: str, wpm: float = WORDS_PER_MINUTE) -> float:
    """How long this line takes to say aloud. Python's arithmetic, not the model's."""
    return max(len(text.split()) / wpm * 60.0, 0.4)


def build_questions(previous: dict | None, current: dict) -> dict:
    """ONE request per utterance. Four properties that can all be true at once."""
    return {
        "commitment": noul(
            {
                "task": "Does the speaker commit to doing something themselves?",
                "utterance": "`utterance.text`",
            },
            true="Takes on a specific piece of work or an action they will personally do",
            false="Describes work in general, agrees with someone, or commits nobody in particular",
        ),
        "dodges_question": noul(
            {
                "task": "Does this reply avoid answering the question that was just asked?",
                "question_asked": "`previous.text`",
                "reply": "`utterance.text`",
            },
            true="Fills the space with context, deflection, or reassurance instead of the answer asked for",
            false="Answers it, or honestly says they do not know, or the previous line asked nothing",
        ),
        "decision": noul(
            {
                "task": "Does this utterance settle a decision for the group?",
                "utterance": "`utterance.text`",
            },
            true="States a choice the group is now going with, closing off the alternative",
            false="Explores options, gives an opinion, or asks what everyone thinks",
        ),
        "raises_blocker": noul(
            {
                "task": "Does the speaker raise something that could stop or delay the work?",
                "utterance": "`utterance.text`",
            },
            true="Names a bug, dependency, gap in ownership, or risk that is currently unresolved",
            false="Reports progress, or mentions a problem that has already been handled",
        ),
    }


def flags_for(signals: dict, threshold: float) -> list[str]:
    """Policy in Python: which flags to raise at this threshold."""
    return [FLAGS[qid][0] for qid, probability in signals.items() if probability >= threshold]


def main() -> int:
    parser = argparse.ArgumentParser(description="Flag commitments and dodges during a call.")
    parser.add_argument("--speed", type=float, default=6.0,
                        help="replay speed multiplier; 0 replays with no pacing at all")
    parser.add_argument("--threshold", type=float, default=0.7, help="P at which a flag is raised")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"{TRANSCRIPT['meeting']} · listening via {provider.name}\n")

    utterances = TRANSCRIPT["utterances"]
    records: list[dict] = []
    latencies: list[float] = []

    try:
        with JevClient(provider=provider) as client:
            for index, utterance in enumerate(utterances):
                previous = utterances[index - 1] if index else {"speaker": "", "text": ""}
                spoken_s = speaking_seconds(utterance["text"])
                if args.speed > 0:
                    time.sleep(spoken_s / args.speed)

                answers = client.ask(
                    {"previous": previous, "utterance": utterance},
                    build_questions(previous, utterance),
                )
                signals = {qid: answers.noul(qid) for qid in FLAGS}
                raised = flags_for(signals, args.threshold)
                latencies.append(answers.elapsed_s * 1000)
                records.append(
                    {
                        "speaker": utterance["speaker"],
                        "text": utterance["text"],
                        "flags": raised,
                        "signals": {k: round(v, 3) for k, v in signals.items()},
                        "ms": round(latencies[-1]),
                        "spoken_ms": round(spoken_s * 1000),
                    }
                )

                if not args.json:
                    marks = " ".join(
                        icon for qid, (label, icon) in FLAGS.items() if label in raised
                    )
                    print(f"{utterance['speaker']:>6}: {utterance['text']}")
                    print(
                        f"        {marks + '  ' if marks else ''}"
                        f"[{', '.join(raised) or 'nothing flagged'} · {latencies[-1]:.0f} ms "
                        f"of a {spoken_s * 1000:.0f} ms utterance]\n"
                    )
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    commitments = [r for r in records if "commitment" in r["flags"]]
    dodges = [r for r in records if "dodged question" in r["flags"]]
    summary = {
        "utterances": len(records),
        "median_ms": round(statistics.median(latencies)),
        "slowest_ms": round(max(latencies)),
        "input_tokens": client.total_input_tokens,
        "cost_usd": round(client.total_cost_usd, 6),
        "commitments": [f"{r['speaker']}: {r['text']}" for r in commitments],
        "dodges": [f"{r['speaker']}: {r['text']}" for r in dodges],
    }

    if args.json:
        print(json.dumps({"utterances": records, "summary": summary}, indent=2))
        return 0

    print("── commitments made ──")
    for line in summary["commitments"] or ["(none)"]:
        print(f"  📌 {line}")
    print("\n── questions dodged ──")
    for line in summary["dodges"] or ["(none)"]:
        print(f"  🚩 {line}")
    spoken_total = sum(r["spoken_ms"] for r in records)
    print(
        f"\n{summary['utterances']} utterances · 4 questions each · "
        f"median {summary['median_ms']} ms · slowest {summary['slowest_ms']} ms · "
        f"{summary['input_tokens']:,} tokens · ${summary['cost_usd']:.6f}"
    )
    print(
        f"Judgment time was {sum(latencies):.0f} ms against {spoken_total} ms of speech — "
        f"the flag is on screen before the speaker finishes the sentence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
