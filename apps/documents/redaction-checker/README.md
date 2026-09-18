# 🖍️ Redaction Checker

Find PII and confidential detail before a document goes out — with a separate
dial for every hazard class.

> ¶6 · severity 3.00 / 3 · Named individual, Contact details
> ¶11 · severity 2.90 / 3 · Legal privilege

## The problem

A post-incident review, a board memo, a diligence pack. It was written for
internal readers, and now it is going to an auditor, a regulator, a customer, or
a journalist. Someone has to read all twenty pages and decide, paragraph by
paragraph, what has to come out.

That reader is doing eight different jobs at once. Is anyone named? Is there a
personal phone number? Did we put a salary in here? Is there a customer we are
contractually barred from naming? Did someone paste a hostname? Is this
paragraph *privileged*?

Those jobs have completely different stakes. Missing a named engineer is
embarrassing. Missing a privileged sentence can waive privilege over the whole
document. Doing all eight in one pass, at page nineteen, on a deadline, is how
things get missed.

Regex catches phone numbers and email addresses. It does not catch *"as counsel
advised in the 6 March memorandum"*, and it does not know that Northwind Grocers
is under NDA while AWS is not.

## Why this needs Jev

**Independent properties get independent Nouls, and that is the entire design.**
A paragraph can carry a name **and** a salary **and** an unreleased codename
simultaneously. They are not options in a Choice — a Choice returns exactly one.
They are not levels on a rubric — a rubric imposes an ordering that does not
exist between "has a home address" and "has a hostname".

So each hazard class is its own Noul, which means each gets its own threshold:

```python
thresholds = {
    key: st.slider(label, 0.0, 1.0, defaults[key], 0.05, key=f"th_{key}")
    for key, label, _q, _t, _f in HAZARDS
}
```

That is what a single combined score cannot give you. **Legal privilege at 0.35
is worth a human look. A customer name at 0.35 is noise.** One number cannot
express both. A rubric that averaged them would bury the privilege signal under
the noise from seven other classes — and the class you most need to catch is the
one with the fewest examples, so it is exactly the one an average washes out.

The defaults reflect that asymmetry: privilege and health sit at 0.30, personal
identity at 0.60. Those are policy decisions, they live in Python, and moving
one re-runs no inference.

Because hazard classes are independent, the per-class counts are a Python
`sum()` over fired classes — the model is never asked to count anything.

## How it works

Per paragraph: eight Nouls and one Score, all in **one** `ask()`. Every question
points at the paragraph by path, so the document is ingested once rather than
re-sent nine times per paragraph:

```python
for key, _label, question, true_criteria, false_criteria in HAZARDS:
    questions[f"{key}_{index}"] = noul(
        {"task": question, "paragraph": f"`paragraphs[{index}].text`"},
        true=true_criteria,
        false=false_criteria,
    )
```

The `false` criteria carry the boundary cases, because the model reads
literally and the exclusions are where the false positives come from:

| Class | `false` says, in effect |
|---|---|
| Named individual | teams, roles and job titles with nobody named are not a hit |
| Individual pay | company revenue, budgets and contract values are not an individual's finances |
| Health | *system* health and *service* health are not medical information |
| Customer confidential | a publicly known customer or an openly used vendor is not a hit |
| Legal privilege | plain facts, contract terms and regulatory obligations are not advice |

The Score is a separate question because "which hazards are present" and "how
much would releasing this cost" are genuinely different. A paragraph can name a
person harmlessly; a paragraph can be damaging with nobody named in it. Four
levels, each a standalone situation:

```python
SEVERITY_LEVELS = [
    "Nothing in this paragraph would embarrass anyone if the entire document were "
    "posted on a public website tomorrow",
    ...
    "Releasing this paragraph would breach a confidentiality obligation, waive legal "
    "privilege, expose an individual's private life, or hand an attacker something "
    "they could use",
]
```

A paragraph lands on the worklist if **any** class cleared its own dial **or**
severity cleared the severity dial. Either signal alone is enough — a hazard the
Score underrates still gets a human, and a paragraph that simply reads badly
still gets one even when no class fired.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

A synthetic 20-paragraph post-incident review is bundled and loaded by default,
seeded with one instance of each hazard class plus a good deal of harmless
engineering prose. The interesting paragraphs to watch:

| ¶ | What is buried in it |
|---|---|
| 5 | A named engineer, in a role — identity fires, severity should stay low |
| 6 | Personal email, mobile, and a home address |
| 10 | A customer named as explicitly unannounced |
| 11 | "As counsel advised…" — privilege |
| 12 | Internal hostname, port, and a service account name |
| 13 | A cardiac event attributed to a named employee |
| 14 | A specific individual's base salary and counter-offer |
| 15 | An unannounced product codename |

¶7 is the control: it is the most important finding in the document and should
flag nothing.

## Design notes

**Why severity is a Score and not a ninth Noul.** "How bad is releasing this" is
a degree, and a Noul is not an intensity dial — 0.5 on a Noul means yes and no
are equally likely, not "medium harm". Degree needs a rubric.

**Why the thresholds are not symmetric.** In a release review the cost of a
false positive is thirty seconds of a reviewer's attention. The cost of a false
negative can be a waived privilege or a contract breach. Every dial should be
set from that ratio, per class, and the ratio is different for every class.
Start low and raise them only when you have measured the noise on your own
documents.

**Why paragraphs and not sentences.** Privilege and confidentiality attach to
context, not to tokens. *"We should not characterise this as a control failure"*
is unremarkable until you read the clause before it. Sentence-level scanning
loses that and produces worse judgments on exactly the classes that matter most.

**Why there is no auto-redact button.** Deciding what to cut is a judgment with
legal consequences, and it belongs to a person who can be accountable for it.
This app ranks a queue; it does not empty it.

## Limits

- **This is a candidate list, not a redaction guarantee.** Nothing here proves
  that the paragraphs it did *not* flag are safe to release. It must not be your
  only control, and it does not replace a reviewer signing their name to the
  document.
- **Recall is unmeasured on your documents.** The bundled sample is synthetic and
  deliberately obvious. Real documents hide things more subtly. Before trusting
  it, run it over a document you have already redacted by hand and see what it
  misses — that number is the only one that matters, and it is yours to measure.
- **`jev-1.13` cannot count, cannot do arithmetic, and cannot compare dates.**
  The model is never asked how many hazards a paragraph carries or how many
  paragraphs fired; those are Python `sum()`s. Do not add a question like "is
  this more sensitive than the previous paragraph" — comparison across items is
  not what a per-item judgment does.
- **It reads literally.** "Unannounced customer" fires because ¶10 *says* the
  relationship is unannounced. A customer named with no such signal will not
  fire, because nothing in the text marks it. If your confidentiality obligations
  are not visible in the document, this cannot see them — maintain a name list
  and match it in Python.
- **It degrades on indirection and on large noisy state.** Each question points
  at one paragraph for that reason. A 200-paragraph document in one run will do
  worse than four runs of 50, independent of the context limit, which the app
  checks separately against `provider.context_tokens`.
- **Paragraph splitting is blank-line based.** A document that is one wall of
  text becomes one paragraph and one set of judgments over the whole thing.
- **No image, table, or metadata scanning.** Plain text only. The address in the
  screenshot, the author name in the PDF metadata, and the hidden column in the
  embedded spreadsheet are all invisible here, and all three are common ways
  documents leak.
