"""Invoice Extractor -- pull fields off messy invoices with a verbatim guarantee.

The pipeline is deliberately three-stage, and the model owns only the middle one:

    1. Python  FINDS   every date-shaped, amount-shaped, identifier-shaped and
                       name-shaped span in the raw text  (find_candidates)
    2. Jev     SELECTS which of those spans is each field -- one Choice per
                       field, options keyed by candidate index      (build_questions)
    3. Python  NORMALISES the winning span into a float or an ISO date
                       (normalize_amount / normalize_date) and reconciles totals

Stage 2 is a selection over a closed set that Python built from the document.
There is no path by which a value that is not literally present in the invoice
can come out the other end. That is the whole point: an LLM extractor asked for
"the grand total" can emit a plausible number that appears nowhere on the page,
and you will not notice. This cannot.

Everything numeric -- parsing "1.234,56", reconciling subtotal + tax against the
total, deciding which date is later -- happens in Python. jev-1.13 cannot do
arithmetic or compare dates, so it is never asked to.

    uv run streamlit run app.py
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from jev_provider import JevClient, JevError, choice, load_provider

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_invoices.json"

# Rough chars-per-token, only used to warn before the provider rejects the call.
CHARS_PER_TOKEN = 4
# A Choice carries every option's text in criteria, so candidates are bounded.
MAX_PER_KIND = 40

NO_MATCH = "No candidate span on this invoice is this field."

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Ordered: earlier patterns claim their characters, later ones skip overlaps.
# This is what stops "Nov 2024" being re-extracted out of "11 Nov 2024".
PATTERNS: list[tuple[str, str]] = [
    ("date", r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"),
    ("date", r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"),
    ("date", r"\b\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}\b"),
    ("date", r"\b[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}\b"),
    ("date", r"\b[A-Za-z]{3,9}\s+\d{4}\b"),  # month + year only; will not normalise
    ("amount", r"-?\d{1,3}(?:[.,]\d{3})*[.,]\d{2}(?![\d])"),
    ("identifier", r"\b[A-Z0-9]{2,}(?:[-/][A-Z0-9]+)+\b"),
    ("identifier", r"\b[A-Za-z]{1,4}\d{3,}\b"),
    ("identifier", r"\b\d{3,}\b"),
]

# field -> (candidate kind, question, boundary note that belongs in criteria)
FIELDS: dict[str, tuple[str, str, str]] = {
    "vendor_name": (
        "name",
        "Which candidate line is the name of the business that issued this invoice?",
        "The business being billed, and generic headings like 'TAX INVOICE', are not the issuer.",
    ),
    "invoice_number": (
        "identifier",
        "Which candidate span is the invoice number the issuer assigned to this document?",
        "A customer purchase-order reference, a VAT registration, a customer account "
        "reference and a bank account are each not the invoice number.",
    ),
    "invoice_date": (
        "date",
        "Which candidate span is the date on which this invoice was issued?",
        "A delivery date, a payment due date, and a date on which an underlying "
        "contract was signed are each not the issue date.",
    ),
    "due_date": (
        "date",
        "Which candidate span is the date by which payment on this invoice must be made?",
        "Pick 'none' when the invoice states terms in words only, such as 'due on "
        "receipt' or 'net 30', without giving a dated deadline.",
    ),
    "subtotal": (
        "amount",
        "Which candidate span is the net amount stated before tax is applied?",
        "Pick 'none' when the invoice charges no tax and therefore states no "
        "separate pre-tax figure. A single line item is not the net figure.",
    ),
    "tax_amount": (
        "amount",
        "Which candidate span is the tax charged on this invoice, shown as its own figure?",
        "Pick 'none' when the issuer states that no tax is charged. A tax rate "
        "such as 7.25, and a tax registration reference, are not tax amounts.",
    ),
    "grand_total": (
        "amount",
        "Which candidate span is the final amount payable on this invoice?",
        "A balance brought forward from an earlier invoice is not payable here. "
        "A single line item is not the final amount.",
    ),
}


# ---------------------------------------------------------------------------
# Stage 1 -- Python finds the candidate spans
# ---------------------------------------------------------------------------


def find_candidates(text: str) -> list[dict]:
    """Every field-shaped span in the document, with its containing line.

    The span is sliced straight out of `text`; the description shown to the
    model is span + line. Nothing here interprets a value -- that is stage 3.
    """
    claimed: list[tuple[int, int]] = []
    found: list[tuple[int, str, str]] = []

    for kind, pattern in PATTERNS:
        for match in re.finditer(pattern, text):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            claimed.append((start, end))
            found.append((start, kind, match.group().strip()))

    # Lines with no digits are the only sensible vendor-name candidates.
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if 3 <= len(stripped) <= 60 and not any(c.isdigit() for c in stripped) \
                and any(c.isalpha() for c in stripped):
            found.append((offset, "name", stripped))
        offset += len(line)

    found.sort()
    per_kind: dict[str, int] = {}
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for start, kind, span in found:
        line = _line_at(text, start)
        if (kind, span + line) in seen:
            continue
        seen.add((kind, span + line))
        per_kind[kind] = per_kind.get(kind, 0) + 1
        if per_kind[kind] > MAX_PER_KIND:
            continue
        candidates.append({"index": len(candidates), "kind": kind, "text": span, "line": line})

    return candidates


def _line_at(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start:end if end != -1 else len(text)].strip()


# ---------------------------------------------------------------------------
# Stage 3 -- Python normalises. Never the model.
# ---------------------------------------------------------------------------


def normalize_amount(raw: str) -> float | None:
    """Parse a money-shaped span. Handles 1,197.84 and 1.502,97 alike.

    Rule: when both separators appear, the rightmost is the decimal point.
    When one appears once followed by exactly three digits it is a thousands
    separator, otherwise it is the decimal point.
    """
    cleaned = re.sub(r"[^\d,.\-]", "", raw).strip()
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    if not cleaned or not any(c.isdigit() for c in cleaned):
        return None

    commas, dots = cleaned.count(","), cleaned.count(".")
    if commas and dots:
        cut = max(cleaned.rfind(","), cleaned.rfind("."))
        cleaned = re.sub(r"[.,]", "", cleaned[:cut]) + "." + cleaned[cut + 1:]
    elif commas or dots:
        separator = "," if commas else "."
        tail = cleaned.rsplit(separator, 1)[1]
        if (commas or dots) > 1 or len(tail) == 3:
            cleaned = cleaned.replace(separator, "")
        else:
            cleaned = cleaned.replace(separator, ".")

    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def normalize_date(raw: str, day_first: bool = True) -> str | None:
    """Parse a date-shaped span to ISO. Returns None when it is not a full date.

    `day_first` is the calibration knob: 03/02/2025 is February 3rd in the UK
    and March 2nd in the US, and nothing in the document settles it. A span
    like "November 2024" has no day and deliberately returns None rather than
    inventing the 1st.
    """
    text = raw.strip().rstrip(".,")

    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return _iso(int(match[1]), int(match[2]), int(match[3]))

    match = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", text)
    if match:
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        day, month = (first, second) if day_first else (second, first)
        if month > 12 >= day:  # unambiguous the other way round; trust the data
            day, month = month, day
        return _iso(year + 2000 if year < 100 else year, month, day)

    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", text)
    if match and match[2][:3].lower() in MONTHS:
        return _iso(int(match[3]), MONTHS[match[2][:3].lower()], int(match[1]))

    match = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if match and match[1][:3].lower() in MONTHS:
        return _iso(int(match[3]), MONTHS[match[1][:3].lower()], int(match[2]))

    return None


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def reconcile(subtotal: float | None, tax: float | None, total: float | None,
              tolerance: float = 0.02) -> str | None:
    """Python does the arithmetic the model is structurally barred from doing."""
    if subtotal is None or tax is None or total is None:
        return None
    if abs(subtotal + tax - total) <= tolerance:
        return None
    return f"{subtotal:,.2f} + {tax:,.2f} does not reconcile against {total:,.2f}"


def order_check(invoice_date: str | None, due_date: str | None) -> str | None:
    """Same again for dates -- an ISO string comparison, done in Python."""
    if invoice_date and due_date and due_date < invoice_date:
        return f"due date {due_date} precedes issue date {invoice_date}"
    return None


def decide(probability: float, threshold: float) -> str:
    return "extracted" if probability >= threshold else "review"


# ---------------------------------------------------------------------------
# Stage 2 -- one Choice per field, every field of every invoice in one request
# ---------------------------------------------------------------------------


def candidate_options(candidates: list[dict], kind: str) -> dict[str, str]:
    """Options keyed by candidate index; description is the span in its line."""
    return {
        str(c["index"]): (
            c["text"] if c["text"] == c["line"]
            else f'"{c["text"]}" — appearing on the line: {c["line"]}'
        )
        for c in candidates
        if c["kind"] == kind
    }


def build_questions(documents: list[dict]) -> dict:
    """One request covering every field of every invoice.

    A field with no candidate span of the right shape is not asked at all --
    the answer is already known to be 'nothing here', at zero cost.
    """
    questions: dict[str, dict] = {}
    for index, document in enumerate(documents):
        for field, (kind, task, note) in FIELDS.items():
            options = candidate_options(document["candidates"], kind)
            if not options:
                continue
            options["none"] = NO_MATCH
            questions[f"{index}:{field}"] = choice(
                {
                    "task": task,
                    "note": note,
                    "invoice_text": f"`invoices[{index}].text`",
                },
                options,
            )
    return questions


def prepare(invoices: list[dict]) -> list[dict]:
    return [dict(inv, candidates=find_candidates(inv["text"])) for inv in invoices]


def load_sample() -> list[dict]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Invoice Extractor", page_icon="🧾", layout="wide")
    st.title("🧾 Invoice Extractor")
    st.caption(
        "Regex finds the candidate spans, Jev picks which one is each field, "
        "Python normalises. **Every value is copied verbatim out of the document.**"
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
        threshold = st.slider(
            "Send a field to manual review below this winning probability",
            0.0, 1.0, 0.70, 0.05,
        )
        day_first = st.toggle(
            "Read 03/02/2025 as day-first", value=True,
            help="Nothing in a numeric date settles this. Set it for your suppliers.",
        )
        tolerance = st.number_input("Reconciliation tolerance", 0.0, 5.0, 0.02, 0.01)

    invoices = load_sample()
    uploaded = st.file_uploader("Upload invoices (.json list, or a single .txt)", type=["json", "txt"])
    if uploaded is not None:
        payload = uploaded.read().decode("utf-8", errors="replace")
        if uploaded.name.endswith(".json"):
            invoices = json.loads(payload)
        else:
            invoices = [{"id": uploaded.name, "name": uploaded.name, "text": payload}]

    pasted = st.text_area("…or paste one invoice here", value="", height=160)
    if pasted.strip():
        invoices = [{"id": "pasted", "name": "pasted invoice", "text": pasted}]

    documents = prepare(invoices)
    with st.expander(f"{len(documents)} invoice(s) loaded — candidate spans found by regex"):
        for document in documents:
            st.markdown(f"**{document.get('name', document.get('id', ''))}**")
            st.caption(", ".join(f"`{c['text']}`" for c in document["candidates"]) or "none")

    if not st.button("Extract", type="primary"):
        st.info("The bundled sample is loaded. Hit Extract.")
        return

    questions = build_questions(documents)
    if not questions:
        st.warning("No field-shaped spans found in that input.")
        return

    payload_chars = sum(len(d["text"]) for d in documents) + sum(
        len(option) for q in questions.values() for option in q["criteria"].values()
    )
    if payload_chars // CHARS_PER_TOKEN > provider.context_tokens:
        st.error(
            f"~{payload_chars // CHARS_PER_TOKEN:,} tokens exceeds {provider.name}'s "
            f"{provider.context_tokens:,}. Process fewer invoices per run."
        )
        return

    state = {"invoices": [{"text": d["text"]} for d in documents]}
    try:
        with JevClient(provider=provider) as client:
            answers = client.ask(state, questions)
    except JevError as exc:
        st.error(str(exc))
        return

    rows: list[dict] = []
    for index, document in enumerate(documents):
        by_index = {c["index"]: c for c in document["candidates"]}
        normalised: dict[str, float | str | None] = {}

        for field, (kind, _task, _note) in FIELDS.items():
            qid = f"{index}:{field}"
            if qid not in questions:
                rows.append({
                    "invoice": document.get("id", index), "field": field,
                    "value": None, "source span": "", "P": 0.0,
                    "status": "no candidate", "kind": kind,
                })
                continue

            winner = answers.choice(qid)
            probability = answers.probabilities(qid).get(winner, 0.0)
            if winner == "none":
                rows.append({
                    "invoice": document.get("id", index), "field": field,
                    "value": None, "source span": "", "P": probability,
                    "status": "absent" if probability >= threshold else "review",
                    "kind": kind,
                })
                continue

            span = by_index[int(winner)]["text"]
            if kind == "amount":
                value: float | str | None = normalize_amount(span)
            elif kind == "date":
                value = normalize_date(span, day_first=day_first)
            else:
                value = span
            normalised[field] = value

            rows.append({
                "invoice": document.get("id", index), "field": field,
                "value": value, "source span": span, "P": probability,
                "status": decide(probability, threshold) if value is not None
                else "unparseable span",
                "kind": kind,
            })

        for problem in (
            reconcile(
                _as_float(normalised.get("subtotal")), _as_float(normalised.get("tax_amount")),
                _as_float(normalised.get("grand_total")), tolerance,
            ),
            order_check(_as_str(normalised.get("invoice_date")), _as_str(normalised.get("due_date"))),
        ):
            if problem:
                st.warning(f"**{document.get('id', index)}** — {problem}")

    frame = pd.DataFrame(rows)
    # Mixed float/str/None in one column upsets Arrow; render it as text.
    frame["value"] = frame["value"].map(lambda v: "" if v is None else str(v))
    flagged = frame[frame["status"].isin({"review", "unparseable span"})]

    st.subheader(f"{len(frame)} fields · {len(flagged)} need a human")
    st.dataframe(
        frame[["invoice", "field", "value", "source span", "P", "status"]],
        width="stretch", hide_index=True,
    )
    if not flagged.empty:
        st.caption(
            "Below the threshold, or a span the normaliser could not parse. "
            "Everything else was copied straight out of the document."
        )

    st.divider()
    columns = st.columns(4)
    columns[0].metric("Questions", len(questions))
    columns[1].metric("Requests", 1)
    columns[2].metric("Latency", f"{answers.elapsed_s:.2f} s")
    columns[3].metric("Cost", f"${answers.cost_usd:.6f}")
    st.caption(
        f"{answers.input_tokens:,} input tokens across {len(documents)} invoice(s). "
        f"Asking each field separately would have been {len(questions)} requests."
    )


def _as_float(value: object) -> float | None:
    return value if isinstance(value, float) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


if __name__ == "__main__":
    main()
