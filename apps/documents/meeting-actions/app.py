"""Meeting Actions -- commitments, owners and deadlines out of a transcript.

The rule this app is built around: **it never invents an owner and never
invents a deadline.** Jev cannot generate text, only select among options, so:

    owner   -> a Choice whose options are the speaker names Python extracted
               from the transcript, plus "unassigned" and "external"
    when    -> a Choice whose options are the deadline-shaped spans Python
               found in that specific turn, plus "none"
    action  -> the turn's own text, copied verbatim. Nothing is rewritten.

An LLM summariser asked for "action items with owners" will confidently assign
one when nobody volunteered, because the output shape demands a name. Here the
only names on offer are people who actually spoke, and "nobody was made
responsible" is a first-class answer rather than an inconvenient one.

Three independent properties get three separate Nouls, because they are
independently true and independently tunable:

    is_commitment   did the speaker commit, or merely discuss?
    still_stands    does it survive to the end of the meeting, or get retracted?
    has_deadline    was a time stated at all? (catches deadlines the regex missed)

Relative deadlines stay relative. "End of next sprint" is surfaced verbatim and
labelled relative in Python -- jev-1.13 cannot compare or compute dates, so it
is never asked to resolve one.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_transcript.txt"

CHARS_PER_TOKEN = 4
# ponytail: turns this short are acknowledgements ("Go ahead.", "And?") and
# never carry a commitment. Dropping them keeps the request from doubling in
# size. Lower it if your transcripts have terse committers.
MIN_TURN_CHARS = 20

SPEAKER = re.compile(r"^([A-Z][\w .'’-]{0,30}):\s*(.*)$")

# Ordered most-specific first; earlier matches claim their characters so that
# "Friday this week" does not also yield a nested duplicate.
DEADLINE_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\bend of (?:the\s+)?(?:next\s+|this\s+|last\s+)?"
    r"(?:week|month|quarter|sprint|day|year|business day)\b",
    r"\b(?:this|next|last)\s+"
    r"(?:week|month|quarter|sprint|year|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    r"\b(?:tomorrow|today|tonight|overnight)\b",
    r"\bQ[1-4]\b",
    r"\bEO[DW]\b",
]

# A span is "absolute" only if it names a calendar position. Everything else --
# "Friday", "end of next sprint" -- needs a meeting date to resolve, which is
# your calendar's job, not the model's.
ABSOLUTE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}"
)

UNASSIGNED = "unassigned"
EXTERNAL = "external"
NO_DEADLINE = "none"


# ---------------------------------------------------------------------------
# Python does the splitting and the span-finding
# ---------------------------------------------------------------------------


def split_utterances(text: str) -> list[dict]:
    """One record per speaker turn. Continuation lines fold into the turn above.

    Text before the first speaker label (a title, a date line) is dropped: it
    belongs to nobody, so it can carry no commitment.
    """
    turns: list[dict] = []
    for line in text.splitlines():
        match = SPEAKER.match(line.strip())
        if match:
            turns.append({"speaker": match[1].strip(), "text": match[2].strip()})
        elif turns and line.strip():
            turns[-1]["text"] = f"{turns[-1]['text']} {line.strip()}".strip()
    for index, turn in enumerate(turns):
        turn["index"] = index
    return turns


def roster(turns: list[dict]) -> list[str]:
    """Speakers who actually said something, in first-appearance order.

    This list *is* the option set for the owner Choice. A name that never
    appears here cannot be returned as an owner -- that is the guarantee.
    """
    names: list[str] = []
    for turn in turns:
        if turn["speaker"] not in names:
            names.append(turn["speaker"])
    return names


def find_deadlines(text: str) -> list[str]:
    """Verbatim deadline-shaped spans, in the order they appear in the turn."""
    claimed: list[tuple[int, int]] = []
    spans: list[tuple[int, str]] = []
    for pattern in DEADLINE_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            claimed.append((start, end))
            spans.append((start, match.group()))
    spans.sort()
    seen: set[str] = set()
    return [s for _, s in spans if not (s in seen or seen.add(s))]


def is_relative(span: str) -> bool:
    """"14 March" is absolute; "Friday" and "end of next sprint" are not."""
    return ABSOLUTE.search(span) is None


def candidate_turns(turns: list[dict]) -> list[dict]:
    return [t for t in turns if len(t["text"]) >= MIN_TURN_CHARS]


# ---------------------------------------------------------------------------
# One request: three Nouls and up to two Choices per candidate turn
# ---------------------------------------------------------------------------


def owner_options(names: list[str]) -> dict[str, str]:
    options = {name: f"{name} was made responsible for doing the work." for name in names}
    # Without these two the model must name somebody who spoke, which is
    # exactly the hallucinated-owner failure this app exists to prevent.
    options[UNASSIGNED] = (
        "The turn says the work should happen but makes no specific person "
        "responsible for it, for example 'someone should do this'."
    )
    options[EXTERNAL] = (
        "Responsibility is placed on a named person who does not speak anywhere "
        "in this transcript."
    )
    return options


def build_questions(turns: list[dict], names: list[str]) -> dict:
    """Every judgment about every candidate turn, in one request."""
    questions: dict[str, dict] = {}
    for turn in turns:
        index = turn["index"]
        reference = {"speaker": f"`turns[{index}].speaker`", "said": f"`turns[{index}].text`"}

        questions[f"commit_{index}"] = noul(
            {"task": "In this turn, was a specific piece of work actually taken on?",
             "turn": reference},
            true="Someone is put on the hook for a specific piece of work, either by "
                 "volunteering for it or by being assigned it in this turn",
            false="The turn discusses, proposes, questions, complains, or agrees in "
                  "principle, without anybody taking on a specific piece of work",
        )
        questions[f"stands_{index}"] = noul(
            {"task": "Reading the whole transcript, does whatever was taken on in this "
                     "turn still hold by the close of the meeting?",
             "turn": reference},
            true="Nothing later in the transcript withdraws, cancels or supersedes it",
            false="A later turn calls it off, tells the owner not to do it, or replaces "
                  "it with a different plan",
        )
        questions[f"timed_{index}"] = noul(
            {"task": "Does this turn state a time by which the work is meant to be done?",
             "turn": reference},
            true="The turn gives a deadline, whether as a date, a weekday, or a phrase "
                 "such as 'end of next sprint'",
            false="No timing is given in this turn",
        )
        questions[f"owner_{index}"] = choice(
            {"task": "Who was made responsible for the work discussed in this turn?",
             "turn": reference,
             "note": "The speaker is the owner only when they took the work on "
                     "themselves. A person spoken about is the owner when the work "
                     "is placed on them."},
            owner_options(names),
        )

        spans = turn["deadline_spans"]
        if spans:
            options = {span: f"The turn gives the timing as: {span}" for span in spans}
            options[NO_DEADLINE] = (
                "None of these spans is the deadline for the work in this turn."
            )
            questions[f"deadline_{index}"] = choice(
                {"task": "Which of these spans, quoted from this turn, gives the time "
                         "by which the work is meant to be done?",
                 "turn": reference},
                options,
            )
    return questions


# ---------------------------------------------------------------------------
# Policy. Lives here, not in an instruction, so tuning re-runs no inference.
# ---------------------------------------------------------------------------


def decide(commit_p: float, stands_p: float, owner: str, owner_confidence: float,
           commit_threshold: float, stands_threshold: float,
           owner_threshold: float) -> tuple[str, str]:
    """Bucket one turn. Order matters: a retracted commitment is not an action
    that merely lacks an owner, so the veto is applied before the owner test."""
    if commit_p < commit_threshold:
        return "discarded", f"nobody took work on here (P={commit_p:.2f})"
    if stands_p < stands_threshold:
        return "discarded", f"called off later in the meeting (P stands={stands_p:.2f})"
    if owner == UNASSIGNED:
        return "needs owner", "stated as work to do, but nobody was put on it"
    if owner == EXTERNAL:
        return "needs owner", "placed on somebody who was not in the room"
    if owner_confidence < owner_threshold:
        return "needs owner", f"owner is ambiguous (confidence={owner_confidence:.2f})"
    return "action", f"{owner} took it on (P={commit_p:.2f})"


def load_sample() -> str:
    return SAMPLE.read_text(encoding="utf-8")


def prepare(text: str) -> tuple[list[dict], list[str], list[dict]]:
    turns = split_utterances(text)
    for turn in turns:
        turn["deadline_spans"] = find_deadlines(turn["text"])
    return turns, roster(turns), candidate_turns(turns)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Meeting Actions", page_icon="📝", layout="wide")
    st.title("📝 Meeting Actions")
    st.caption(
        "Commitments, owners and deadlines from a transcript. "
        "**Owners are selected from the people who spoke — never invented.**"
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
        st.subheader("Policy")
        commit_threshold = st.slider("Treat as a commitment at or above", 0.0, 1.0, 0.60, 0.05)
        stands_threshold = st.slider("Treat as still standing at or above", 0.0, 1.0, 0.50, 0.05)
        owner_threshold = st.slider("Owner needs this confidence", 0.0, 1.0, 0.55, 0.05)
        st.caption("Bias low on commitment: a false action item is cheap to delete, "
                   "a missed one is not.")

    uploaded = st.file_uploader("Upload a transcript (.txt)", type=["txt"])
    text = uploaded.read().decode("utf-8", errors="replace") if uploaded else load_sample()
    text = st.text_area("Transcript", value=text, height=260)

    if not text.strip():
        st.info("Paste a transcript with `Speaker: text` lines.")
        return

    turns, names, candidates = prepare(text)
    if not candidates:
        st.warning("No speaker turns found. Lines must look like `Priya: ...`.")
        return

    st.caption(
        f"{len(turns)} turns · {len(candidates)} long enough to carry a commitment · "
        f"owner options: {', '.join(names)}, {UNASSIGNED}, {EXTERNAL}"
    )

    if not st.button("Extract actions", type="primary"):
        st.info("The bundled sample transcript is loaded. Hit Extract actions.")
        return

    questions = build_questions(candidates, names)
    if (len(text) + sum(len(t["text"]) for t in candidates)) // CHARS_PER_TOKEN > provider.context_tokens:
        st.error(
            f"This transcript exceeds {provider.name}'s {provider.context_tokens:,} "
            "token context. Split it by agenda item."
        )
        return

    state = {
        "turns": [{"speaker": t["speaker"], "text": t["text"]} for t in turns],
        "transcript": text,
    }
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    for turn in candidates:
        index = turn["index"]
        commit_p = answers.noul(f"commit_{index}")
        stands_p = answers.noul(f"stands_{index}")
        stated_deadline = answers.noul(f"timed_{index}")
        owner = answers.choice(f"owner_{index}")
        owner_confidence = answers.probabilities(f"owner_{index}").get(owner, 0.0)

        deadline, relative = "", ""
        if f"deadline_{index}" in questions:
            picked = answers.choice(f"deadline_{index}")
            if picked != NO_DEADLINE:
                deadline = picked
                relative = "relative" if is_relative(picked) else "absolute"
        if not deadline and stated_deadline >= 0.5:
            # The Noul saw timing the regex could not surface. Say so; do not
            # let the model write a deadline to fill the gap.
            relative = "stated, no span matched"

        bucket, reason = decide(
            commit_p, stands_p, owner, owner_confidence,
            commit_threshold, stands_threshold, owner_threshold,
        )
        rows.append({
            "bucket": bucket, "owner": owner, "action (verbatim)": turn["text"],
            "said by": turn["speaker"], "deadline": deadline, "deadline kind": relative,
            "P commit": round(commit_p, 3), "P stands": round(stands_p, 3),
            "owner conf": round(owner_confidence, 3), "why": reason,
        })

    frame = pd.DataFrame(rows)
    buckets = ("action", "needs owner", "discarded")
    tabs = st.tabs([f"{name.title()} ({int((frame['bucket'] == name).sum())})" for name in buckets])

    for tab, name in zip(tabs, buckets):
        with tab:
            subset = frame[frame["bucket"] == name]
            if subset.empty:
                st.caption("Nothing here.")
                continue
            columns = ["owner", "action (verbatim)", "deadline", "deadline kind",
                       "P commit", "P stands", "owner conf", "why"]
            if name == "discarded":
                columns = ["said by", "action (verbatim)", "P commit", "P stands", "why"]
            st.dataframe(subset[columns], width="stretch", hide_index=True)

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Questions", len(questions))
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{answers.elapsed_s:.2f} s")
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{answers.input_tokens:,} input tokens over {len(candidates)} turns. "
        f"Every action text and every owner above is copied out of the transcript."
    )


if __name__ == "__main__":
    main()
