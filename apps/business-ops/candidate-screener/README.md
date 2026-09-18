# 🧑‍💼 Candidate Screener

Criterion-by-criterion screening **assistance**. It orders a reading queue and
shows the evidence. It does not reject anyone.

## Read this first

**This tool assists human screening. It must not be used to auto-reject
applications, and it is built so that it cannot.**

- There is **no reject outcome in the code.** `triage()` returns one of
  `advance`, `review`, `review_thin` — all three mean a person reads the
  application. The offline check asserts this holds at every threshold setting
  and for every possible input, including an empty criteria list.
- **Criteria must be job-related.** Anything referencing age, graduation year,
  nationality, citizenship or visa status, gender, family or marital status,
  pregnancy, disability, health, religion, race or ethnicity, sexual
  orientation, appearance, accent or "native speaker", "culture fit", salary
  history, or address is blocked in Python **before any request is sent**. The
  check is a blunt keyword screen, not a lawyer — it stops obvious mistakes and
  nothing more.
- **The sample applications carry no names**, and no field in this app is meant
  to carry one. Strip identifying details before you use it on real
  applications; it makes the tool better *and* it is what fair screening
  practice requires anyway.
- Automated employment decisions are regulated in a growing number of
  jurisdictions (EU AI Act Annex III, NYC Local Law 144, Illinois HB 3773 among
  others). Meeting those obligations — bias auditing, candidate notice, human
  oversight, record retention — is your responsibility, not this repo's.

## The problem

A single senior engineering role gets 400 applications. A recruiter has an
afternoon. What happens in practice is a keyword filter on the ATS, which
rejects the ledger engineer who wrote "Kotlin" where the filter wanted "Java",
and advances the applicant who pasted the job description into their profile.

The demand is real and the usual automation is worse than the problem: an
end-to-end "candidate score" that nobody can audit, that cannot be contested,
and that encodes whatever correlations were in its training data.

## Why this needs Jev

**Criterion-by-criterion, with the evidence attached.** Each requirement is its
own Noul over the application text, so what comes back is not a verdict but a
row of probabilities, one per stated requirement. A screener sees:

```
APP-008  ✅ 0.94 backend services in production
         ✅ 0.88 payments, billing, ledgers, or financial transaction systems
         ✅ 0.91 statically typed backend language
         ✅ 0.96 owning a service on call, including incident response
```

and can disagree with any single line. That is what makes the output
contestable, which is the property a hiring process actually needs. A composite
score has no such line to disagree with — which is precisely why this app
refuses to compute one.

**Nothing is composed, deliberately.** No weights, no total, no ranking by
score. The only aggregation is which pile an application goes in, and every
pile ends at a human.

One request: 12 applications × 8 criteria = 96 judgments.

## How it works

```python
questions[f"{kind}_{index}_{position}"] = noul(
    {"task": "Does the application below provide evidence for this job requirement?",
     "requirement": criterion,
     "role": role["title"],
     "application": {"resume": f"`applications[{index}].resume`",
                     "cover_note": f"`applications[{index}].cover_note`"}},
    true="The application states experience that clearly satisfies this requirement",
    false="The application does not state experience satisfying this requirement")
```

and the triage, which is the whole safety argument:

```python
def triage(required, met_at, review_at):
    if not required:                       return "review", ...
    unmet = [i for i, p in enumerate(required) if p <= review_at]
    if all(p >= met_at for p in required): return "advance", ...
    if unmet:                              return "review_thin", f"... criterion {listed} — verify by hand"
    return "review", ...
```

`review_thin` is *not* a reject pile. It is the pile where the screener is told
which requirement to check by hand, because that is where a keyword filter would
have silently dropped someone.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Twelve anonymised synthetic applications ship with it, including the ones that
break keyword screening: a crypto-exchange ledger engineer with no PCI
experience, an SRE who ran the payments platform but wrote none of it, an
engineering manager going back to IC, and one CV that is pure filler.

## Design notes

**Why a separate `evidence` Noul.** Some applications are thin because the
person is inexperienced and some are thin because they wrote three lines. Those
need different human attention, and the app says which it is seeing rather than
letting "no evidence found" stand in for both.

**Why `required` and `preferred` are the same question type.** Preferred
criteria are shown, never scored into anything. The moment preferred criteria
contribute to an outcome they become required criteria with extra steps.

**Why the thresholds are called "reading-order thresholds".** They are. Moving
them changes what a recruiter reads first and nothing else. Naming them
"accept" and "reject" would invite exactly the use this app is built to
prevent.

**Why the protected-term check runs in Python before the request.** It is a
trust boundary. A criterion that should never be evaluated should never be
sent, and a check that depends on the model's cooperation is not a check.

## Limits

- **It reads what is written.** A strong candidate who writes a bad CV screens
  as a bad CV. This is the same failure a human screener has, at greater speed,
  which is exactly why nothing here is final.
- **The protected-term screen is a keyword list.** It will not catch a proxy
  ("recent graduate energy", a criterion about a specific school, a postcode
  requirement phrased as commute time). Have a human who understands
  employment law review your criteria.
- **`P(yes)` is not a measure of a person.** It is the probability that a
  statement holds of a document. Treating 0.42 as "42% qualified" is a
  category error.
- **Nothing is calibrated for fairness here.** Differential performance across
  demographic groups is possible and this app does not measure it. If you
  deploy this, run a bias audit on your own historical data first.
- **No arithmetic, no dates, no counting.** The model is never asked how many
  years of experience someone has or whether a date range is long enough —
  `jev-1.13` cannot do date maths, and "years of experience" is a criterion
  worth avoiding anyway.
- **`jev-1.13` reads literally.** "Kotlin" satisfies "a statically typed
  backend language such as Go, Java, Kotlin, Rust, or C#" because the criterion
  names it. Write the list out; do not expect inference.
