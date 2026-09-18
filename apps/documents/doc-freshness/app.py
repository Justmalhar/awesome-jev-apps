"""Doc Freshness -- which documented claims the current code no longer supports.

Two states, compared: the documentation, and the evidence of what the system
actually does now (route table, config schema, changelog, package metadata).

The three failure modes are INDEPENDENT, so they are three Nouls, not one
rubric:

  contradicted     the evidence says something incompatible with the claim
  unsupported      the evidence neither confirms nor contradicts it
  describes_removed the thing the claim documents is gone

"Wrong" and "unverifiable" need different triage -- one is a docs bug, the other
is a gap in the evidence you supplied -- so they need separate thresholds. A
Score would have implied they sit on one axis, and they do not.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE_DOC = HERE / "sample_docs.md"
SAMPLE_REALITY = HERE / "sample_reality.txt"
# Rough chars-per-token, only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4

HAZARDS: dict[str, dict[str, str]] = {
    "contradicted": {
        "label": "Contradicted by the evidence",
        "task": "Does the evidence state something incompatible with this documented claim, exactly as the claim is written?",
        "true": "Some line of the evidence asserts something that cannot both be true alongside this claim as worded",
        "false": "The evidence agrees with the claim, or says nothing that conflicts with it",
    },
    "unsupported": {
        "label": "Not verifiable from the evidence",
        "task": "Is this documented claim neither confirmed nor contradicted anywhere in the evidence?",
        "true": "The evidence is silent on the subject of this claim, so it can be neither confirmed nor refuted from what was supplied",
        "false": "The evidence confirms the claim, or the evidence conflicts with it; either way the evidence speaks to it",
    },
    "describes_removed": {
        "label": "Documents something gone",
        "task": "Does this claim document a route, option, header, environment variable, or feature that the evidence shows no longer exists?",
        "true": "The evidence shows the named thing was removed, or lists the set it would belong to and it is absent from that set",
        "false": "The evidence shows the named thing still exists, or does not establish that it is gone",
    },
}

# A Choice must return something, so 'still_accurate' and 'not_determinable'
# are the escape hatches that stop it nominating the least-wrong change type.
CHANGE_KINDS: dict[str, str] = {
    "renamed": "The thing still exists but the evidence shows it is now called something else: a different path, field, header, or environment variable name.",
    "default_changed": "The thing still exists with the same name, but the evidence shows its default value is now different from the documented one.",
    "removed": "The evidence shows the thing the claim documents no longer exists at all.",
    "newly_required": "The thing the claim describes as optional is shown by the evidence to be mandatory now.",
    "behaviour_changed": "The name and presence are unchanged, but the evidence shows it now does something different from what the claim describes.",
    "still_accurate": "The evidence agrees with the claim; nothing about it has changed and the documentation is correct as written.",
    "not_determinable": "The evidence does not say enough about this claim to tell whether anything changed.",
}

QUESTIONS_PER_CLAIM = len(HAZARDS) + 1


def split_claims(doc: str) -> list[dict[str, str]]:
    """Split a doc into individual factual claims. Mechanical, so it stays here.

    Markdown docs are one claim per bullet, under a heading that gives the claim
    its context. Prose falls back to sentence splitting under the same heading.
    """
    claims: list[dict[str, str]] = []
    section = ""
    for raw in doc.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        if line.startswith(("- ", "* ", "+ ")):
            claims.append({"section": section, "text": line[2:].strip()})
            continue
        if re.match(r"^\d+[.)]\s", line):
            claims.append({"section": section, "text": re.sub(r"^\d+[.)]\s*", "", line)})
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z`])", line):
            if sentence.strip():
                claims.append({"section": section, "text": sentence.strip()})
    return claims


def build_questions(claims: list[dict[str, str]]) -> dict:
    """Three Nouls + one Choice per claim, against one shared evidence state."""
    questions: dict[str, dict] = {}
    for index in range(len(claims)):
        reference = {
            "section": f"`claims[{index}].section`",
            "claim": f"`claims[{index}].text`",
        }
        for key, spec in HAZARDS.items():
            questions[f"{key}_{index}"] = noul(
                {
                    "task": spec["task"],
                    "documented_claim": reference,
                    "evidence": "`reality`",
                },
                true=spec["true"],
                false=spec["false"],
            )
        questions[f"change_{index}"] = choice(
            {
                "task": "Select what the evidence shows has changed about the thing this claim documents.",
                "documented_claim": reference,
                "evidence": "`reality`",
                "note": "Choose 'still_accurate' when nothing changed, or 'not_determinable' when the evidence does not say.",
            },
            CHANGE_KINDS,
        )
    return questions


def classify(
    probabilities: dict[str, float],
    change: str,
    contradicted_at: float,
    unsupported_at: float,
    removed_at: float,
) -> str:
    """Route one claim. Jev supplied evidence; this is the policy.

    Order matters and is deliberate: a claim that documents something gone is a
    harder error than one that merely conflicts, and both beat 'unverifiable'.
    'unsupported' is checked last because it is the weakest signal -- it usually
    means the evidence you pasted was too narrow, not that the doc is wrong.
    """
    if probabilities["describes_removed"] >= removed_at:
        return "stale"
    if probabilities["contradicted"] >= contradicted_at:
        return "wrong"
    if probabilities["unsupported"] >= unsupported_at:
        return "unverifiable"
    if change in ("still_accurate", "not_determinable"):
        return "current"
    # The Choice named a change the Nouls did not corroborate: worth a human.
    return "check"


def rank(rows: list[dict]) -> list[dict]:
    """Most-certainly-broken first: removed, then contradicted, then unverified.

    Sorting happens here, on floats, because the model cannot order a list.
    """
    severity = {"stale": 0, "wrong": 1, "check": 2, "unverifiable": 3, "current": 4}
    return sorted(
        rows,
        key=lambda r: (
            severity[r["verdict"]],
            -max(r["probabilities"]["describes_removed"], r["probabilities"]["contradicted"]),
        ),
    )


def main() -> None:
    st.set_page_config(page_title="Doc Freshness", page_icon="📉", layout="wide")
    st.title("📉 Doc Freshness")
    st.caption(
        "Which statements in your documentation the current code no longer backs up — "
        "claim by claim, against evidence you supply."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.caption("Change `provider` in providers.toml, or set `JEV_PROVIDER`.")
        st.divider()
        st.subheader("Thresholds")
        st.caption("One per hazard — they are independent, and you will want them different.")
        removed_at = st.slider("Documents something gone, at or above", 0.0, 1.0, 0.70, 0.05)
        contradicted_at = st.slider("Contradicted, at or above", 0.0, 1.0, 0.65, 0.05)
        unsupported_at = st.slider("Unverifiable, at or above", 0.0, 1.0, 0.80, 0.05)
        st.caption(
            "Set 'unverifiable' high. It usually means the evidence you pasted was "
            "too narrow, not that the doc is wrong."
        )

    left, right = st.columns(2)
    with left:
        doc_upload = st.file_uploader("Documentation (.md or .txt)", type=["md", "txt"], key="doc")
        doc_text = (
            doc_upload.read().decode("utf-8", errors="replace")
            if doc_upload
            else (SAMPLE_DOC.read_text(encoding="utf-8") if SAMPLE_DOC.is_file() else "")
        )
        doc_text = st.text_area("What the docs claim", value=doc_text, height=300)
    with right:
        reality_upload = st.file_uploader("Evidence (.txt or .md)", type=["txt", "md"], key="reality")
        reality_text = (
            reality_upload.read().decode("utf-8", errors="replace")
            if reality_upload
            else (SAMPLE_REALITY.read_text(encoding="utf-8") if SAMPLE_REALITY.is_file() else "")
        )
        reality_text = st.text_area(
            "What is actually true (routes, config schema, changelog)",
            value=reality_text,
            height=300,
        )

    if not st.button("Check freshness", type="primary") or not doc_text.strip() or not reality_text.strip():
        st.info("Both panes are prefilled with the bundled sample. Hit **Check freshness** to run it.")
        return

    claims = split_claims(doc_text)
    if not claims:
        st.warning("No claims found in that document.")
        return

    estimated_tokens = (len(doc_text) + len(reality_text)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Check one section at a time, or switch provider."
        )
        return

    questions = build_questions(claims)

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"claims": claims, "reality": reality_text}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, claim in enumerate(claims):
        probabilities = {key: answers.noul(f"{key}_{index}") for key in HAZARDS}
        change = answers.choice(f"change_{index}")
        rows.append(
            {
                "index": index,
                "section": claim["section"],
                "text": claim["text"],
                "probabilities": probabilities,
                "change": change,
                "change_confidence": answers.confidence(f"change_{index}"),
                "verdict": classify(
                    probabilities, change, contradicted_at, unsupported_at, removed_at
                ),
            }
        )

    ordered = rank(rows)
    buckets = {
        name: [r for r in ordered if r["verdict"] == name]
        for name in ("stale", "wrong", "check", "unverifiable", "current")
    }

    columns = st.columns(5)
    for column, (name, caption) in zip(
        columns,
        (
            ("stale", "Documents something gone"),
            ("wrong", "Contradicted"),
            ("check", "Needs a human"),
            ("unverifiable", "Evidence silent"),
            ("current", "Still accurate"),
        ),
    ):
        column.metric(caption, len(buckets[name]))

    tabs = st.tabs([f"{name} ({len(buckets[name])})" for name in buckets])
    for tab, name in zip(tabs, buckets):
        with tab:
            if not buckets[name]:
                st.caption("Nothing in this bucket at the current thresholds.")
            for row in buckets[name]:
                with st.container(border=True):
                    st.markdown(f"**{row['section'] or 'document'}** — {row['text']}")
                    st.caption(
                        f"change: `{row['change']}` (conf {row['change_confidence']:.2f})"
                    )
                    marks = st.columns(3)
                    for column, (key, spec) in zip(marks, HAZARDS.items()):
                        value = row["probabilities"][key]
                        column.caption(f"{value:.2f} — {spec['label']}")

    st.divider()
    footer = st.columns(4)
    footer[0].metric("Claims", len(claims))
    footer[1].metric("Judgments", len(questions))
    footer[2].metric("Latency", f"{answers.elapsed_s:.1f} s")
    footer[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{len(claims)} claims × {QUESTIONS_PER_CLAIM} questions = {len(questions)} judgments "
        f"in 1 request, {answers.input_tokens:,} input tokens. Every claim was judged against "
        f"the whole evidence state, ingested once."
    )


if __name__ == "__main__":
    main()
