# 🛡️ Policy Gap Finder

Which controls in a standard your policy document leaves open, vague, or
nobody's job.

## The problem

Readiness assessment for ISO 27001, SOC 2 or a 300-row customer security
questionnaire is the same afternoon every time: the control catalogue in one
window, the policy PDF in the other, Ctrl-F, and a spreadsheet column that says
"covered — Y/N".

That column is where the exercise goes wrong, because "covered" is three
different things wearing one hat, and an auditor fails you for any of them:

- **No rule.** The policy simply does not speak to the control. Everyone catches
  this one.
- **A rule with nothing behind it.** *"Northwind is committed to working only
  with suppliers that take security seriously."* That is a sentence, not a
  control. There is no mechanism, no cadence, no artefact to sample.
- **A rule nobody owns.** *"Access is withdrawn when someone leaves."* Withdrawn
  by whom, on what trigger, evidenced how? The auditor asks the question, three
  people look at each other, and it becomes a finding.

A Y/N column collapses all three, so the remediation plan that comes out of it
is wrong about *who* has to fix things — an absent control needs a policy
author, an unowned one needs a conversation with a department head.

## Why this needs Jev

The three properties are **independent**, and independent properties get
separate Nouls with separate thresholds. A policy can be specific and unowned
(a detailed backup procedure with no named owner), or owned and aspirational
(a named exec who "intends to exercise the plan periodically"). Collapsing them
into one "maturity score" destroys precisely the thresholding the compliance
lead needs — they want to tighten *ownership* without re-litigating *coverage*.

```python
questions[f"covered_{index}"]  = noul({"task": "Does the policy document state a rule "
                                               "that would satisfy this control?", **reference}, ...)
questions[f"specific_{index}"] = noul({"task": "Where the policy addresses this control, does it "
                                               "name a concrete mechanism, review frequency, "
                                               "tool or artefact?", **reference}, ...)
questions[f"owned_{index}"]    = noul({"task": "Does the policy make a named role, team or job "
                                               "title accountable for this control?", **reference}, ...)
questions[f"gap_{index}"]      = choice({"task": "Select the option that best describes the "
                                                 "policy's treatment of this control.", **reference},
                                        GAP_TYPES)   # absent / aspirational / partial / unowned / none
```

Every question points at the same ingested state by path
(`` `controls[3].requirement` ``, `` `policy` ``), so 15 controls is 60
judgments in **one** request over a policy read once — not 15 requests each
re-reading the whole document.

The Choice carries the `none` escape hatch. A Choice *must* return something; a
gap-shape question without "there is no gap" would label a perfectly good
control as `partial` at entirely plausible confidence. The app cross-checks the
two: when the thresholded Nouls say *met* and the Choice does not say *none*
(or vice versa), the control is flagged as disputed and a human reads it. That
disagreement is free — it is one extra question in a request that was happening
anyway.

## How it works

Ordered, not averaged:

```python
def assess(covered, specific, owned, accept, reject):
    if covered  <= reject: return "open",    "the policy states no rule for this control"
    if specific <= reject: return "vague",   "stated as intent, no mechanism named"
    if owned    <= reject: return "unowned", "rule exists but no role is accountable"
    if covered >= accept and specific >= accept and owned >= accept:
        return "met", "rule, mechanism and accountable role all present"
    return "vague", f"borderline — weakest signal P={min(covered, specific, owned):.2f}"
```

Domain rollups, coverage fractions and the remediation ordering are all
computed in `summarise_domains()` — arithmetic never goes to the model.

## Run it

```bash
cp ../../../.env.example .env     # add TYPESAFE_API_KEY or OPENROUTER_API_KEY
uv run streamlit run app.py
```

Bundled: 15 controls modelled on ISO 27001:2022 Annex A across six domains, and
a synthetic InfoSec policy that is genuinely good in places (privileged access,
screening, incident management, backups), aspirational in others (suppliers,
business continuity), unowned in one (leavers), and silent on three (threat
intelligence, cloud services, physical monitoring). It runs before you supply
anything.

```bash
JEV_PROVIDER=openrouter uv run streamlit run app.py
```

## Design notes

**Why three Nouls and not one Score.** A Score is an ordered rubric — it assumes
the levels lie on one line. These do not. "Specific but unowned" and "owned but
aspirational" are both worse than "met" and neither is worse than the other, so
there is no ordering to put them on. Three Nouls also means three sliders, and
the compliance lead can be strict about ownership while staying lenient about
specificity, which is a real operating point during a gap assessment.

**Why the Choice as well.** The Nouls tell you *whether* there is a gap; the
Choice tells you what **shape** it is, which is what routes the remediation
ticket. It is not redundant with the Nouls — it is the categorical view of the
same evidence, and where the two views disagree you have found a boundary case
worth a human minute.

**Why `covered` short-circuits.** An absent rule cannot be rescued by a named
owner. Averaging would let two strong signals drag a control with no rule at
all up to "mostly fine", which is the exact bug that makes automated gap
assessment untrustworthy.

**Thresholds are sliders.** Retuning re-runs no inference: the same 60
probabilities get re-read. `test_app.py` asserts no numeric threshold leaked
into a question.

## Limits

- **It reads your policy, not your practice.** A control can be perfectly
  documented and never performed. This finds documentation gaps, which is one
  of the three things an auditor tests and not the other two (evidence and
  operating effectiveness).
- **One policy document at a time.** Real control coverage is spread across a
  policy, several standards, a couple of runbooks and a Confluence page. Paste
  the concatenation, or run it per document and union the results yourself —
  the app does not do multi-document attribution.
- **Control catalogue extraction is your job.** It takes structured controls; it
  does not parse the standard's PDF into them.
- **`jev-1.13` cannot count, do arithmetic or compare dates.** Coverage
  percentages, domain rollups and "reviewed within 12 months" are all computed
  in Python. Do not be tempted to ask the model whether a review cadence is
  frequent enough — extract the cadence and compare it in code.
- **It reads literally.** A control written in standards-speak
  (*"as appropriate to the organisation"*) produces a mushy answer because the
  control is mush. Boundary cases belong in the `true`/`false` criteria.
- **It degrades on large noisy state.** A 60-page policy with appendices,
  revision history and a glossary makes every judgment worse. Feed it the
  normative sections.
- **Not an audit opinion, and not evidence.** It produces a prioritised reading
  list for the person who will write the Statement of Applicability. That person
  is still accountable for it.
