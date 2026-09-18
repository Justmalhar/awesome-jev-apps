"""Calendar Triage -- which meetings you could skip, shorten, or delegate.

Judged against priorities you write in one sentence. Jev never rewrites an
invite; it judges each one, and Python decides what that means for your week.

The hours reclaimed are arithmetic, so Python does them. The model is only
asked things a human would answer by reading the invite.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, choice, noul, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_calendar.json"

DEFAULT_PRIORITIES = (
    "This quarter I own the billing migration and the on-call rotation redesign. "
    "Everything else -- recruiting, roadmap theatre, other teams' status -- is "
    "background noise I should not be spending mornings on."
)

KINDS = {
    "decision": "A decision has to be made in the room and the attendees are the deciders.",
    "working": "People do the actual work together: pairing, design, review, debugging.",
    "status": "Round-the-table updates on progress that already happened.",
    "one_on_one": "A recurring two-person check-in with a manager, report, or peer.",
    "external": "Customers, vendors, candidates, or anyone outside the company attends.",
    "ceremony": "Standing team ritual: all-hands, sprint ceremony, retro, birthday call.",
    "unclear": "The invite does not say enough to tell what kind of meeting this is.",
}

VALUE_LEVELS = [
    "Nothing would go wrong if this person never attended; someone would send notes anyway",
    "Their absence would mean catching up on a written summary afterwards",
    "Their absence would slow a decision down by a day or two while someone chases them",
    "Their absence would stop the meeting achieving what it was called to achieve",
]


def load_events(path: Path | None) -> list[dict]:
    """A user-supplied calendar export, or the bundled sample."""
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8")) if source.is_file() else []


def build_questions(events: list[dict], priorities: str) -> dict:
    """Independent properties get their own Noul. Degree gets the one Score."""
    questions: dict[str, dict] = {}
    for index in range(len(events)):
        invite = {
            "title": f"`events[{index}].title`",
            "organizer": f"`events[{index}].organizer`",
            "attendees": f"`events[{index}].attendees`",
            "agenda": f"`events[{index}].agenda`",
            "recurring": f"`events[{index}].recurring`",
        }
        questions[f"needs_me_{index}"] = noul(
            {"task": "Does this meeting need this specific person's input, decision, or authority?",
             "invite": invite, "their_priorities": priorities},
            true="They own something on the agenda, or a decision waits on them personally",
            false="They are one of many attendees and nothing on the agenda is theirs",
        )
        questions[f"advances_{index}"] = noul(
            {"task": "Does this meeting move the stated priorities forward?",
             "invite": invite, "their_priorities": priorities},
            true="The subject is one of the priorities they wrote, or directly unblocks one",
            false="The subject is unrelated to the priorities, or is background work they said they care less about",
        )
        questions[f"delegable_{index}"] = noul(
            {"task": "Could a teammate attend in this person's place and report back without loss?",
             "invite": invite, "their_priorities": priorities},
            true="Any informed team member could represent them and relay the outcome",
            false="It relies on their own authority, relationships, or knowledge",
        )
        questions[f"agenda_{index}"] = noul(
            {"task": "Does this invite state a concrete agenda or a decision to be reached?",
             "invite": invite},
            true="The invite names what will be discussed or decided",
            false="Title only, a vague 'sync' / 'catch-up', or an empty description",
        )
        questions[f"kind_{index}"] = choice(
            {"task": "What kind of meeting is this?", "invite": invite}, KINDS
        )
        questions[f"value_{index}"] = score(
            {"task": "What would it cost if this person simply did not attend?",
             "invite": invite, "their_priorities": priorities},
            VALUE_LEVELS,
        )
    return questions


def decide(judgment: dict, *, need_gate: float, delegate_gate: float, value_gate: float) -> str:
    """Pure policy: gates live here so tuning them re-runs no inference."""
    needed = judgment["needs_me"] >= need_gate
    valuable = judgment["value"] >= value_gate
    if needed and valuable:
        return "attend"
    if judgment["delegable"] >= delegate_gate:
        return "delegate"
    if needed or valuable:
        return "shorten"
    return "skip"


def reclaimed_minutes(verdict: str, minutes: int) -> int:
    """Skipping or delegating frees the whole slot; shortening frees half of it."""
    return {"skip": minutes, "delegate": minutes, "shorten": minutes // 2}.get(verdict, 0)


def main() -> None:
    st.set_page_config(page_title="Calendar Triage", page_icon="📅", layout="wide")
    st.title("📅 Calendar Triage")
    st.caption("Skip, shorten, or delegate -- judged against priorities you write in a sentence.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Thresholds")
        st.caption("Move these freely -- re-deciding costs nothing.")
        need_gate = st.slider("P(needs me) to keep the slot", 0.0, 1.0, 0.55, 0.05)
        delegate_gate = st.slider("P(delegable) to hand it over", 0.0, 1.0, 0.65, 0.05)
        value_gate = st.slider("Cost-of-absence to defend the slot", 0.0, 3.0, 1.6, 0.1)
        calendar_path = st.text_input("Calendar JSON (optional)", value="")

    priorities = st.text_area("What actually matters this quarter?", value=DEFAULT_PRIORITIES, height=110)
    events = load_events(Path(calendar_path).expanduser() if calendar_path.strip() else None)
    if not events:
        st.warning("No events. Provide a calendar JSON, or restore sample_calendar.json.")
        return
    if not calendar_path.strip():
        st.caption(f"Using the bundled sample week: {len(events)} meetings.")

    if st.button(f"Triage {len(events)} meetings", type="primary"):
        questions = build_questions(events, priorities.strip())
        try:
            with st.spinner("Reading your week..."):
                with JevClient(provider=provider) as client:
                    answers = client.ask({"events": events, "priorities": priorities}, questions)
        except JevError as exc:
            st.error(str(exc))
            return
        st.session_state["week"] = {
            "events": events,
            "judgments": [
                {
                    "needs_me": answers.noul(f"needs_me_{i}"),
                    "advances": answers.noul(f"advances_{i}"),
                    "delegable": answers.noul(f"delegable_{i}"),
                    "agenda": answers.noul(f"agenda_{i}"),
                    "kind": answers.choice(f"kind_{i}"),
                    "kind_confidence": answers.confidence(f"kind_{i}"),
                    "value": answers.score(f"value_{i}"),
                }
                for i in range(len(events))
            ],
            "questions": len(questions),
            "tokens": answers.input_tokens,
            "cost": answers.cost_usd,
            "elapsed": answers.elapsed_s,
        }

    week = st.session_state.get("week")
    if not week:
        st.info("Hit the button to judge the week.")
        return

    # --- policy only, no inference below this line ---
    buckets: dict[str, list[tuple[dict, dict]]] = {"attend": [], "shorten": [], "delegate": [], "skip": []}
    saved = 0
    for event, judgment in zip(week["events"], week["judgments"]):
        verdict = decide(judgment, need_gate=need_gate, delegate_gate=delegate_gate, value_gate=value_gate)
        buckets[verdict].append((event, judgment))
        saved += reclaimed_minutes(verdict, int(event.get("minutes", 30)))

    booked = sum(int(e.get("minutes", 30)) for e in week["events"])
    columns = st.columns(4)
    columns[0].metric("Booked", f"{booked / 60:.1f} h")
    columns[1].metric("Reclaimable", f"{saved / 60:.1f} h")
    columns[2].metric("Meetings kept as-is", len(buckets["attend"]))
    columns[3].metric("Cost", f"${week['cost']:.6f}")

    labels = {
        "skip": "🗑️ Decline", "delegate": "🤝 Send someone else",
        "shorten": "✂️ Cut it short", "attend": "✅ Keep it",
    }
    tabs = st.tabs([f"{labels[key]} ({len(buckets[key])})" for key in ("skip", "delegate", "shorten", "attend")])
    for tab, key in zip(tabs, ("skip", "delegate", "shorten", "attend")):
        with tab:
            if not buckets[key]:
                st.caption("Nothing here.")
            for event, judgment in sorted(buckets[key], key=lambda pair: -pair[0].get("minutes", 30)):
                flags = []
                if judgment["agenda"] < 0.4:
                    flags.append("📭 no agenda -- ask for one before accepting")
                if judgment["advances"] < 0.35:
                    flags.append("🎯 off your priorities")
                if judgment["kind_confidence"] < 0.5:
                    flags.append("❓ invite too thin to classify")
                with st.container(border=True):
                    st.markdown(f"**{event['title']}** · {event.get('minutes', 30)} min")
                    st.caption(
                        f"{event.get('day', '')} · {event.get('organizer', '')} · {judgment['kind']} · "
                        f"needs you P={judgment['needs_me']:.2f} · delegable P={judgment['delegable']:.2f} · "
                        f"absence cost {judgment['value']:.2f}/3"
                        + ("  ·  " + " · ".join(flags) if flags else "")
                    )
                    if event.get("agenda"):
                        with st.expander("Invite text"):
                            st.text(event["agenda"])

    st.divider()
    st.caption(
        f"{len(week['events'])} meetings × 6 judgments = {week['questions']} questions in 1 request · "
        f"{week['elapsed']:.1f}s · {week['tokens']:,} input tokens · ${week['cost']:.6f}. "
        "Every threshold above re-decides the week for $0.00."
    )


if __name__ == "__main__":
    main()
