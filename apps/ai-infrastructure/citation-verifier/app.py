#!/usr/bin/env python3
"""citation-verifier -- check every claim against the source it cites.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --claims my_claims.json --json

Exit code 1 if any claim is contradicted by the source it cites.

"Supported" and "contradicted" are NOT two ends of one dial. A source that says
nothing about a claim is neither -- and that third state, silent unsupported, is
the one a single rubric collapses and a reviewer most needs to see. So they are
separate Nouls, thresholded separately, and the app flags disagreement between
them for a human rather than averaging it away.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_claims.json"
CHARS_PER_TOKEN = 4

PROPERTIES = {
    "stated": {
        "task": "Does the cited source state this claim, either in these words or in plainly equivalent ones?",
        "true": "A reader of the source alone would come away asserting this claim",
        "false": "The source does not assert it, even if it is compatible with it",
    },
    "contradicted": {
        "task": "Does the cited source assert something incompatible with this claim?",
        "true": "Believing the source and the claim at the same time is not possible",
        "false": "The source is either consistent with the claim or silent about it",
    },
    "same_subject": {
        "task": "Is the cited source about the same entity, product, period and event as the claim?",
        "true": "The claim and the source are talking about the same thing",
        "false": "The source is about a different company, product, version, region or occasion",
    },
    "needs_inference": {
        "task": "Does getting from the source to the claim require adding something the source does not supply?",
        "true": "The claim only follows if the reader brings in an outside assumption or an extra step",
        "false": "The claim is read directly off the source",
    },
}

DRIFT_LEVELS = [
    "The claim restates the source with no change in meaning",
    "The claim generalises or simplifies, but a careful reader would accept it",
    "The claim adds a detail, direction or figure the source never supplies",
    "The claim asserts something the source gives no basis for at all",
]

VERDICTS = ("contradicted", "unsupported", "wrong-source", "verified")


def load_claims(path: Path | None) -> list[dict]:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(claims: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for claim in claims:
        size = max(1, len(json.dumps(claim)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(claim)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(claims: list[dict]) -> dict:
    """Five judgments per claim, all in ONE request."""
    questions: dict[str, dict] = {}
    for index in range(len(claims)):
        reference = {"claim": f"`claims[{index}].claim`",
                     "cited_source": f"`claims[{index}].source`"}
        for name, spec in PROPERTIES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "pair": reference},
                true=spec["true"], false=spec["false"],
            )
        questions[f"drift_{index}"] = score(
            {"task": "How far does the claim travel beyond what the cited source actually says?",
             "pair": reference},
            DRIFT_LEVELS,
        )
    return questions


def verdict(props: dict[str, float], drift: float, *,
            support_p: float, contradict_p: float, subject_p: float) -> tuple[str, bool]:
    """Returns (verdict, needs_human_review). Policy lives here, not in a prompt."""
    if props["contradicted"] >= contradict_p:
        return "contradicted", False
    if props["same_subject"] < subject_p:
        return "wrong-source", True
    if props["stated"] >= support_p:
        # Stated but heavy inference, or stated AND contradicted, is a conflict.
        conflicted = props["contradicted"] >= contradict_p / 2 or props["needs_inference"] >= 0.7
        return "verified", conflicted
    return "unsupported", drift < 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify claims against their cited sources.")
    parser.add_argument("--claims", type=Path, help="JSON list of {id, claim, source_id, source}")
    parser.add_argument("--support-p", type=float, default=0.65)
    parser.add_argument("--contradict-p", type=float, default=0.60)
    parser.add_argument("--subject-p", type=float, default=0.50)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    claims = load_claims(args.claims)
    if not claims:
        print("no claims to verify", file=sys.stderr)
        return 2

    groups = batch(claims, int(provider.context_tokens * 0.5))
    print(f"verifying {len(claims)} claims via {provider.name} "
          f"({len(groups)} request(s), {len(claims) * (len(PROPERTIES) + 1)} questions)",
          file=sys.stderr)

    rows: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                answers = client.ask({"claims": group}, build_questions(group))
                for index, claim in enumerate(group):
                    props = {name: answers.noul(f"{name}_{index}") for name in PROPERTIES}
                    drift = answers.score(f"drift_{index}")
                    label, review = verdict(props, drift, support_p=args.support_p,
                                            contradict_p=args.contradict_p,
                                            subject_p=args.subject_p)
                    rows.append({**claim, "properties": props, "drift": drift,
                                 "verdict": label, "needs_review": review})
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"rows": rows, "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if any(r["verdict"] == "contradicted" for r in rows) else 0

    marks = {"verified": "✅", "unsupported": "❔", "contradicted": "❌", "wrong-source": "🔀"}
    for row in rows:
        review = "  🧑 review" if row["needs_review"] else ""
        print(f"{marks[row['verdict']]} {row['id']}  {row['verdict']:<13} "
              f"drift {row['drift']:.2f}/{len(DRIFT_LEVELS) - 1}{review}")
        print(f"     claim : {row['claim']}")
        print(f"     source: [{row['source_id']}] {row['source'][:100]}")
        print(f"     {' '.join(f'{k}={v:.2f}' for k, v in row['properties'].items())}")

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    flagged = sum(1 for r in rows if r["needs_review"])
    print(f"\n{counts['verified']} verified · {counts['unsupported']} unsupported · "
          f"{counts['contradicted']} contradicted · {counts['wrong-source']} wrong source · "
          f"{flagged} flagged for a human")
    print(f"{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(claims)) * 1e6:,.2f} per million claims checked")
    return 1 if counts["contradicted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
