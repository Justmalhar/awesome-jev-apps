#!/usr/bin/env python3
"""Pure-stdlib TF-IDF cosine reranker -- the thing this app exists to beat.

No dependencies on purpose: it must run in the same process as app.py with
nothing installed, so the comparison is honest and always available.

    from baseline import rank
    rank("how long to return a laptop", ["...", "..."])  -> [(index, cosine), ...]

This is deliberately the *good* version of the cheap baseline: sublinear term
frequency is skipped, but IDF is smoothed and computed over the candidate set,
which is what an in-process reranker does after a vector search. It still has
the structural flaw that makes reranking a real job -- cosine rewards shared
vocabulary, and the passage that shares the most vocabulary with a question is
very often the one about the wrong product, the wrong plan, or the wrong year.

    uv run --no-project python baseline.py    # self-check
"""

from __future__ import annotations

import math
import re
from collections import Counter

TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def _tfidf(tokens: list[str], idf: dict[str, float], default_idf: float) -> dict[str, float]:
    counts = Counter(tokens)
    length = sum(counts.values())
    if not length:
        return {}
    return {term: (count / length) * idf.get(term, default_idf)
            for term, count in counts.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    dot = sum(left[term] * right[term] for term in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def rank(query: str, passages: list[str]) -> list[tuple[int, float]]:
    """Rank passages against the query. Returns (original index, cosine),
    best first, ties broken by original order so the output is stable."""
    documents = [tokenize(passage) for passage in passages]
    document_frequency: Counter[str] = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))

    total = len(documents)
    idf = {term: math.log((total + 1) / (frequency + 1)) + 1.0
           for term, frequency in document_frequency.items()}
    default_idf = math.log(total + 1) + 1.0

    query_vector = _tfidf(tokenize(query), idf, default_idf)
    scored = [(index, _cosine(query_vector, _tfidf(tokens, idf, default_idf)))
              for index, tokens in enumerate(documents)]
    return sorted(scored, key=lambda pair: (-pair[1], pair[0]))


if __name__ == "__main__":
    passages = [
        "Return a laptop: laptop return requests for a laptop accessory are handled here.",
        "Business plan hardware may be sent back up to 45 days after it arrives.",
        "Our Lisbon office is on the fourth floor and opens at nine.",
    ]
    ordering = rank("how long do I have to return a laptop", passages)
    assert [index for index, _ in ordering] == [0, 1, 2], (
        "the vocabulary trap must outrank the correct answer -- that is the whole "
        f"reason a real reranker exists. got {ordering}")
    assert all(0.0 <= value <= 1.0001 for _, value in ordering)
    assert rank("anything", []) == []
    assert rank("", ["some text"]) == [(0, 0.0)], "an empty query scores nothing"
    print("baseline self-check passed")
