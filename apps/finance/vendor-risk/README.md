# 🛡️ Vendor Risk

Screen a supplier portfolio against seven risk criteria, judged from the
suppliers' own documents. Seven probabilities per supplier, seven independent
thresholds, no composite score anywhere.

## The problem

Third-party risk management is a spreadsheet with a RAG column. A supplier sends
back a 90-question security questionnaire, someone in procurement skims it,
someone else types "Amber", and the supplier is onboarded. Nobody re-reads it
until there is an incident.

The information is *in* the documents. "Working towards ISO 27001, expect
certification next year" is a no. "A disaster recovery replica in us-east-1 that
holds full customer identity records" is data leaving the region, stated plainly,
buried in paragraph four of an otherwise reassuring answer. "Ownership structure
runs through a holding company in the British Virgin Islands" is a sentence in a
company profile nobody scored.

And the tooling that exists makes it worse by producing a **single risk score**,
which is the one output shape that guarantees you cannot act on it. Orrell has a
going-concern warning and touches no data. Tessellate is immaculate and
irreplaceable. Both land on "medium". Now what?

## Why this needs Jev

**Independent properties must stay independent — this app exists to make that
concrete.** Seven criteria, seven Nouls, in one request:

```python
questions[f"{criterion['id']}_{vendor_index}"] = noul(
    {"task": "Judge this one risk criterion against the supplier documents below, "
             "on what the documents actually say.",
     "criterion": criterion["label"],
     "supplier": reference,
     "note": "Silence is evidence. A questionnaire that does not mention a control "
             "is not a questionnaire that confirms one."},
    true=criterion["true"],
    false=criterion["false"],
)
```

They cannot be a Choice, because a supplier routinely trips four at once. They
cannot be a Score, because there is no ordering in which "financially distressed"
sits above or below "data leaves the region" — they are different risks owned by
different people.

**The thresholds differ, and that is the payoff.** Because each risk has its own
probability, each gets its own gate:

| criterion | gate | why |
|---|---|---|
| regulatory / sanctions / opaque ownership | **0.25** | missing one is a regulatory event; false positives cost an hour |
| data leaves the region | 0.45 | a finding you must be able to evidence |
| no security assurance | 0.55 | expensive to chase, rarely ambiguous |

`P = 0.30` trips the sanctions gate and does not trip the certification gate.
That asymmetry is impossible to express in a composite score, and it is the whole
reason the criteria are separate primitives. The app asserts it in
`test_app.py`, including the fact that the *mean* of those same seven
probabilities sits below every gate — an averaged model would have seen nothing.

**Policy stays in Python.** One criterion is marked `hard_stop`, and it beats the
count regardless:

```python
if any(c["id"] in flags for c in CRITERIA if c["hard_stop"]):
    return "blocked"
```

## How it works

```
documents  ->  7 Nouls per supplier, one request
           ->  flagged(): each probability against ITS OWN gate
           ->  tier(): hard stop > count threshold > any flag > standard
           ->  pandas: spend behind each criterion, the heat map, the exposure chart
```

Every number on screen — spend at risk, criteria tripped, exposure per criterion —
is pandas over the flags. The model never sees a total and is never asked to
count anything.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 8 suppliers, each with a company profile, a security questionnaire, a
contract summary and a public-record note. They are written to be genuinely
mixed — a spotless-but-irreplaceable print supplier, a solvent vendor with a
US disaster-recovery replica, a cleaning firm with a going-concern warning and no
data access at all, and an advisory boutique whose only real problem is who owns
it.

## Design notes

- **"Silence is evidence" is in the instruction on purpose.** `jev-1.13` reads
  literally, and the most common real-world signal is an *absent* answer. Without
  that line the model treats an unmentioned control as unknown rather than
  missing, and every gap scores 0.5.
- **The criteria table is data, not code.** Add a criterion by adding a dict —
  label, `true`, `false`, default gate, `hard_stop`. The question builder and the
  UI both derive from it.
- **The heat map is the deliverable, not the tier.** The tier is a routing
  decision; the row of seven probabilities is what a risk committee actually
  argues about.
- **No weights.** There is no `0.3 * financial + 0.2 * security`. Weights are how
  a composite score smuggles a policy decision into an arithmetic that nobody
  re-derives.

## Limits

- **This screens documents, not suppliers.** Everything here is a judgment about
  what the paperwork says. A supplier that lies fluently scores well. Screening is
  a prioritisation step ahead of diligence, never a substitute for it.
- **No external data.** No sanctions list, no credit file, no Companies House
  lookup, no news feed. `public_record` is a field you populate; the app does not
  go and find it. Wiring a real sanctions screen in is the obvious next step and
  it is a lookup, not a judgment.
- **`jev-1.13` cannot compare dates**, so "certification expired last March" is
  invisible unless the document says it expired. Validity windows are a pandas
  check on a structured field, and are not built.
- **Criticality is not modelled.** `annual_spend` stands in for how much a
  supplier matters, which is crude — a £41k document-destruction contract can
  carry more risk than a £1.1m software licence. Bring your own criticality
  rating.
- **Seven criteria is a starting set**, tuned to IT and data suppliers. Modern
  slavery, ESG, insurance adequacy, business continuity testing and concentration
  across your own portfolio are all missing.

## Privacy

Supplier names, questionnaire responses, contract terms and commercial
information are sent to whichever provider `providers.toml` selects. Much of this
is material your suppliers gave you under NDA — confirm you are permitted to
process it through a third party before running this on real vendor files, and
read the provider's retention policy.
