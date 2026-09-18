"""Contract Diff -- "our terms have changed": what actually got worse for you.

A redline tells you *which* words moved. It cannot tell you whether the move
was adverse. This app splits those two jobs along the line where each is
reliable:

  * Python `difflib` decides WHICH clauses changed. Alignment, pairing and
    renumbering are mechanical, so no model is asked to do them.
  * Jev judges MATERIALITY of each change it is handed: adverse to the
    customer, imposes a new obligation, and how severe the consequence is.

Those three properties are independent -- a clause can add an obligation
without being adverse, and can be adverse without adding one -- so they are
three separate questions with three separate thresholds, not one rubric.

    uv run streamlit run app.py
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, score

HERE = Path(__file__).resolve().parent
CHARS_PER_TOKEN = 4  # rough, only used to warn before the provider rejects the call
PAIR_CUTOFF = 0.45  # below this similarity a clause is a deletion, not an edit

SEVERITY_LEVELS = [
    "Wording was tidied up; the customer's rights and obligations are identical to before",
    "The customer now has a shorter window to act, a new notification duty, or less discretion",
    "The customer loses a remedy, a refund right, a protection, or the ability to bring a claim",
]


def split_clauses(text: str) -> list[str]:
    """Numbered clauses, one per paragraph. Anything unnumbered is a heading."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    clauses = [p for p in paragraphs if re.match(r"^\d+\.\s", p)]
    return clauses or paragraphs


def body(clause: str) -> str:
    """Clause text with its number stripped and whitespace collapsed.

    Stripping the number is what makes a deletion not look like fourteen edits:
    remove clause 9 and every clause after it renumbers, but the bodies match.
    """
    return " ".join(re.sub(r"^\d+\.\s*", "", clause).split())


def _pair_block(old_block: list[str], new_block: list[str], cutoff: float) -> list[dict]:
    """Greedily pair the clauses inside one replace-block by similarity.

    O(n*m) inside a block, which is a handful of clauses in practice.
    """
    changes: list[dict] = []
    used: set[int] = set()
    for old in old_block:
        best, best_ratio = None, cutoff
        for index, new in enumerate(new_block):
            if index in used:
                continue
            ratio = SequenceMatcher(None, body(old), body(new), autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = index, ratio
        if best is None:
            changes.append({"kind": "removed", "before": old, "after": ""})
        else:
            used.add(best)
            changes.append({"kind": "modified", "before": old, "after": new_block[best]})
    for index, new in enumerate(new_block):
        if index not in used:
            changes.append({"kind": "added", "before": "", "after": new})
    return changes


def diff_clauses(old: list[str], new: list[str], cutoff: float = PAIR_CUTOFF) -> list[dict]:
    """Which clauses changed, and how. Pure Python -- never asked of the model."""
    matcher = SequenceMatcher(None, [body(c) for c in old], [body(c) for c in new], autojunk=False)
    changes: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            changes += [{"kind": "removed", "before": c, "after": ""} for c in old[i1:i2]]
        elif tag == "insert":
            changes += [{"kind": "added", "before": "", "after": c} for c in new[j1:j2]]
        else:
            changes += _pair_block(old[i1:i2], new[j1:j2], cutoff)
    return changes


def build_questions(changes: list[dict]) -> dict:
    """Three independent judgments per changed clause, all in one request."""
    questions: dict[str, dict] = {}
    for index, change in enumerate(changes):
        reference = {
            "change_type": change["kind"],
            "old_wording": f"`changes[{index}].before`",
            "new_wording": f"`changes[{index}].after`",
        }
        questions[f"adverse_{index}"] = noul(
            {
                "task": "Is this change to a Terms of Service adverse to the customer, "
                        "judged from the customer's side rather than the vendor's?",
                "note": "An empty old_wording means the clause is brand new. "
                        "An empty new_wording means the clause was struck out.",
                "change": reference,
            },
            true="The customer is worse off under the new wording: a right narrowed, a "
                 "protection withdrawn, a vendor discretion widened, or a burden shifted onto them",
            false="The customer is unaffected or better off: pure rewording, a clarification, "
                  "or a term that gives the customer more than before",
        )
        questions[f"obligation_{index}"] = noul(
            {
                "task": "Does the new wording put a duty on the customer that the old wording "
                        "did not put on them?",
                "note": "Judge only duties owed by the customer, not duties owed by the vendor.",
                "change": reference,
            },
            true="The customer must now do, provide, warrant, or refrain from something new",
            false="The customer's duties are unchanged, or the change only alters vendor duties",
        )
        questions[f"severity_{index}"] = score(
            {
                "task": "Rate the consequence of this change for a customer who is already "
                        "using the service under the old wording.",
                "change": reference,
            },
            SEVERITY_LEVELS,
        )
    return questions


def decide(
    adverse: float,
    obligation: float,
    severity: float,
    t_adverse: float,
    t_obligation: float,
    t_severity: float,
) -> tuple[str, str]:
    """Policy lives here, so retuning a threshold re-runs no inference."""
    if adverse >= t_adverse and severity >= t_severity:
        return "worse", f"adverse P={adverse:.2f} at severity {severity:.2f}"
    if adverse >= t_adverse:
        return "watch", f"adverse P={adverse:.2f} but severity only {severity:.2f}"
    if obligation >= t_obligation:
        return "watch", f"new customer obligation P={obligation:.2f}"
    return "benign", f"adverse P={adverse:.2f}, severity {severity:.2f}"


VERDICT_ORDER = {"worse": 0, "watch": 1, "benign": 2}
VERDICT_ICON = {"worse": "🔴", "watch": "🟠", "benign": "⚪"}


def main() -> None:
    st.set_page_config(page_title="Contract Diff", page_icon="📜", layout="wide")
    st.title("📜 Contract Diff")
    st.caption(
        "Two versions of the same terms. `difflib` finds what moved; "
        "Jev judges what got **worse for you**."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        st.divider()
        st.subheader("Thresholds")
        st.caption("Tune these against your own redlines; they are not universal.")
        t_adverse = st.slider("P(adverse) at or above this counts as adverse", 0.3, 0.99, 0.6, 0.01)
        t_severity = st.slider("Severity at or above this escalates to 'worse'", 0.0, 2.0, 1.0, 0.05)
        t_obligation = st.slider("P(new obligation) at or above this warrants a look", 0.3, 0.99, 0.7, 0.01)
        st.divider()
        cutoff = st.slider("Clause-pairing similarity cutoff", 0.1, 0.9, PAIR_CUTOFF, 0.05)
        st.caption("Below this, an edited clause is reported as a deletion plus an addition.")

    old_default = (HERE / "sample_terms_v1.txt").read_text(encoding="utf-8")
    new_default = (HERE / "sample_terms_v2.txt").read_text(encoding="utf-8")

    left, right = st.columns(2)
    old_text = left.text_area("Old version", value=old_default, height=300)
    new_text = right.text_area("New version", value=new_default, height=300)

    if not st.button("Diff and judge", type="primary"):
        st.info("The bundled Northwind Cloud terms are loaded. Press the button.")
        return
    if not old_text.strip() or not new_text.strip():
        st.warning("Both versions are required.")
        return

    changes = diff_clauses(split_clauses(old_text), split_clauses(new_text), cutoff)
    if not changes:
        st.success("No clause changed. Nothing to judge, and nothing was sent.")
        return

    estimated_tokens = (len(old_text) + len(new_text)) // CHARS_PER_TOKEN
    if estimated_tokens > provider.context_tokens:
        st.error(
            f"~{estimated_tokens:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Diff a section at a time, or switch provider."
        )
        return

    questions = build_questions(changes)
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"changes": changes}, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows = []
    for index, change in enumerate(changes):
        adverse = answers.noul(f"adverse_{index}")
        obligation = answers.noul(f"obligation_{index}")
        severity = answers.score(f"severity_{index}")
        verdict, reason = decide(
            adverse, obligation, severity, t_adverse, t_obligation, t_severity
        )
        rows.append((verdict, reason, change, adverse, obligation, severity))

    rows.sort(key=lambda r: (VERDICT_ORDER[r[0]], -r[5]))

    worse = [r for r in rows if r[0] == "worse"]
    st.subheader(f"{len(worse)} change(s) that got worse for the customer")

    for verdict, reason, change, adverse, obligation, severity in rows:
        with st.container(border=True):
            st.markdown(f"{VERDICT_ICON[verdict]} **{verdict.upper()}** · {change['kind']} · {reason}")
            columns = st.columns(3)
            columns[0].metric("P(adverse)", f"{adverse:.2f}")
            columns[1].metric("P(new obligation)", f"{obligation:.2f}")
            columns[2].metric("Severity", f"{severity:.2f} / {len(SEVERITY_LEVELS) - 1}")
            before, after = st.columns(2)
            before.markdown("*Before*")
            before.write(change["before"] or "_(clause did not exist)_")
            after.markdown("*After*")
            after.write(change["after"] or "_(clause was struck out)_")

    st.divider()
    st.caption(
        f"{len(changes)} changed clauses × 3 judgments = {len(questions)} questions, "
        f"1 request, {answers.elapsed_s:.1f}s, ${answers.cost_usd:.6f} "
        f"({answers.input_tokens:,} input tokens)."
    )


if __name__ == "__main__":
    main()
