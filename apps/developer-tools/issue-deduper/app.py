"""issue-deduper -- which of these issues are the same underlying bug?

Duplicate detection by text similarity finds the easy half: two reports that
say "app crashes on startup" in the same words. It misses the pair that
matters -- "blank screen after login" and "session token dropped on redirect"
are the same defect described from two ends, and share almost no vocabulary.
It also fires on "crash on startup, Windows" versus "crash on startup, iOS",
which are two different bugs.

So: difflib does the blocking (cheap, exact, no tokens), and Jev judges the
surviving pairs. The judgment is a 3-level Score whose levels ARE the three
things a triager can do, which means no mapping table sits between the answer
and the action.

    uv run streamlit run app.py
"""

from __future__ import annotations

import difflib
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_issues.json"
BODY_CHARS = 900

# The three levels are the three available actions. `verdict()` reads the score
# and nothing else has to be decided.
ACTIONS = [
    "Two different defects. Closing either as a duplicate of the other would lose a bug report.",
    "Plausibly the same defect, but the two reports do not establish it; a triager should look at both.",
    "One defect reported twice. Closing the later report as a duplicate loses nothing.",
]


@dataclass
class Pair:
    left: dict
    right: dict
    similarity: float

    @property
    def key(self) -> str:
        return f"{self.left['id']}~{self.right['id']}"


def tokens_of(text: str) -> list[str]:
    """Sorted unique words. SequenceMatcher over WORDS, not characters --
    character-level ratios on issue bodies are dominated by length, so two
    reports of the same bug written at different lengths score far apart."""
    return sorted(set(re.findall(r"[a-z0-9_]+", text.lower())))


def similarity(left: dict, right: dict) -> float:
    """Best of title-only and whole-report overlap. A terse title that matches
    should survive blocking even when the bodies diverge, and vice versa."""
    title = difflib.SequenceMatcher(
        None, tokens_of(left.get("title", "")), tokens_of(right.get("title", ""))
    ).ratio()
    whole = difflib.SequenceMatcher(
        None,
        tokens_of(f"{left.get('title', '')} {left.get('body', '')}"),
        tokens_of(f"{right.get('title', '')} {right.get('body', '')}"),
    ).ratio()
    return max(title, whole)


def candidate_pairs(issues: list[dict], floor: float, cap: int) -> list[Pair]:
    """Blocking in Python. Quadratic in issue count but free, and it discards
    the overwhelming majority of pairs before a single token is spent."""
    pairs = [
        Pair(left=left, right=right, similarity=ratio)
        for left, right in itertools.combinations(issues, 2)
        if (ratio := similarity(left, right)) >= floor
    ]
    pairs.sort(key=lambda p: -p.similarity)
    return pairs[:cap]


def build_questions(pairs: list[Pair]) -> dict:
    questions: dict[str, dict] = {}
    for index in range(len(pairs)):
        reference = {
            "issue_a": {"title": f"`pairs[{index}].a.title`", "body": f"`pairs[{index}].a.body`"},
            "issue_b": {"title": f"`pairs[{index}].b.title`", "body": f"`pairs[{index}].b.body`"},
        }
        questions[f"action_{index}"] = score(
            {"task": "Do these two issue reports describe the same underlying defect?",
             "pair": reference},
            ACTIONS,
        )
        # Independent of whether they are duplicates: which report to keep as
        # the canonical one. Useful only when they ARE duplicates, but asked in
        # the same request because a second round trip costs more than a wasted
        # question.
        questions[f"a_is_richer_{index}"] = noul(
            {"task": "Does report A contain reproduction detail that report B lacks?",
             "pair": reference},
            true="A gives steps, versions, logs, or an environment that B does not",
            false="B is as detailed as A, or more so",
        )
    return questions


def verdict(action_score: float, *, merge_gate: float, separate_ceiling: float) -> str:
    """The score lands between levels; policy decides where the cuts are."""
    if action_score >= merge_gate:
        return "merge"
    if action_score <= separate_ceiling:
        return "keep_separate"
    return "human_decides"


def main() -> None:
    st.set_page_config(page_title="Issue Deduper", page_icon="🔗", layout="wide")
    st.title("🔗 Issue Deduper")
    st.caption("Which issues are the same underlying bug? difflib blocks, Jev judges.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Blocking (Python, free)")
        floor = st.slider("difflib word-overlap floor", 0.0, 1.0, 0.22, 0.02)
        cap = st.slider("Max pairs to judge", 10, 400, 120, 10)
        st.subheader("Policy (no inference)")
        merge_gate = st.slider("Score to merge outright", 1.0, 2.0, 1.6, 0.05)
        separate_ceiling = st.slider("Score below which they stay separate", 0.0, 1.0, 0.7, 0.05)
        batch_size = st.slider("Pairs per request", 5, 60, 20, 5)

    uploaded = st.file_uploader("Issues JSON: [{id, title, body}]", type=["json"])
    if uploaded:
        issues = json.loads(uploaded.read().decode("utf-8"))
    else:
        issues = json.loads(SAMPLE.read_text(encoding="utf-8"))
        st.caption(f"Using the bundled sample: {len(issues)} issues.")

    for issue in issues:
        issue["body"] = str(issue.get("body", ""))[:BODY_CHARS]

    pairs = candidate_pairs(issues, floor, cap)
    columns = st.columns(2)
    columns[0].metric("Issues", len(issues))
    columns[1].metric("Pairs surviving blocking", len(pairs))
    if not pairs:
        st.info("Blocking removed every pair. Lower the similarity floor.")
        return
    if not st.button(f"Judge {len(pairs)} candidate pairs", type="primary"):
        return

    groups = [pairs[i:i + batch_size] for i in range(0, len(pairs), batch_size)]
    judged: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        with st.spinner(f"Judging in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    state = {
                        "pairs": [
                            {"a": {"title": p.left["title"], "body": p.left["body"]},
                             "b": {"title": p.right["title"], "body": p.right["body"]}}
                            for p in group
                        ]
                    }
                    questions = build_questions(group)
                    answers = client.ask(state, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, pair in enumerate(group):
                        action = answers.score(f"action_{index}")
                        richer = answers.noul(f"a_is_richer_{index}")
                        judged.append(
                            {
                                "pair": pair.key,
                                "a": pair.left["title"],
                                "b": pair.right["title"],
                                "similarity": round(pair.similarity, 3),
                                "score": round(action, 3),
                                "confidence": round(answers.confidence(f"action_{index}"), 3),
                                "keep": pair.left["id"] if richer >= 0.5 else pair.right["id"],
                                "verdict": verdict(action, merge_gate=merge_gate,
                                                   separate_ceiling=separate_ceiling),
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    buckets = {name: [j for j in judged if j["verdict"] == name]
               for name in ("merge", "human_decides", "keep_separate")}

    columns = st.columns(3)
    columns[0].metric("Merge", len(buckets["merge"]))
    columns[1].metric("Human decides", len(buckets["human_decides"]))
    columns[2].metric("Keep separate", len(buckets["keep_separate"]))

    for name, title, blurb in (
        ("merge", "🔗 Close as duplicate", "Keep the report with the better reproduction detail."),
        ("human_decides", "🤔 A triager should look", "Related, but the reports do not establish one defect."),
        ("keep_separate", "✂️ Different bugs", "Similar words, different defects. Do not merge these."),
    ):
        items = buckets[name]
        if not items:
            continue
        st.subheader(title)
        st.caption(blurb)
        st.dataframe(
            sorted(items, key=lambda i: -i["score"]),
            width="stretch",
            column_order=["a", "b", "score", "confidence", "similarity", "keep"],
        )

    st.divider()
    st.caption(
        f"{asked} question(s) over {len(judged)} pair(s) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s measured. "
        f"Blocking discarded the rest before any of it was spent."
    )


if __name__ == "__main__":
    main()
