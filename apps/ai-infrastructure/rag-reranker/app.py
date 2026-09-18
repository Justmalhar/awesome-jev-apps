#!/usr/bin/env python3
"""rag-reranker -- a drop-in replacement for a cross-encoder or Cohere Rerank,
shown side by side with the cosine baseline it replaces.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --queries my_queries.json --json

Exit code 1 if any query's best passage falls below --min-top, which makes this
a retrieval-quality gate: it fails when the retriever returned nothing that
actually answers the question, instead of quietly reordering ten bad passages.

Three questions per passage, all in ONE request:

  relevance   Score -- how much of the answer this passage carries
  answers_directly    Noul -- does it state the answer, or only lead toward one
  different_subject   Noul -- is it about another product, plan or version

The last one is the classic distractor, and it has to be its own Noul: a
passage about the *consumer* return window is highly relevant-looking and
completely wrong, so it must be able to score high on relevance and still be
pushed down. Fold it into the rubric and you lose the ability to weight it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from baseline import rank as cosine_rank  # noqa: E402
from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_queries.json"
CHARS_PER_TOKEN = 4

RELEVANCE_LEVELS = [
    "About a different topic; a reader looking for this answer would skip it",
    "Touches the same topic but does not address what was asked",
    "Supplies background a reader needs to make sense of the answer, without stating it",
    "States the answer to the question outright, in this passage, with no further lookup",
]


def load_queries(path: Path | None) -> list[dict]:
    """The bundled sample is the default so this runs before anyone supplies data."""
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def batch(queries: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split by query so each request's state fits the provider's context window.
    A query and its candidates always travel together -- they have to."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for query in queries:
        size = max(1, len(json.dumps(query)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(query)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(queries: list[dict]) -> dict:
    """3 questions per (query, passage) pair, every pair in ONE request."""
    questions: dict[str, dict] = {}
    for qi, query in enumerate(queries):
        for pi in range(len(query["passages"])):
            reference = {
                "question": f"`queries[{qi}].query`",
                "passage": f"`queries[{qi}].passages[{pi}].text`",
            }
            questions[f"relevance_{qi}_{pi}"] = score(
                {"task": "How much of the answer to the question does this passage carry?",
                 "pair": reference},
                RELEVANCE_LEVELS,
            )
            questions[f"direct_{qi}_{pi}"] = noul(
                {"task": "Could a reader answer the question from this passage alone?",
                 "pair": reference},
                true="The passage states the fact being asked for, not merely where to find it",
                false="The reader would still have to consult something else to answer",
            )
            questions[f"different_subject_{qi}_{pi}"] = noul(
                {"task": "Is this passage about a different product, plan, version or system than the question is asking about?",
                 "pair": reference},
                true="It describes a neighbouring subject -- another plan, another product, a superseded revision, a different job or service",
                false="It is about the same subject the question names",
            )
    return questions


def combine(relevance: float, direct: float, different_subject: float, *,
            w_relevance: float, w_direct: float, w_different: float) -> float:
    """The ranking function. Weights are CLI arguments, never instructions, so
    retuning re-sorts everything without another request."""
    normalised = relevance / (len(RELEVANCE_LEVELS) - 1)
    return w_relevance * normalised + w_direct * direct - w_different * different_subject


def movement(before: list[str], after: list[str]) -> dict[str, int]:
    """Rank change per passage id, positive meaning it moved up. Python's job:
    the model is never asked to compare positions."""
    old = {pid: position for position, pid in enumerate(before)}
    return {pid: old[pid] - position for position, pid in enumerate(after)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerank retrieved passages and compare with cosine.")
    parser.add_argument("--queries", type=Path, help="JSON list of {query, passages:[{id,text}]}")
    parser.add_argument("--w-relevance", type=float, default=1.0)
    parser.add_argument("--w-direct", type=float, default=0.5)
    parser.add_argument("--w-different", type=float, default=0.8,
                        help="penalty for a passage about a neighbouring subject")
    parser.add_argument("--top", type=int, default=5, help="results to show per query")
    parser.add_argument("--min-top", type=float, default=0.5,
                        help="a query whose best passage scores below this is a retrieval failure")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    queries = load_queries(args.queries)
    if not queries:
        print("no queries to rerank", file=sys.stderr)
        return 2

    pairs = sum(len(query["passages"]) for query in queries)
    groups = batch(queries, int(provider.context_tokens * 0.5))
    print(f"reranking {pairs} passages across {len(queries)} queries via {provider.name} "
          f"({len(groups)} request(s), {pairs * 3} questions)", file=sys.stderr)

    results: list[dict] = []
    elapsed = 0.0
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                answers = client.ask({"queries": group}, build_questions(group))
                elapsed += answers.elapsed_s
                for qi, query in enumerate(group):
                    scored = []
                    for pi, passage in enumerate(query["passages"]):
                        relevance = answers.score(f"relevance_{qi}_{pi}")
                        direct = answers.noul(f"direct_{qi}_{pi}")
                        different = answers.noul(f"different_subject_{qi}_{pi}")
                        scored.append({
                            "id": passage["id"],
                            "text": passage["text"],
                            "relevance": relevance,
                            "direct": direct,
                            "different_subject": different,
                            "score": combine(relevance, direct, different,
                                             w_relevance=args.w_relevance,
                                             w_direct=args.w_direct,
                                             w_different=args.w_different),
                        })
                    baseline_order = cosine_rank(
                        query["query"], [p["text"] for p in query["passages"]])
                    baseline_ids = [query["passages"][index]["id"] for index, _ in baseline_order]
                    reranked = sorted(scored, key=lambda row: -row["score"])
                    results.append({
                        "query": query["query"],
                        "baseline": baseline_ids,
                        "baseline_scores": {query["passages"][i]["id"]: value
                                            for i, value in baseline_order},
                        "reranked": reranked,
                        "movement": movement(baseline_ids, [row["id"] for row in reranked]),
                    })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    failures = [r for r in results if r["reranked"][0]["score"] < args.min_top]

    if args.json:
        print(json.dumps({"results": results, "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if failures else 0

    for result in results:
        print(f"\n▸ {result['query']}")
        print(f"  {'rank':<5} {'cosine baseline':<22} {'jev reranked':<22} move")
        for position in range(min(args.top, len(result["reranked"]))):
            base_id = result["baseline"][position]
            row = result["reranked"][position]
            shift = result["movement"][row["id"]]
            arrow = f"▲{shift}" if shift > 0 else (f"▼{-shift}" if shift < 0 else "·")
            print(f"  {position + 1:<5} {base_id:<22} {row['id']:<22} {arrow}")
        winner = result["reranked"][0]
        print(f"  top: {winner['text'][:120]}")
        print(f"       relevance {winner['relevance']:.2f}/{len(RELEVANCE_LEVELS) - 1} · "
              f"direct P={winner['direct']:.2f} · "
              f"different subject P={winner['different_subject']:.2f} · "
              f"score {winner['score']:.3f}")
        demoted = sorted(result["reranked"], key=lambda row: result["movement"][row["id"]])[0]
        if result["movement"][demoted["id"]] < 0:
            print(f"  biggest demotion: {demoted['id']} "
                  f"({result['movement'][demoted['id']]}) -- different subject "
                  f"P={demoted['different_subject']:.2f}")

    print(f"\n{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured · {elapsed:.2f}s")
    print(f"${cost / max(1, pairs) * 1e6:,.2f} per million query-passage pairs at this size -- "
          "measured, not estimated. Price your current reranker per search and compare.")
    if failures:
        print(f"{len(failures)} quer(ies) had no passage above --min-top {args.min_top}: "
              "a retrieval failure, not a ranking one.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
