#!/usr/bin/env python3
"""tool-output-validator -- did this tool actually return what was asked for?

    uv run --with httpx python app.py
    uv run --with httpx python app.py --calls my_calls.json --json

Exit code 1 if any call needs escalation.

Agents ignore silent tool failures. A `200 OK` with `{"results": []}`, an HTTP
error page returned as a document, a query that quietly answered a different
question than the one asked -- none of these raise, so the agent proceeds as if
it has an answer and confabulates the rest. Schema validation does not help:
every one of these examples is valid JSON of the expected shape.

The judgment that catches them is semantic, and it is several independent
judgments rather than one: an output can be well-formed, non-empty, on-schema,
about the right entity, and still answer a different question.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_calls.json"
CHARS_PER_TOKEN = 4

PROPERTIES = {
    "answers_intent": {
        "task": "Does this tool output supply what the caller was trying to find out?",
        "true": "The caller can act on this output without another call",
        "false": "The caller still does not have the thing they asked for",
    },
    "error_in_disguise": {
        "task": "Is this output reporting a failure while presenting itself as a success?",
        "true": "It carries a timeout, a retry notice, a sign-in page, or a message saying the operation could not be completed",
        "false": "It is a genuine result, including a genuine result that happens to be empty",
    },
    "wrong_subject": {
        "task": "Is this output about a different entity, filter, period or grouping than the call asked for?",
        "true": "The parameters in the output do not match what the call requested",
        "false": "The output is about exactly what was requested",
    },
    "missing_requested_field": {
        "task": "Was the specific detail the caller wanted left out of this output?",
        "true": "The record came back but the particular field the caller needed is absent",
        "false": "The detail the caller needed is present, or the record itself did not exist",
    },
    "empty_but_valid": {
        "task": "Is this a well-formed result that simply contains nothing?",
        "true": "The tool worked and honestly reports that there is nothing matching",
        "false": "There is content, or the tool did not work",
    },
}

RISK_LEVELS = [
    "An agent proceeding on this output reaches a correct conclusion",
    "An agent proceeding on this output produces a vague or incomplete reply",
    "An agent proceeding on this output states something false to the user",
    "An agent proceeding on this output takes a wrong action on a real system",
]

VERDICTS = ("accept", "empty", "retry", "escalate")


def load_calls(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(calls: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for call in calls:
        size = max(1, len(json.dumps(call)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(call)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(calls: list[dict]) -> dict:
    """Six judgments per call, all in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(calls)):
        reference = {"what_the_caller_wanted": f"`calls[{index}].intent`",
                     "the_call_made": f"`calls[{index}].call`",
                     "what_came_back": f"`calls[{index}].output`"}
        for name, spec in PROPERTIES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "tool_call": reference},
                true=spec["true"], false=spec["false"],
            )
        questions[f"risk_{index}"] = score(
            {"task": "What happens if an agent treats this output as a valid answer and carries on?",
             "tool_call": reference},
            RISK_LEVELS,
        )
    return questions


def decide(props: dict[str, float], risk: float, *, gate: float, risk_gate: float) -> str:
    """accept / empty / retry / escalate. Policy in Python, tunable for free."""
    if props["error_in_disguise"] >= gate:
        return "retry"
    if props["wrong_subject"] >= gate:
        return "escalate"
    if props["answers_intent"] >= gate:
        return "accept"
    if props["empty_but_valid"] >= gate:
        # An honest empty is a real answer -- unless acting on it is dangerous.
        return "escalate" if risk >= risk_gate else "empty"
    return "escalate" if risk >= risk_gate else "retry"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate that tool outputs answered the call.")
    parser.add_argument("--calls", type=Path, help="JSON list of {id, intent, call, output}")
    parser.add_argument("--gate", type=float, default=0.60, help="probability gate for every flag")
    parser.add_argument("--risk-gate", type=float, default=2.0,
                        help=f"0..{len(RISK_LEVELS) - 1}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    calls = load_calls(args.calls)
    if not calls:
        print("no tool calls to validate", file=sys.stderr)
        return 2

    groups = batch(calls, int(provider.context_tokens * 0.5))
    print(f"validating {len(calls)} tool calls via {provider.name} "
          f"({len(groups)} request(s), {len(calls) * (len(PROPERTIES) + 1)} questions)",
          file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                answers = client.ask({"calls": group}, build_questions(group))
                for index, call in enumerate(group):
                    props = {name: answers.noul(f"{name}_{index}") for name in PROPERTIES}
                    risk = answers.score(f"risk_{index}")
                    rows.append({**call, "properties": props, "risk": risk,
                                 "verdict": decide(props, risk, gate=args.gate,
                                                   risk_gate=args.risk_gate)})
            cost, tokens, calls_made = (client.total_cost_usd, client.total_input_tokens,
                                        client.calls)
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"rows": rows, "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if any(r["verdict"] == "escalate" for r in rows) else 0

    marks = {"accept": "✅", "empty": "📭", "retry": "🔁", "escalate": "🚨"}
    for row in rows:
        print(f"{marks[row['verdict']]} {row['id']}  {row['verdict']:<9} "
              f"risk {row['risk']:.2f}/{len(RISK_LEVELS) - 1}")
        print(f"     wanted: {row['intent'][:92]}")
        print(f"     got   : {row['output'][:92]}")
        if row["verdict"] != "accept":
            print(f"     {' '.join(f'{k}={v:.2f}' for k, v in row['properties'].items())}")

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    print(f"\n{counts['accept']} accepted · {counts['empty']} honestly empty · "
          f"{counts['retry']} retry · {counts['escalate']} escalate")
    print(f"{calls_made} request(s) · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(calls)) * 1e6:,.2f} per million tool calls validated")
    return 1 if counts["escalate"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
