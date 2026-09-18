"""Address Normalizer -- reconcile inconsistent address strings into fields.

THE STRUCTURAL IDEA WORTH STEALING: Jev cannot generate text, it selects. So
PYTHON ENUMERATES THE PARSE CANDIDATES and Jev picks the one the writer meant.
For each messy string this module produces 3-6 plausible structured readings --
different split points for unit / house number / street / locality / city /
region / postcode -- and asks one Choice over that address's own candidates,
plus a `none_correct` option meaning every reading misfiles something.

"Flat 2 12 High St" has two readings a regex cannot rank. A human ranks it
instantly, and so does a judgment model, because the ambiguity is semantic and
not lexical. Enumeration is cheap and total; ranking is the expensive part, and
that is the part worth paying a model for.

Two further properties ride along as SEPARATE Nouls, because they are
independent of the parse and of each other: the string may be missing a
component post needs, and it may be a PO box / care-of line rather than a place
you can stand in front of.

Every count and rate on screen is computed by pandas. The model is never asked
a number.

    uv run streamlit run app.py
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, load_provider, noul, choice

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_addresses.csv"

FIELDS = ("unit", "house_number", "street", "locality", "city", "region", "postcode")

# UK outward+inward codes, US 5 or 5+4 ZIPs, and the 4-6 digit blocks used almost
# everywhere else. Deliberately loose: this only proposes a reading, Jev ranks it.
POSTCODE_RE = re.compile(
    r"^(?:[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|\d{5}-\d{4}|\d{5}-\d{3}|\d{3}-\d{4}|\d{4,6})$",
    re.IGNORECASE,
)
REGION_RE = re.compile(r"^[A-Z]{2,3}$", re.IGNORECASE)
NUMBER_RE = re.compile(r"^\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?$", re.IGNORECASE)
UNIT_WORDS = {
    "flat", "apt", "apartment", "unit", "suite", "ste", "no", "room", "rm",
    "level", "floor", "fl", "box", "shop", "office", "bldg", "building",
}


def strip_postcode(text: str) -> tuple[str, str]:
    """Pull a trailing postal code off the whole string before comma logic runs."""
    tokens = text.split()
    for span in (2, 1):
        if len(tokens) > span and POSTCODE_RE.match(" ".join(tokens[-span:])):
            return " ".join(tokens[:-span]).rstrip(" ,"), " ".join(tokens[-span:])
    return text, ""


def segmentations(body: str) -> list[tuple[str, list[str]]]:
    """(premises segment, trailing segments).

    With commas: the writer's own split, plus one variant that folds the second
    segment back into the premises, because "Apartment 4, 88 Kings Rd, ..." puts
    the unit and the house number either side of a comma.

    Without commas: every plausible word split between the street part and the
    settlement part. That is precisely the guess a regex has to make silently,
    and we would rather enumerate it and have it ranked.
    """
    parts = [p.strip() for p in body.split(",") if p.strip()]
    if not parts:
        return []
    if len(parts) > 1:
        return [(parts[0], parts[1:]), (f"{parts[0]} {parts[1]}", parts[2:])]
    tokens = parts[0].split()
    ranked = []
    for tail_len in (1, 2, 3):
        if len(tokens) > tail_len + 1:
            tail = tokens[-tail_len:]
            ranked.append(
                (0 if REGION_RE.match(tail[-1]) else 1, tail_len,
                 " ".join(tokens[:-tail_len]), " ".join(tail))
            )
    ranked.sort()
    return [(head, [tail]) for _, _, head, tail in ranked] + [(parts[0], [])]


def head_readings(head: str) -> list[dict[str, str]]:
    """Readings of the premises segment: which token is the unit, which the house."""
    tokens = head.split()
    if not tokens:
        return [{}]
    lowered = [t.lower().strip(".#") for t in tokens]
    reads: list[dict[str, str]] = []

    def splits(prefix: int, unit: str) -> None:
        rest = tokens[prefix:]
        base = {"unit": unit} if unit else {}
        if not rest:
            reads.append(dict(base))
            return
        if NUMBER_RE.match(rest[0]):
            reads.append({**base, "house_number": rest[0], "street": " ".join(rest[1:])})
        if len(rest) > 1 and NUMBER_RE.match(rest[-1]):
            # much of the world writes the number after the street name
            reads.append({**base, "house_number": rest[-1], "street": " ".join(rest[:-1])})
        reads.append({**base, "street": " ".join(rest)})

    # unit designator leading the segment: "Flat 2 12 High St", "PO Box 1234"
    if len(tokens) >= 3 and (lowered[0] in UNIT_WORDS or lowered[1] in UNIT_WORDS):
        splits(2, " ".join(tokens[:2]))
    if len(tokens) >= 2 and lowered[0] in UNIT_WORDS:
        splits(1, tokens[0])
    # unit designator trailing the street: "350 5th Ave Ste 7500", "88 Market St Apt 3C"
    for index, word in enumerate(lowered):
        if index > 0 and word in UNIT_WORDS and index + 1 < len(tokens):
            rest = tokens[:index]
            unit = " ".join(tokens[index:index + 2])
            street = " ".join(rest[1:] + tokens[index + 2:])
            if rest and NUMBER_RE.match(rest[0]):
                reads.append({"unit": unit, "house_number": rest[0], "street": street})
            elif rest:
                reads.append({"unit": unit, "street": " ".join(rest + tokens[index + 2:])})
    splits(0, "")
    return [{k: v.strip() for k, v in read.items() if v and v.strip()} for read in reads]


def tail_readings(tail: list[str], postcode: str) -> list[dict[str, str]]:
    """Readings of everything after the premises: locality / city / region / postcode."""
    if not tail:
        return [{"postcode": postcode}]

    tokens = tail[-1].split()
    region = ""
    if len(tokens) > 1 and REGION_RE.match(tokens[-1]):
        region, tokens = tokens[-1], tokens[:-1]
    chain = tail[:-1] + ([" ".join(tokens)] if tokens else [])
    if not chain:
        return [{"region": region, "postcode": postcode}]

    reads = [{"locality": ", ".join(chain[:-1]), "city": chain[-1],
              "region": region, "postcode": postcode}]
    if not postcode:
        words = chain[-1].split()
        if len(words) > 1 and words[0].isdigit():
            # "10115 Berlin" -- postal code ahead of the settlement
            reads.append({"locality": ", ".join(chain[:-1]), "city": " ".join(words[1:]),
                          "region": region, "postcode": words[0]})
        if len(words) > 1 and words[-1].isdigit():
            # a trailing digit block nobody punctuated: postal code, or part of the name?
            reads.append({"locality": ", ".join(chain[:-1]), "city": " ".join(words[:-1]),
                          "region": region, "postcode": words[-1]})
    if len(chain) > 1:
        # the whole chain read as one settlement name ("Shibuya, Shibuya-ku")
        reads.append({"city": ", ".join(chain), "region": region, "postcode": postcode})
        if not region:
            reads.append({"locality": ", ".join(chain[:-2]), "city": chain[-2],
                          "region": chain[-1], "postcode": postcode})
    return reads


def candidate_parses(raw: str, limit: int = 6) -> list[dict[str, str]]:
    """3-6 plausible structured readings of one messy string. Pure Python."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip(" ,")
    if not text:
        return []
    body, postcode = strip_postcode(text)

    combos = []
    for si, (head, tail) in enumerate(segmentations(body)):
        for hi, header in enumerate(head_readings(head)):
            for ti, trailer in enumerate(tail_readings(tail, postcode)):
                combos.append((si + hi + ti, si, hi, ti, header, trailer))

    out: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for *_, header, trailer in sorted(combos, key=lambda combo: combo[:4]):
        parse = {f: str(header.get(f) or trailer.get(f) or "").strip() for f in FIELDS}
        key = tuple(parse.values())
        if key in seen or not any(parse.values()):
            continue
        seen.add(key)
        out.append(parse)
        if len(out) >= limit:
            break
    return out


def describe(parse: dict[str, str]) -> str:
    filled = " · ".join(f"{f}={parse[f]}" for f in FIELDS if parse.get(f))
    return filled or "every field left empty"


def build_questions(items: list[dict]) -> dict:
    """Per address: one Choice over ITS OWN candidates, plus two independent Nouls."""
    questions: dict[str, dict] = {}
    for index, item in enumerate(items):
        reference = f"`addresses[{index}].raw`"
        options = {key: describe(parse) for key, parse in item["candidates"].items()}
        options["none_correct"] = (
            "Every reading above misfiles something: a part of the string is assigned "
            "to the wrong field, or the string is not an address at all."
        )
        questions[f"parse_{index}"] = choice(
            {
                "task": "Which of these structured readings is the one the writer of this address meant?",
                "address": reference,
                "readings": f"`addresses[{index}].candidates`",
                "scope": "Judge only where each part of the string belongs. Do not judge whether the place exists.",
                "ambiguity": (
                    "A leading number may be a flat or apartment number or the house number. "
                    "A trailing block of digits may be a postal code or part of the street name."
                ),
            },
            options,
        )
        # Independent of the parse and of each other -- one Noul each, never levels.
        questions[f"incomplete_{index}"] = noul(
            {"task": "Is a component the postal service would need in order to deliver here absent from this string?",
             "address": reference},
            true="No postal code, no town or city, or no street named -- a carrier could not route it as written",
            false="Street, settlement and postal code are all present, whatever the order or casing",
        )
        questions[f"nonphysical_{index}"] = noul(
            {"task": "Is this a PO box, care-of line, or other non-physical delivery point rather than a building somebody occupies?",
             "address": reference},
            true="A PO box, private mail box, poste restante, rural route box, or a c/o line addressed via another person",
            false="A physical building, flat or unit sited on a named street",
        )
    return questions


def batch_items(items: list[dict], context_tokens: int, fill: float = 0.30) -> list[list[dict]]:
    """Size batches against the provider's real window, never a hardcoded number.

    ~4 chars per token, and the candidate list is echoed a second time in the
    Choice option descriptions, so the default fill is deliberately conservative.
    """
    budget = max(1, int(context_tokens * fill * 4))
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for item in items:
        cost = len(json.dumps(item, ensure_ascii=False))
        if current and size + cost > budget:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += cost
    if current:
        batches.append(current)
    return batches


def decide(pick: str, confidence: float, gate: float) -> str:
    """Three outcomes, all policy, all in Python so re-tuning costs no inference."""
    if pick == "none_correct":
        return "unparseable"
    if confidence < gate:
        return "curator"
    return "auto_accept"


def load_frame(uploaded) -> pd.DataFrame | None:
    # keep_default_na=False: "n/a" is a real thing a human typed into an address
    # field, not a missing value, and the app has to have an answer for it.
    if uploaded is not None:
        return pd.read_csv(io.BytesIO(uploaded.read()), keep_default_na=False, dtype=str)
    if SAMPLE.is_file():
        return pd.read_csv(SAMPLE, keep_default_na=False, dtype=str)
    return None


def main() -> None:
    st.set_page_config(page_title="Address Normalizer", page_icon="📮", layout="wide")
    st.title("📮 Address Normalizer")
    st.caption("Python enumerates the readings. Jev picks the one that was meant.")

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.subheader("Provider")
        st.code(f"{provider.name}\n{provider.model}\n{provider.context_tokens:,} token window", language=None)
        st.divider()
        st.subheader("Policy")
        gate = st.slider(
            "Auto-accept a parse above this confidence", 0.0, 1.0, 0.70, 0.05,
            help="Below it, the address goes to the curator queue instead of the clean table.",
        )
        max_candidates = st.slider(
            "Candidate readings per address", 3, 6, 6, 1,
            help="Pure Python enumeration. More readings cost tokens, not accuracy.",
        )
        flag_gate = st.slider(
            "Flag a Noul above P=", 0.0, 1.0, 0.60, 0.05,
            help="Applies to 'missing a component' and 'not a physical place'.",
        )
        fill = st.slider(
            "Share of the context window to fill", 0.10, 0.60, 0.30, 0.05,
            help=f"Batches are sized against {provider.context_tokens:,} tokens, not a constant.",
        )

    uploaded = st.file_uploader("Addresses CSV (needs a `raw_address` column)", type=["csv"])
    frame = load_frame(uploaded)
    if frame is None:
        st.warning("Upload a CSV, or put sample_addresses.csv next to this app.")
        return
    if "raw_address" not in frame.columns:
        st.error("CSV is missing the required column `raw_address`.")
        return
    if uploaded is None:
        st.caption(f"Using the bundled sample: {len(frame)} addresses in mixed UK, US and other formats.")

    st.dataframe(frame.head(8), width="stretch")

    items = [
        {
            "raw": str(row["raw_address"]),
            "candidates": {
                f"p{k}": parse
                for k, parse in enumerate(candidate_parses(str(row["raw_address"]), max_candidates))
            },
        }
        for _, row in frame.iterrows()
    ]
    empty = [i for i, item in enumerate(items) if not item["candidates"]]
    askable = [i for i, item in enumerate(items) if item["candidates"]]

    candidate_total = sum(len(item["candidates"]) for item in items)
    batches = batch_items([items[i] for i in askable], provider.context_tokens, fill)
    columns = st.columns(4)
    columns[0].metric("Addresses", len(items))
    columns[1].metric("Candidate readings enumerated", candidate_total)
    columns[2].metric("Blank / no reading possible", len(empty))
    columns[3].metric("Requests needed", len(batches))

    if not askable:
        st.info("Nothing to parse.")
        return
    if not st.button(f"Parse {len(askable)} addresses", type="primary"):
        return

    results: list[dict] = []
    total_cost = 0.0
    total_tokens = 0
    total_elapsed = 0.0
    total_questions = 0
    progress = st.progress(0.0, text="Parsing...")

    try:
        with JevClient(provider=provider) as client:
            for batch_index, group in enumerate(batches):
                questions = build_questions(group)
                answers = client.ask({"addresses": group}, questions)
                total_cost += answers.cost_usd
                total_tokens += answers.input_tokens
                total_elapsed += answers.elapsed_s
                total_questions += len(questions)
                for index, item in enumerate(group):
                    pick = answers.choice(f"parse_{index}")
                    confidence = answers.confidence(f"parse_{index}")
                    parse = item["candidates"].get(pick, {f: "" for f in FIELDS})
                    results.append(
                        {
                            "raw_address": item["raw"],
                            **{field: parse.get(field, "") for field in FIELDS},
                            "reading": pick,
                            "readings_offered": len(item["candidates"]),
                            "confidence": confidence,
                            "incomplete": answers.noul(f"incomplete_{index}"),
                            "non_physical": answers.noul(f"nonphysical_{index}"),
                            "status": decide(pick, confidence, gate),
                        }
                    )
                progress.progress((batch_index + 1) / len(batches), text="Parsing...")
    except JevError as exc:
        progress.empty()
        st.error(str(exc))
        return
    progress.empty()

    out = pd.DataFrame(results)

    # ── Everything below is pandas. The model produced labels, not statistics. ──
    counts = out["status"].value_counts()
    accepted = int(counts.get("auto_accept", 0))
    columns = st.columns(4)
    columns[0].metric("Auto-accepted", f"{accepted} ({accepted / len(out) * 100:.0f}%)")
    columns[1].metric("Curator queue", int(counts.get("curator", 0)))
    columns[2].metric("No reading fit", int(counts.get("unparseable", 0)))
    columns[3].metric("Cost", f"${total_cost:.6f}")

    flagged_incomplete = out[out["incomplete"] > flag_gate]
    flagged_physical = out[out["non_physical"] > flag_gate]
    columns = st.columns(2)
    columns[0].metric(f"Missing a component (P>{flag_gate:.2f})", len(flagged_incomplete))
    columns[1].metric(f"PO box / care-of (P>{flag_gate:.2f})", len(flagged_physical))

    st.subheader(f"Normalized ({accepted})")
    st.caption("One row per address, split into the fields the chosen reading assigned.")
    st.dataframe(
        out[out["status"] == "auto_accept"][["raw_address", *FIELDS, "confidence"]],
        width="stretch", hide_index=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader(f"⚠️ Curator queue ({int(counts.get('curator', 0))})")
        st.caption(
            "A Choice always returns a reading. A flat distribution over several "
            "readings is the signal that the string was genuinely ambiguous — that "
            "is the row a person should see, not a row to silently file."
        )
        st.dataframe(
            out[out["status"] == "curator"][["raw_address", "reading", "confidence", *FIELDS[:4]]]
            .sort_values("confidence"),
            width="stretch", hide_index=True,
        )
    with right:
        st.subheader(f"🚫 No reading fit ({int(counts.get('unparseable', 0))})")
        st.caption("`none_correct` won. Enumeration never produced the right structure, or there was no address in the string.")
        st.dataframe(
            out[out["status"] == "unparseable"][["raw_address", "confidence", "non_physical"]],
            width="stretch", hide_index=True,
        )

    st.subheader("Independent flags")
    st.caption(
        "Separate Nouls, not levels on one rubric: an address can be both "
        "incomplete and a PO box, or either alone. Collapsing them would destroy "
        "the ability to threshold them separately."
    )
    st.dataframe(
        out[(out["incomplete"] > flag_gate) | (out["non_physical"] > flag_gate)][
            ["raw_address", "incomplete", "non_physical", "status"]
        ].sort_values("incomplete", ascending=False),
        width="stretch", hide_index=True,
    )

    st.download_button(
        "Download normalized CSV",
        out.to_csv(index=False).encode("utf-8"),
        file_name="normalized_addresses.csv",
        mime="text/csv",
    )

    st.divider()
    st.caption(
        f"{len(batches)} request(s) · {total_questions} questions · "
        f"{total_tokens:,} input tokens · {total_elapsed:.2f}s · ${total_cost:.6f} total "
        f"(${total_cost / len(out):.8f} per address). "
        f"Batches were sized against this provider's {provider.context_tokens:,}-token window."
    )


if __name__ == "__main__":
    main()
