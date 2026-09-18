#!/usr/bin/env python3
"""eval-judge -- score model outputs against a rubric, reproducibly.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --data my_eval.json --json

Exit code 1 if any output trips a hard gate.

Four rubric dimensions, four separate Scores. They are independent: an output
can follow the format perfectly and be factually wrong, or be flawlessly correct
and answer a question nobody asked. One blended "quality" rubric cannot express
that, and reweighting it means re-running the judge.

Two hard gates are Nouls rather than dimensions, because "it refused" and "it
broke an explicit format rule" are not degrees of quality -- they are conditions
that disqualify a run no matter how the dimensions land.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_eval.json"
CHARS_PER_TOKEN = 4

DIMENSIONS = {
    "instruction_following": {
        "task": "Does this output obey the explicit formatting and structure instructions in the task?",
        "levels": [
            "Ignores the stated format entirely and answers in a different shape",
            "Recognisably attempts the format but breaks at least one stated rule",
            "Follows the stated format with one small deviation a reviewer would let pass",
            "Matches every stated formatting and structure instruction exactly",
        ],
    },
    "factual_correctness": {
        "task": "Are the statements in this output consistent with the supplied policy brief?",
        "levels": [
            "Asserts something the brief directly contradicts",
            "Mixes correct statements with at least one the brief does not support",
            "Everything is supported by the brief, but one statement is stretched or vague",
            "Every statement is supported by the brief and stated precisely",
        ],
    },
    "completeness": {
        "task": "Does this output cover everything the question asked about?",
        "levels": [
            "Addresses none of what was asked, or answers a different question",
            "Addresses one part of the question and omits another part that was asked",
            "Addresses every part, but one of them thinly enough that the reader still has to ask",
            "Addresses every part of the question with enough detail to act on",
        ],
    },
    "directness": {
        "task": "Does this output answer the question without padding, hedging or filler?",
        "levels": [
            "Buries or avoids the answer under preamble, caveats or restated background",
            "Answers, but wrapped in meaningful filler the reader has to skip",
            "Mostly direct, with a small amount of unnecessary framing",
            "Every sentence carries information the reader asked for",
        ],
    },
}

GATES = {
    "refused": {
        "task": "Does this output decline to do the task rather than attempting it?",
        "true": "It redirects, defers to someone else, or states it cannot help, instead of answering",
        "false": "It attempts the task, even if the attempt is wrong or incomplete",
    },
    "format_violation": {
        "task": "Does this output break a formatting rule the task stated explicitly?",
        "true": "The task named a required shape and this output does not have it",
        "false": "The output has the shape the task asked for",
    },
}

MAX_LEVEL = 3.0


def load_data(path: Path | None) -> dict:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(outputs: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for item in outputs:
        size = max(1, len(json.dumps(item)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(item)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(outputs: list[dict]) -> dict:
    """Six judgments per output, all in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(outputs)):
        reference = {
            "candidate_output": f"`outputs[{index}].text`",
            "task_the_output_was_given": "`task`",
            "policy_brief_the_output_must_agree_with": "`brief`",
        }
        for name, spec in DIMENSIONS.items():
            questions[f"{name}_{index}"] = score(
                {"task": spec["task"], "subject": reference}, spec["levels"],
            )
        for name, spec in GATES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "subject": reference},
                true=spec["true"], false=spec["false"],
            )
    return questions


def aggregate(dimensions: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean on the 0..3 rubric scale. Arithmetic belongs in Python, so
    reweighting costs nothing and re-runs no inference."""
    divisor = sum(weights.values())
    if divisor <= 0:
        raise ValueError("weights must sum above zero")
    return sum(dimensions[name] * weights[name] for name in dimensions) / divisor


def grade(total: float, gates: dict[str, float], *, pass_score: float, gate_p: float) -> str:
    """'gated' beats 'fail' beats 'pass'. A tripped gate is disqualifying at any score."""
    if any(probability >= gate_p for probability in gates.values()):
        return "gated"
    return "pass" if total >= pass_score else "fail"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score model outputs against a rubric.")
    parser.add_argument("--data", type=Path, help="JSON: {task, brief, rubric, outputs[]}")
    parser.add_argument("--pass-score", type=float, default=2.0, help=f"0..{MAX_LEVEL}")
    parser.add_argument("--gate-p", type=float, default=0.60)
    for name in DIMENSIONS:
        parser.add_argument(f"--w-{name.replace('_', '-')}", type=float, default=1.0,
                            dest=f"w_{name}", help=f"weight for {name}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    weights = {name: getattr(args, f"w_{name}") for name in DIMENSIONS}

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    data = load_data(args.data)
    outputs = data["outputs"]
    if not outputs:
        print("no outputs to judge", file=sys.stderr)
        return 2

    groups = batch(outputs, int(provider.context_tokens * 0.4))
    per_output = len(DIMENSIONS) + len(GATES)
    print(f"judging {len(outputs)} outputs via {provider.name} "
          f"({len(groups)} request(s), {len(outputs) * per_output} questions)", file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                state = {"task": data["task"], "brief": data["brief"],
                         "rubric": data.get("rubric", {}), "outputs": group}
                answers = client.ask(state, build_questions(group))
                for index, item in enumerate(group):
                    dimensions = {name: answers.score(f"{name}_{index}") for name in DIMENSIONS}
                    gates = {name: answers.noul(f"{name}_{index}") for name in GATES}
                    total = aggregate(dimensions, weights)
                    rows.append({
                        "id": item.get("id", f"#{index}"),
                        "label": item.get("label", ""),
                        "dimensions": dimensions,
                        "gates": gates,
                        "total": total,
                        "verdict": grade(total, gates, pass_score=args.pass_score,
                                         gate_p=args.gate_p),
                    })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: (r["verdict"] == "gated", -r["total"]))
    gated = [r for r in rows if r["verdict"] == "gated"]

    if args.json:
        print(json.dumps({"rows": rows, "weights": weights, "cost_usd": cost,
                          "input_tokens": tokens}, indent=2))
        return 1 if gated else 0

    header = "  ".join(f"{name[:4]:>5}" for name in DIMENSIONS)
    print(f"\n{'':<8}{'score':>6}  {header}   verdict   label")
    for row in rows:
        cells = "  ".join(f"{row['dimensions'][name]:>5.2f}" for name in DIMENSIONS)
        mark = {"pass": "✅", "fail": "❌", "gated": "⛔"}[row["verdict"]]
        print(f"{mark} {row['id']:<6}{row['total']:>6.2f}  {cells}   "
              f"{row['verdict']:<9} {row['label']}")
        tripped = [name for name, p in row["gates"].items() if p >= args.gate_p]
        if tripped:
            print(f"     gate: {', '.join(tripped)} "
                  f"({', '.join(f'{k}={v:.2f}' for k, v in row['gates'].items())})")

    passed = sum(1 for r in rows if r["verdict"] == "pass")
    print(f"\n{passed} passed · {len(rows) - passed - len(gated)} below "
          f"{args.pass_score:.2f} · {len(gated)} gated")
    print(f"weights: {', '.join(f'{k}={v:g}' for k, v in weights.items())} "
          "-- change them and re-rank from the same --json, no new inference")
    print(f"{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(outputs)) * 1e6:,.2f} per million outputs judged")
    return 1 if gated else 0


if __name__ == "__main__":
    raise SystemExit(main())
