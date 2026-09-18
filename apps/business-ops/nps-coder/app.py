"""NPS Coder -- theme thousands of open-ended survey comments against a codebook.

Coding open-ended survey responses is the single most expensive part of running
a survey, and it is the part that gets cut. Someone reads 3,000 comments, tags
each against a codebook, a second person checks a sample, and the whole thing
takes two weeks -- by which time the quarter has moved on.

The design point: a comment can be about pricing AND onboarding AND a specific
missing feature. Those are not competing options. They are separate Nouls, one
per theme, all true at once if the comment says so. A Choice would force the
model to pick one and silently drop the rest.

Batching is against the provider's context window, never a hardcoded number:
comments are split into chunks that fit `provider.context_tokens` and each chunk
is one request carrying every theme for every comment in it.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_comments.json"

DEFAULT_CODEBOOK = {
    "price_value": "The respondent talks about price, cost, billing, or whether the product is worth what they pay.",
    "onboarding": "The respondent talks about getting started, setup, implementation, migration, or training.",
    "reliability": "The respondent talks about outages, bugs, errors, data problems, or the product being unstable.",
    "performance": "The respondent talks about the product being slow, laggy, or timing out.",
    "support_quality": "The respondent talks about the support or customer success team, response times, or the quality of help received.",
    "missing_capability": "The respondent names something the product cannot do that they want it to do.",
    "usability": "The respondent talks about the interface being confusing, cluttered, hard to learn, or pleasant to use.",
    "integrations": "The respondent talks about connecting the product to other tools they use.",
    "mobile": "The respondent talks about the phone or tablet experience specifically.",
    "reporting": "The respondent talks about dashboards, exports, analytics, or getting data out.",
}

SENTIMENT_OPTIONS = {
    "promoter_language": "The comment is clearly positive about the product overall",
    "mixed": "The comment contains both a clear positive and a clear negative",
    "detractor_language": "The comment is clearly negative about the product overall",
    "neutral_factual": "The comment states facts or a request without evaluating the product",
    "no_content": "The comment is empty, nonsense, or says nothing about the product",
}

ACTIONABLE = (
    "Does this comment name something specific enough that a team could act on it?",
    "It names a concrete feature, workflow, screen, price, or interaction",
    "It is a general feeling with no specific target",
)

CHARS_PER_TOKEN = 4  # crude, deliberately conservative
OVERHEAD_TOKENS = 2000  # codebook, criteria text, and question scaffolding


def batch_comments(
    comments: list[dict], context_tokens: int, themes: int
) -> list[list[int]]:
    """Split comment indices into chunks that fit the provider's context window.

    Never hardcode a context size -- read `provider.context_tokens`. The question
    scaffolding grows with the theme list, so it is part of the budget.
    """
    budget = max(context_tokens - OVERHEAD_TOKENS - themes * 60, 2000)
    chunks: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, comment in enumerate(comments):
        cost = len(comment.get("text", "")) // CHARS_PER_TOKEN + 40 + themes * 8
        if current and used + cost > budget:
            chunks.append(current)
            current, used = [], 0
        current.append(index)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def build_questions(indices: list[int], codebook: dict[str, str]) -> dict:
    """One Noul per theme per comment -- several themes can apply at once."""
    questions: dict[str, dict] = {}
    for index in indices:
        reference = f"`comments[{index}].text`"
        for theme, definition in codebook.items():
            questions[f"{theme}__{index}"] = noul(
                {
                    "task": "Does this survey comment fall under the codebook theme below?",
                    "theme": definition,
                    "comment": reference,
                },
                true="The comment raises this theme, whether positively or negatively",
                false="The comment does not touch this theme",
            )
        questions[f"sentiment__{index}"] = choice(
            {"task": "How does this comment evaluate the product overall?", "comment": reference},
            SENTIMENT_OPTIONS,
        )
        task, yes, no = ACTIONABLE
        questions[f"actionable__{index}"] = noul(
            {"task": task, "comment": reference}, true=yes, false=no
        )
    return questions


def nps_bucket(rating: int) -> str:
    """NPS arithmetic is arithmetic. It belongs here, not in a question."""
    if rating >= 9:
        return "promoter"
    if rating >= 7:
        return "passive"
    return "detractor"


def nps_score(ratings: list[int]) -> float:
    if not ratings:
        return 0.0
    buckets = [nps_bucket(r) for r in ratings]
    promoters = buckets.count("promoter")
    detractors = buckets.count("detractor")
    return 100.0 * (promoters - detractors) / len(buckets)


def theme_table(frame: pd.DataFrame, codebook: dict[str, str], fire_at: float) -> pd.DataFrame:
    """Theme prevalence and its NPS impact. All aggregation is pandas."""
    rows = []
    overall = nps_score(frame["rating"].tolist())
    for theme in codebook:
        hit = frame[frame[theme] >= fire_at]
        rows.append(
            {
                "theme": theme,
                "comments": len(hit),
                "share": round(len(hit) / len(frame) * 100, 1) if len(frame) else 0.0,
                "nps when mentioned": round(nps_score(hit["rating"].tolist()), 1),
                "nps delta": round(nps_score(hit["rating"].tolist()) - overall, 1),
                "actionable share": round(
                    hit["actionable"].ge(fire_at).mean() * 100, 1
                ) if len(hit) else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("comments", ascending=False)


def main() -> None:
    st.set_page_config(page_title="NPS Coder", page_icon="🗂️", layout="wide")
    st.title("🗂️ NPS Coder")
    st.caption("Theme open-ended survey comments against your codebook. Several themes per comment.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.caption(f"context window: {provider.context_tokens:,} tokens")
        st.divider()
        st.subheader("Coding thresholds")
        st.caption("Re-coding from stored probabilities costs nothing.")
        fire_at = st.slider("Apply a theme at P(yes) ≥", 0.3, 0.95, 0.60, 0.05)
        show_uncoded = st.checkbox("Show comments no theme matched", value=True)

    codebook_text = st.text_area(
        "Codebook (JSON: theme → definition)",
        value=json.dumps(DEFAULT_CODEBOOK, indent=2),
        height=240,
    )
    try:
        codebook = json.loads(codebook_text)
        assert isinstance(codebook, dict) and codebook
    except (json.JSONDecodeError, AssertionError) as exc:
        st.error(f"Codebook must be a non-empty JSON object: {exc}")
        return

    upload = st.file_uploader("Comments (JSON list of {id, rating, text})", type=["json"])
    comments = json.loads(upload.read().decode("utf-8")) if upload else json.loads(
        SAMPLE.read_text(encoding="utf-8")
    )

    chunks = batch_comments(comments, provider.context_tokens, len(codebook))
    st.caption(
        f"{len(comments)} comments × ({len(codebook)} themes + 2) = "
        f"{len(comments) * (len(codebook) + 2)} judgments in {len(chunks)} request(s)."
    )

    if st.button("Code the comments", type="primary"):
        judgments: dict[int, dict] = {}
        tokens = cost = elapsed = 0.0
        asked = 0
        try:
            with JevClient(provider=provider) as client:
                progress = st.progress(0.0)
                for position, indices in enumerate(chunks, start=1):
                    questions = build_questions(indices, codebook)
                    answers = client.ask({"comments": comments}, questions)
                    asked += len(questions)
                    tokens += answers.input_tokens
                    cost += answers.cost_usd
                    elapsed += answers.elapsed_s
                    for index in indices:
                        judgments[index] = {
                            **{t: answers.noul(f"{t}__{index}") for t in codebook},
                            "sentiment": answers.choice(f"sentiment__{index}"),
                            "actionable": answers.noul(f"actionable__{index}"),
                        }
                    progress.progress(position / len(chunks))
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["coded"] = {
            "comments": comments,
            "codebook": list(codebook),
            "judgments": judgments,
            "questions": asked,
            "requests": len(chunks),
            "tokens": int(tokens),
            "cost": cost,
            "elapsed": elapsed,
        }

    coded = st.session_state.get("coded")
    if not coded:
        st.info("Hit the button. Nothing is sent until you do.")
        return

    codebook_keys = coded["codebook"]
    frame = pd.DataFrame(
        [
            {
                "id": comment["id"],
                "rating": int(comment["rating"]),
                "bucket": nps_bucket(int(comment["rating"])),
                "sentiment": coded["judgments"][i]["sentiment"],
                "actionable": round(coded["judgments"][i]["actionable"], 2),
                "text": comment["text"],
                **{t: round(coded["judgments"][i][t], 3) for t in codebook_keys},
            }
            for i, comment in enumerate(coded["comments"])
        ]
    )

    columns = st.columns(4)
    columns[0].metric("Comments", len(frame))
    columns[1].metric("NPS", f"{nps_score(frame['rating'].tolist()):.0f}")
    coded_mask = frame[codebook_keys].ge(fire_at).any(axis=1)
    columns[2].metric("Coded to ≥1 theme", int(coded_mask.sum()))
    columns[3].metric("Themes per comment", f"{frame[codebook_keys].ge(fire_at).sum(axis=1).mean():.2f}")

    st.subheader("Themes")
    st.dataframe(theme_table(frame, {t: "" for t in codebook_keys}, fire_at), width="stretch", hide_index=True)

    st.subheader("Sentiment × NPS bucket")
    st.dataframe(pd.crosstab(frame["sentiment"], frame["bucket"]), width="stretch")

    st.subheader("Coded comments")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download coded.csv",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="coded_comments.csv",
        mime="text/csv",
    )

    if show_uncoded:
        uncoded = frame[~coded_mask]
        st.subheader(f"No theme matched ({len(uncoded)})")
        st.caption("These are your codebook's blind spots. Read them before the next survey.")
        st.dataframe(uncoded[["id", "rating", "sentiment", "text"]], width="stretch", hide_index=True)

    st.divider()
    metrics = st.columns(4)
    metrics[0].metric("Judgments", coded["questions"])
    metrics[1].metric("Requests", coded["requests"])
    metrics[2].metric("Latency", f"{coded['elapsed']:.1f} s")
    metrics[3].metric("Cost", f"${coded['cost']:.6f}")
    st.caption(
        f"{coded['tokens']:,} input tokens. Chunked to fit {provider.context_tokens:,} "
        "tokens of context — re-coding at a different threshold costs $0.00."
    )


if __name__ == "__main__":
    main()
