"""form-validator -- semantic field validation while the user is still typing.

Every validation pass is ONE request carrying one Noul per rule. The rules are
the ones a regex cannot state: is that a real job title, is that city actually
in that country, is this bio placeholder text someone forgot to replace.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul

HERE = Path(__file__).resolve().parent
SAMPLE = json.loads((HERE / "sample_profile.json").read_text(encoding="utf-8"))

FIELDS = ["full_name", "job_title", "company", "city", "country", "work_email", "bio"]

# qid -> (field it annotates, complain when P is BELOW/ABOVE, message)
RULES = {
    "name_is_a_person": ("full_name", "below", "That does not read like a person's name."),
    "title_is_a_real_job": ("job_title", "below", "That does not look like a job title an employer would post."),
    "company_is_an_org": ("company", "below", "That does not read like the name of an organisation."),
    "city_in_country": ("city", "below", "That city does not appear to be in the country you selected."),
    "email_matches_name": ("work_email", "below", "This address does not look like it belongs to the name given."),
    "bio_is_placeholder": ("bio", "above", "This looks like placeholder text rather than a real bio."),
}


def build_questions(form: dict) -> dict:
    """ONE request for the whole form. Independent rules, one Noul each.

    Several can fail at once, so they are Nouls rather than a Choice -- and each
    one carries its own threshold, because a wrong city is a hard stop while a
    thin bio is only a nudge.
    """
    return {
        "name_is_a_person": noul(
            {"task": "Is this the name of a person?", "value": "`form.full_name`"},
            true="A plausible personal name in any naming tradition",
            false="A company, a role, a placeholder, or keyboard mashing",
        ),
        "title_is_a_real_job": noul(
            {"task": "Is this a job title a real employer would put in a job posting?",
             "value": "`form.job_title`"},
            true="A recognisable role, including senior and niche ones",
            false="A joke title, a description of a person, or something no HR system would hold",
        ),
        "company_is_an_org": noul(
            {"task": "Does this read as the name of an organisation?", "value": "`form.company`"},
            true="A plausible company, agency, university, or other employer name",
            false="A person's name, a product, a placeholder, or gibberish",
        ),
        "city_in_country": noul(
            {"task": "Is the stated city located in the stated country?",
             "city": "`form.city`", "country": "`form.country`"},
            true="The city is in that country, including under a local or alternative spelling",
            false="The city is in a different country, or is not a city at all",
        ),
        "email_matches_name": noul(
            {"task": "Does this email address plausibly belong to the person named?",
             "email": "`form.work_email`", "person": "`form.full_name`"},
            true="The part before the @ is consistent with that person's name, including initials or a nickname",
            false="It names a different person, or is a shared mailbox such as info@ or sales@",
        ),
        "bio_is_placeholder": noul(
            {"task": "Is this text placeholder or filler rather than a real biography?",
             "value": "`form.bio`"},
            true="Lorem ipsum, keyboard mashing, a note to self, or a promise to write it later",
            false="Actual sentences describing this person's work, however short",
        ),
    }


def field_errors(signals: dict, threshold: float) -> dict[str, str]:
    """Which fields to mark, and with what text. Pure policy, no inference."""
    errors: dict[str, str] = {}
    for qid, (field, direction, message) in RULES.items():
        probability = signals.get(qid)
        if probability is None:
            continue
        # Both directions read the same way: how sure are we the rule is violated.
        violated = (1 - probability) if direction == "below" else probability
        failed = violated >= threshold
        if failed:
            errors[field] = message
    return errors


def main() -> None:
    st.set_page_config(page_title="Semantic Form Validator", page_icon="🧾", layout="wide")
    st.title("🧾 Semantic Form Validator")
    st.caption(
        "Validation a regex cannot express — checked while the user is still in the form, "
        "with the latency of each pass measured."
    )

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} tok", language=None)
        threshold = st.slider("Confidence needed to complain", 0.5, 1.0, 0.8, 0.05)
        st.caption("One threshold, applied in Python. Moving it re-marks the form with no new call.")

    history = st.session_state.setdefault("latencies", [])

    left, right = st.columns([1, 1])
    form = {}
    with left:
        form["full_name"] = st.text_input("Full name", SAMPLE["full_name"])
        form["job_title"] = st.text_input("Job title", SAMPLE["job_title"])
        form["company"] = st.text_input("Company", SAMPLE["company"])
        form["city"] = st.text_input("City", SAMPLE["city"])
        form["country"] = st.text_input("Country", SAMPLE["country"])
        form["work_email"] = st.text_input("Work email", SAMPLE["work_email"])
        form["bio"] = st.text_area("Short bio", SAMPLE["bio"], height=90)
        validate = st.button("Validate (fires on every edit in production)", type="primary")

    if not validate:
        right.info(
            "Edit any field and press **Validate**. In a real form this fires on the "
            "change event — the round trip is short enough to sit there."
        )
        return

    try:
        with JevClient(provider=provider) as client:
            answers = client.ask({"form": form}, build_questions(form))
    except JevError as exc:
        st.error(str(exc))
        return

    signals = {qid: answers.noul(qid) for qid in RULES}
    errors = field_errors(signals, threshold)
    history.append(answers.elapsed_s * 1000)

    with right:
        st.metric("This validation pass", f"{answers.elapsed_s * 1000:.0f} ms")
        if errors:
            for field, message in errors.items():
                st.error(f"**{field}** — {message}\n\n`{form[field][:70]}`")
        else:
            st.success("Nothing semantically wrong with this form.")
        st.dataframe(
            [
                {
                    "rule": qid,
                    "field": RULES[qid][0],
                    "P(yes)": round(probability, 3),
                    "marked": RULES[qid][0] in errors,
                }
                for qid, probability in signals.items()
            ],
            width="stretch",
        )

    st.divider()
    footer = st.columns(5)
    footer[0].metric("Rules per pass", len(RULES))
    footer[1].metric("Requests this pass", 1)
    footer[2].metric("Latency", f"{answers.elapsed_s * 1000:.0f} ms")
    footer[3].metric("Median over session", f"{statistics.median(history):.0f} ms")
    footer[4].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{answers.input_tokens:,} input tokens, measured. {len(history)} validation pass(es) this session. "
        f"Running these {len(RULES)} rules as separate calls would be {len(RULES)}x the round trips."
    )


if __name__ == "__main__":
    main()
