#!/usr/bin/env python3
"""agent-trace-classifier -- why did these agent runs fail? Map-reduce over a
pile of traces nobody has time to read.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --traces my_traces.json --json

Exit code 1 if any single failure cause dominates the run set, which is the
signal worth paging someone about.

MAP: every trace gets one Choice over failure causes plus three independent
Nouls. Traces are chunked only because state has to fit the provider's context
window -- that is one logical request split by size, not a cascade.

REDUCE: Python groups and tallies the map results (jev-1.13 cannot count, so
this was never the model's job), then ONE final request asks which remediation
would prevent each of the largest clusters. That second request is justified on
the same grounds as `agent-tool-router`: the first round's answers decide WHICH
traces get sent in the second, so it cannot be folded into the first call.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, choice  # noqa: E402

SAMPLE = HERE / "sample_traces.json"
CHARS_PER_TOKEN = 4

CAUSES = {
    "tool_error_ignored": "A tool returned an explicit error and the agent carried on as if it had succeeded.",
    "empty_result_invented": "A tool returned nothing, and the agent supplied an answer it had no source for.",
    "wrong_tool_or_plan": "The agent chose an approach or a tool that could not have achieved the goal, even executed perfectly.",
    "retry_loop": "The agent repeated the same failing call without changing anything about it.",
    "missing_permission": "The run was stopped by a credential, scope or approval the agent did not hold.",
    "ambiguous_goal": "The request did not determine what success meant, and the agent guessed instead of asking.",
    "lost_constraint": "A requirement stated earlier in the run was no longer honoured by the time the agent acted.",
    "unclear": "None of these describe this run, or the trace does not show enough to tell.",
}

REMEDIES = {
    "validate_tool_results": "Make the agent check each tool result before using it, and stop the run on an error.",
    "forbid_unsourced_answers": "Require a retrieved source for any factual claim, and return an explicit 'not found' otherwise.",
    "improve_tool_selection": "Rewrite tool descriptions or add a routing step so the right tool is chosen up front.",
    "add_retry_budget": "Cap identical retries and force a different action or an escalation after the cap.",
    "fix_credentials": "Grant the missing scope up front, or fail fast with a clear message when it is absent.",
    "ask_before_acting": "Make the agent ask a clarifying question when the goal does not determine success.",
    "pin_constraints": "Keep the user's stated constraints in a structured slot that survives context truncation.",
    "none": "No single change would prevent this class; these runs failed for unrelated reasons.",
}

FLAGS = {
    "recoverable_by_retry": {
        "task": "Would running this same request again, unchanged, plausibly have succeeded?",
        "true": "The failure came from a transient condition that a later attempt would not hit",
        "false": "The same run would fail the same way every time",
    },
    "agent_noticed": {
        "task": "Did the agent recognise that something had gone wrong?",
        "true": "The agent reported the problem, retried deliberately, or stopped rather than claiming success",
        "false": "The agent presented a failed or empty result as a completed task",
    },
    "root_cause_outside_agent": {
        "task": "Does the root cause sit outside the agent's own reasoning?",
        "true": "A tool, service, permission or upstream data problem caused this, and better reasoning would not have prevented it",
        "false": "The agent's own planning, tool choice or interpretation caused this",
    },
}


def load_traces(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(traces: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for trace in traces:
        size = max(1, len(json.dumps(trace)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(trace)
        used += size
    if current:
        groups.append(current)
    return groups


def build_map_questions(traces: list[dict]) -> dict:
    """Four judgments per trace, batched into one request per chunk."""
    questions: dict[str, dict] = {}
    for index in range(len(traces)):
        reference = {
            "goal": f"`traces[{index}].goal`",
            "steps": f"`traces[{index}].steps`",
            "final_state": f"`traces[{index}].final_state`",
            "note": "steps are the tool calls the agent made, in order, with what each returned",
        }
        questions[f"cause_{index}"] = choice(
            {"task": "What is the single root cause of this failed agent run?",
             "run": reference,
             "note": "Choose unclear when no option fits or the trace is too thin to tell."},
            CAUSES,
        )
        for name, spec in FLAGS.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "run": reference},
                true=spec["true"], false=spec["false"],
            )
    return questions


def cluster(rows: list[dict]) -> list[dict]:
    """Group, tally and order the map output. Arithmetic belongs here, in Python."""
    tally = Counter(row["cause"] for row in rows)
    clusters = []
    for cause, size in tally.most_common():
        members = [row for row in rows if row["cause"] == cause]
        clusters.append({
            "cause": cause,
            "size": size,
            "share": size / len(rows),
            "mean_confidence": sum(m["confidence"] for m in members) / size,
            "members": [m["id"] for m in members],
            # Highest-confidence members represent the cluster in the reduce step.
            "exemplars": [m["id"] for m in sorted(members, key=lambda m: -m["confidence"])[:3]],
        })
    return clusters


def build_reduce_questions(clusters: list[dict]) -> dict:
    """One Choice per cluster, all in ONE request."""
    questions = {}
    for position, group in enumerate(clusters):
        questions[f"remedy_{position}"] = choice(
            {"task": "Which single change to this agent would prevent this group of failures from recurring?",
             "failure_cause": CAUSES[group["cause"]],
             "example_runs": f"`clusters[{position}].traces`",
             "note": "Choose none when no single change would cover these runs."},
            REMEDIES,
        )
    return questions


def decide_alert(clusters: list[dict], *, alert_share: float) -> bool:
    """A single cause eating this much of the run set is worth paging someone."""
    return any(c["share"] >= alert_share and c["cause"] != "unclear" for c in clusters)


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify why agent runs failed, across many traces.")
    parser.add_argument("--traces", type=Path, help="JSON list of {id, goal, steps, final_state}")
    parser.add_argument("--clusters", type=int, default=3, help="clusters that reach the reduce step")
    parser.add_argument("--alert-share", type=float, default=0.25,
                        help="fraction of runs in one cause that triggers exit code 1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    traces = load_traces(args.traces)
    if not traces:
        print("no traces to classify", file=sys.stderr)
        return 2

    groups = batch(traces, int(provider.context_tokens * 0.4))
    print(f"map: {len(traces)} traces via {provider.name} "
          f"({len(groups)} chunk(s), {len(traces) * (1 + len(FLAGS))} questions)", file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                answers = client.ask({"traces": group}, build_map_questions(group))
                for index, trace in enumerate(group):
                    rows.append({
                        "id": trace.get("id", f"#{index}"),
                        "goal": trace.get("goal", ""),
                        "cause": answers.choice(f"cause_{index}"),
                        "confidence": answers.confidence(f"cause_{index}"),
                        "flags": {name: answers.noul(f"{name}_{index}") for name in FLAGS},
                    })

            clusters = cluster(rows)
            top = [c for c in clusters if c["cause"] != "unclear"][: args.clusters]

            # Reduce. The state here is chosen BY the map answers -- the one
            # documented reason a second request is legitimate.
            if top:
                by_id = {t.get("id"): t for t in traces}
                state = {"clusters": [
                    {"traces": [by_id[i] for i in group["exemplars"] if i in by_id]}
                    for group in top
                ]}
                remedy_answers = client.ask(state, build_reduce_questions(top))
                for position, group in enumerate(top):
                    group["remedy"] = remedy_answers.choice(f"remedy_{position}")
                    group["remedy_confidence"] = remedy_answers.confidence(f"remedy_{position}")

            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    alert = decide_alert(clusters, alert_share=args.alert_share)

    if args.json:
        print(json.dumps({"traces": rows, "clusters": clusters, "alert": alert,
                          "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if alert else 0

    print(f"\n── {len(traces)} failed runs, {len(clusters)} distinct causes ──")
    for group in clusters:
        bar = "█" * int(group["share"] * 30)
        print(f"  {group['cause']:<24} {group['size']:>3} runs  {group['share']:>5.0%} {bar}")
        print(f"      {', '.join(group['members'])}")
        if "remedy" in group:
            print(f"      → fix: {group['remedy']} (confidence {group['remedy_confidence']:.2f})")
            print(f"        {REMEDIES[group['remedy']]}")

    silent = [r for r in rows if r["flags"]["agent_noticed"] < 0.5]
    external = [r for r in rows if r["flags"]["root_cause_outside_agent"] >= 0.5]
    retryable = [r for r in rows if r["flags"]["recoverable_by_retry"] >= 0.5]
    print(f"\n{len(silent)} runs failed silently · {len(external)} had an external root cause · "
          f"{len(retryable)} would plausibly pass on a retry")
    if silent:
        print(f"  silent failures: {', '.join(r['id'] for r in silent)}")

    print(f"\n{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(traces)) * 1e6:,.2f} per million traces classified")
    if alert:
        print(f"ALERT: one cause covers at least {args.alert_share:.0%} of the runs.")
    return 1 if alert else 0


if __name__ == "__main__":
    raise SystemExit(main())
