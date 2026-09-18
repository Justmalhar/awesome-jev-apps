#!/usr/bin/env python3
"""alert-router -- page, ticket, or ignore, decided INSIDE the alerting path.

    uv run --with httpx python router.py
    uv run --with httpx python router.py --budget-ms 400 --json

Every alert gets ONE request with three questions. The exit code gates on the
measured p95 latency against a stated budget, because the only honest way to
put a judgment in the alerting path is to state what delay you are adding and
then fail the build when you exceed it.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, choice, load_provider, noul  # noqa: E402

ALERTS = json.loads((HERE / "sample_alerts.json").read_text(encoding="utf-8"))

ROUTES = {
    "page": "Someone has to be woken up: it is degrading now and waiting until morning makes it worse.",
    "ticket": "Real work that a human should do, but it can wait for the next working day.",
    "ignore": "Nothing here needs a human at all; recording it is enough.",
    "unclear": "The alert text does not say enough to place it in any of these.",
}


def build_questions(alert: dict) -> dict:
    """ONE request per alert. One routing Choice, two independent Nouls."""
    return {
        "route": choice(
            {
                "task": "How should this alert be handled right now?",
                "service": "`alert.service`",
                "environment": "`alert.env`",
                "alert": "`alert.summary`",
                "note": "Choose 'unclear' when the alert text does not say enough to decide.",
            },
            ROUTES,
        ),
        # Independent of the route, and the one signal allowed to override it.
        "customer_impact": noul(
            {
                "task": "Are customers outside the company being affected right now?",
                "alert": "`alert.summary`",
            },
            true="People using the product are currently failing to do something they came to do",
            false="Internal tooling, a test environment, or a problem with no user-visible effect yet",
        ),
        "self_healing": noul(
            {
                "task": "Has this already recovered, or does it recover on its own?",
                "alert": "`alert.summary`",
            },
            true="It resolved by itself, retries on a schedule, or a replica absorbed it",
            false="It is ongoing and will stay broken until somebody acts",
        ),
    }


def decide(route: str, customer_impact: float, self_healing: float,
           impact_at: float, healed_at: float) -> str:
    """Final routing policy, in Python. Order matters and is deliberate.

    Live customer impact outranks the model's own route -- a Choice that lands on
    'ticket' while customers are locked out is the failure that costs you an
    incident, so Python takes that decision away from it.
    """
    if customer_impact >= impact_at and self_healing < healed_at:
        return "page"
    if self_healing >= healed_at and customer_impact < impact_at:
        return "ignore" if route in {"ignore", "unclear"} else "ticket"
    if route == "unclear":
        return "ticket"  # an unreadable alert is a human's problem, not a pager's
    return route


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Arithmetic is Python's job, never the model's."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Route alerts at alert time.")
    parser.add_argument("--budget-ms", type=float, default=500.0,
                        help="latency this may add to the alerting path; p95 above it exits 1")
    parser.add_argument("--impact-threshold", type=float, default=0.6)
    parser.add_argument("--healed-threshold", type=float, default=0.7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    print(f"routing {len(ALERTS)} alerts via {provider.name}, budget {args.budget_ms:.0f} ms/alert",
          file=sys.stderr)

    rows: list[dict] = []
    latencies: list[float] = []

    try:
        with JevClient(provider=provider) as client:
            for alert in ALERTS:
                answers = client.ask({"alert": alert}, build_questions(alert))
                route = answers.choice("route")
                impact = answers.noul("customer_impact")
                healed = answers.noul("self_healing")
                final = decide(route, impact, healed, args.impact_threshold, args.healed_threshold)
                latencies.append(answers.elapsed_s * 1000)
                rows.append(
                    {
                        "id": alert["id"],
                        "service": alert["service"],
                        "env": alert["env"],
                        "title": alert["title"],
                        "model_route": route,
                        "customer_impact": round(impact, 3),
                        "self_healing": round(healed, 3),
                        "final": final,
                        "ms": round(latencies[-1]),
                        "over_budget": latencies[-1] > args.budget_ms,
                    }
                )
                if not args.json:
                    mark = "!" if rows[-1]["over_budget"] else " "
                    print(
                        f"{final:>6} {mark} {alert['service']:<16} {alert['title'][:44]:<44} "
                        f"impact {impact:.2f}  healed {healed:.2f}  {latencies[-1]:>4.0f} ms"
                    )
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    p95 = percentile(latencies, 0.95)
    summary = {
        "alerts": len(rows),
        "median_ms": round(statistics.median(latencies)),
        "p95_ms": round(p95),
        "slowest_ms": round(max(latencies)),
        "budget_ms": args.budget_ms,
        "within_budget": p95 <= args.budget_ms,
        "input_tokens": client.total_input_tokens,
        "cost_usd": round(client.total_cost_usd, 6),
        "paged": sum(1 for r in rows if r["final"] == "page"),
        "ticketed": sum(1 for r in rows if r["final"] == "ticket"),
        "ignored": sum(1 for r in rows if r["final"] == "ignore"),
    }

    if args.json:
        print(json.dumps({"alerts": rows, "summary": summary}, indent=2))
    else:
        print(
            f"\n{summary['alerts']} alerts · 3 questions each · median {summary['median_ms']} ms · "
            f"p95 {summary['p95_ms']} ms · slowest {summary['slowest_ms']} ms · "
            f"{summary['input_tokens']:,} tokens · ${summary['cost_usd']:.6f}"
        )
        print(
            f"Latency budget added to the alerting path: p95 {summary['p95_ms']} ms against "
            f"{args.budget_ms:.0f} ms allowed — {'within' if summary['within_budget'] else 'OVER'} budget."
        )
        print(f"paged {summary['paged']} · ticketed {summary['ticketed']} · ignored {summary['ignored']}")

    return 0 if summary["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
