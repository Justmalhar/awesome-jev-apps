"""incident-tagger -- tag postmortems by cause class AND contributing factors.

Every incident review ends with a dropdown: pick a root cause. The dropdown is
a lie. An outage is a bad deploy AND no staging parity AND an alert that fired
into a channel nobody reads AND a rollback that needed a human who was asleep.
Forcing one label discards the three findings that would actually have
prevented the next one -- and those are the ones that repeat across incidents.

So: one Choice for the cause class, because an incident is triggered by one
thing; and one independent NOUL per contributing factor, because several are
true at once. Python then sums which factor recurs most across the corpus,
which is the report nobody has time to write by hand.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice, score

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_postmortems.json"
TEXT_CHARS = 2500

CAUSE_CLASSES = {
    "code_deploy": "A code change that shipped and behaved differently in production than anywhere it had run before.",
    "config_change": "A setting, flag, limit, or policy was edited, and the edit itself is what broke things.",
    "capacity": "Demand exceeded what the system was provisioned for; nothing was wrong except the size of it.",
    "dependency_failure": "A third party or another team's service failed, and this system followed it down.",
    "data_issue": "Bad, missing, or corrupted data moved through a path that assumed it would be well-formed.",
    "infrastructure": "Hardware, network, DNS, certificates, or the cloud provider failed underneath the application.",
    "latent_defect": "A bug that had been in production for a long time, triggered by an ordinary condition finally occurring.",
    "human_procedure": "Someone ran the right tool in the wrong place, or a runbook step did something other than what it said.",
    "unclear": "The write-up does not establish what set it off.",
}

# Independent properties. Each one is separately true or false of an incident,
# each one is separately actionable, and each gets its own threshold. They are
# deliberately NOT levels on one rubric and NOT options on one Choice.
FACTORS = {
    "detection_gap": (
        "Did this system fail to tell anyone it was broken, so that people found out some other way?",
        "Customers, another team, or a person happening to look is how it was noticed",
        "The system's own alerting reported it promptly to someone who could act",
    ),
    "no_safe_rollback": (
        "Was undoing the change harder or slower than making it?",
        "Reverting needed a migration, a manual step, a person with special access, or was not possible",
        "The change could be backed out quickly by the person who noticed",
    ),
    "environment_mismatch": (
        "Did this behave differently in production than it did wherever it was tested?",
        "Scale, data, configuration, or topology differed in a way that hid the problem before release",
        "Pre-production resembled production closely enough that the problem could have been seen there",
    ),
    "single_point_of_failure": (
        "Did one component's failure take down more than itself?",
        "One node, region, service, or credential had no redundancy and its failure propagated",
        "The failure stayed contained, or redundancy worked as intended",
    ),
    "missing_ownership": (
        "Was there confusion or delay about who was responsible for responding?",
        "Time was lost finding an owner, paging the wrong team, or waiting for someone with access",
        "The right people were engaged promptly",
    ),
    "manual_step_in_critical_path": (
        "Did recovery depend on a person performing steps by hand?",
        "Someone had to run commands, edit records, or copy values for the system to recover",
        "Recovery was automatic once the trigger was addressed",
    ),
    "known_risk_accepted": (
        "Had this risk been identified before the incident and left unaddressed?",
        "The write-up refers to a prior incident, a backlog item, or a warning about exactly this",
        "Nobody had previously identified this specific risk",
    ),
    "alert_noise": (
        "Was a signal that could have caught this ignored or lost among others?",
        "An alert fired and was muted, missed, routed to an unread channel, or dismissed as routine",
        "No relevant signal fired, or the one that fired was acted on",
    ),
}

RECURRENCE_LEVELS = [
    "The exact conditions are gone and could not assemble again in this system",
    "It could happen again only if somebody repeated an unusual action deliberately",
    "The same trigger is likely to recur on an ordinary day, and the same outcome would follow",
    "It is recurring already, or nothing at all has changed since it happened",
]


def load_incidents(raw: str) -> list[dict]:
    incidents = json.loads(raw)
    return [
        {
            "id": item.get("id", index),
            "title": str(item.get("title", "")).strip(),
            "text": str(item.get("text", ""))[:TEXT_CHARS],
        }
        for index, item in enumerate(incidents)
    ]


def build_questions(incidents: list[dict]) -> dict:
    """One Choice + one Score + eight independent Nouls per incident, batched."""
    questions: dict[str, dict] = {}
    for index in range(len(incidents)):
        reference = {"title": f"`incidents[{index}].title`", "postmortem": f"`incidents[{index}].text`"}
        questions[f"cause_{index}"] = choice(
            {"task": "What set this incident off?", "incident": reference}, CAUSE_CLASSES
        )
        questions[f"recurrence_{index}"] = score(
            {"task": "How likely is this same incident to happen again as things stand?",
             "incident": reference},
            RECURRENCE_LEVELS,
        )
        for name, (task, true, false) in FACTORS.items():
            questions[f"{name}_{index}"] = noul(
                {"task": task, "incident": reference}, true=true, false=false
            )
    return questions


def tally(tagged: list[dict], gate: float) -> list[dict]:
    """Which factors recur across the corpus. Python counts; the model never does."""
    rows = []
    for name in FACTORS:
        hits = [item for item in tagged if item["factors"][name] >= gate]
        rows.append(
            {
                "factor": name,
                "incidents": len(hits),
                "share": round(len(hits) / len(tagged), 3) if tagged else 0.0,
                "examples": ", ".join(str(item["id"]) for item in hits[:4]),
            }
        )
    return sorted(rows, key=lambda row: -row["incidents"])


def main() -> None:
    st.set_page_config(page_title="Incident Tagger", page_icon="🚨", layout="wide")
    st.title("🚨 Incident Tagger")
    st.caption("One cause class, many contributing factors — because that is how incidents work.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}", language=None)
        st.divider()
        st.subheader("Policy (no inference)")
        factor_gate = st.slider("P(factor applies) to count it", 0.0, 1.0, 0.6, 0.05)
        cause_gate = st.slider("Confidence to trust the cause class", 0.0, 1.0, 0.5, 0.05)
        recurrence_gate = st.slider("Recurrence score that demands an action item", 0.0, 3.0, 2.0, 0.1)
        batch_size = st.slider("Incidents per request", 1, 20, 5, 1)

    uploaded = st.file_uploader("Postmortems JSON: [{id, title, text}]", type=["json"])
    incidents = load_incidents(
        uploaded.read().decode("utf-8") if uploaded else SAMPLE.read_text(encoding="utf-8")
    )
    if not uploaded:
        st.caption(f"Using the bundled sample: {len(incidents)} postmortems.")
    if not incidents:
        st.warning("No incidents to tag.")
        return

    st.caption(f"{len(FACTORS)} independent factors + cause class + recurrence "
               f"= {len(FACTORS) + 2} questions per incident.")
    if not st.button(f"Tag {len(incidents)} postmortems", type="primary"):
        return

    groups = [incidents[i:i + batch_size] for i in range(0, len(incidents), batch_size)]
    tagged: list[dict] = []
    cost = elapsed = 0.0
    tokens = asked = 0

    try:
        with st.spinner(f"Tagging in {len(groups)} request(s)..."):
            with JevClient(provider=provider) as client:
                for group in groups:
                    questions = build_questions(group)
                    answers = client.ask({"incidents": group}, questions)
                    cost += answers.cost_usd
                    tokens += answers.input_tokens
                    elapsed += answers.elapsed_s
                    asked += len(questions)
                    for index, incident in enumerate(group):
                        confidence = answers.confidence(f"cause_{index}")
                        tagged.append(
                            {
                                "id": incident["id"],
                                "title": incident["title"],
                                "cause": (answers.choice(f"cause_{index}")
                                          if confidence >= cause_gate else "unclear"),
                                "cause_confidence": round(confidence, 3),
                                "recurrence": round(answers.score(f"recurrence_{index}"), 3),
                                "factors": {
                                    name: round(answers.noul(f"{name}_{index}"), 3) for name in FACTORS
                                },
                            }
                        )
    except JevError as exc:
        st.error(str(exc))
        return

    st.subheader("What keeps coming back")
    st.caption("Counted in Python across every incident, at the factor threshold in the sidebar. "
               "This is the list the quarterly review should be arguing about.")
    st.dataframe(tally(tagged, factor_gate), width="stretch")

    st.subheader("Cause classes")
    causes: dict[str, int] = {}
    for item in tagged:
        causes[item["cause"]] = causes.get(item["cause"], 0) + 1
    st.bar_chart(causes)

    st.subheader("Per incident")
    for item in sorted(tagged, key=lambda i: -i["recurrence"]):
        applying = [name for name, value in item["factors"].items() if value >= factor_gate]
        with st.container(border=True):
            st.markdown(f"**{item['id']} · {item['title']}**")
            st.caption(
                f"cause: `{item['cause']}` (confidence {item['cause_confidence']:.2f}) · "
                f"recurrence {item['recurrence']:.2f}/3"
                + ("  ·  ⚠️ needs an action item" if item["recurrence"] >= recurrence_gate else "")
            )
            st.write(" ".join(f"`{name}`" for name in applying) if applying
                     else "_no contributing factor above the threshold_")
            with st.expander("All factor probabilities"):
                st.json(item["factors"])

    st.divider()
    st.caption(
        f"{asked} question(s) over {len(tagged)} incident(s) in {len(groups)} request(s) · "
        f"{tokens:,} tokens · ${cost:.6f} · {elapsed:.2f}s measured "
        f"(${cost / max(len(tagged), 1):.8f} per postmortem)."
    )


if __name__ == "__main__":
    main()
