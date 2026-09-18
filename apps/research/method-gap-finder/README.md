# 🧪 Method Gap Finder

Runs the reporting checklist over a methods section before a reviewer does.

## The problem

Desk rejections and major revisions cluster around a small, boring set of
omissions: no control condition, no rationale for the sample size, no ethics
statement, no preregistration. CONSORT, STROBE, ARRIVE and PRISMA exist because
these gaps are *predictable* — and they are still missed constantly, because the
person who wrote the methods section is structurally the worst person to notice
what is not in it.

The cost is asymmetric. Catching a missing preregistration statement before
submission takes an hour. Catching it in review costs a review cycle, three
months, and sometimes the paper.

## Why this needs Jev

**The checklist is a set of independent conditions with a veto rule, and that is
a Python problem wearing a language problem's clothes.**

Each item is its own Noul:

```python
questions[f"chk_{index}_{item['key']}"] = noul(
    {"task": item["question"], "methods_section": f"`manuscripts[{index}].methods`"},
    true=item["true"], false=item["false"],
)
```

and the verdict is conjunctive in code:

```python
blocking      ⟺  any CRITICAL item is clearly absent
needs review  ⟺  any CRITICAL item is ambiguous
ready         ⟺  every CRITICAL item is clearly present
```

A single "rate the methodological quality of this paper" rubric is the obvious
alternative and it is wrong in a specific way: it lets exemplary ethics
reporting *compensate* for the absence of a control group. Weighted scores model
compensating preferences. A reporting checklist is not a compensating
preference — a missing control condition is a veto, and no amount of good
blinding offsets it.

The independence also makes severity a **runtime policy**. The sidebar lets you
move an item between blocking and advisory, and the verdicts recompute with no
inference re-run at all: ARRIVE weights blinding differently from STROBE, and
that is one checkbox, not another API call.

## How it works

All `manuscripts × items` Nouls go in one request. Then three bands per item —
present / absent / **unclear** — and the unclear band is load-bearing. A
checklist tool that forces every item to yes-or-no produces confident wrong
answers on exactly the items an author most needs to look at.

Eight items ship by default; four are critical (control, sample size rationale,
ethics, preregistration) and four advisory (consent, eligibility criteria,
blinding, data availability).

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five synthetic methods sections are bundled, spanning the cases that matter: a
fully reported clinical trial, an ML paper where ethics and preregistration are
genuinely not applicable, an uncontrolled workplace study missing nearly
everything, an animal study that reports ethics well and preregistration not at
all, and a qualitative study where "no comparison group" is a correct design
choice rather than a gap.

## Design notes

- **Nouls, not a rubric.** The items co-occur freely and are separately
  actionable; there is no ordering among them to put on a scale.
- **The question asks whether the text *states* it, not whether it *happened*.**
  This is a reporting checklist. "The authors surely got ethics approval" is not
  the same claim as "the methods section says so", and only the second is
  checkable from the manuscript.
- **Thresholds are inclusive at both ends** and default to 0.75 / 0.25. Tune
  against a handful of manuscripts you have already had reviewed.
- **Nothing numeric is asked of the model.** "Is the sample size adequate?" is
  *not* a question here, and could not be — it would need arithmetic against an
  effect size. The question asked is whether a *rationale is stated*, which is
  a qualitative property of the text. If you need the sample size as a number,
  parse it in Python.

## This assists a human researcher

It flags candidates for a human to confirm; it does not certify a manuscript.
Several items are legitimately inapplicable by design — an ML benchmark paper
has no ethics committee, an exploratory qualitative study has no control arm —
and only a human knows which absences are correct.

**Recall matters more than precision**, as in every screening task. A false flag
costs ten seconds of reading; a missed gap costs a review cycle. The defaults
push borderline items into the unclear band deliberately, and the right response
to a noisy run is to widen that band, not to narrow it.

## Limits

- **Reads the methods section only.** Ethics and registration statements
  frequently live in a declarations section at the end of the paper. Paste in
  whatever text actually carries them, or you will get false gaps.
- **Cannot judge whether a stated justification is any good.** A power analysis
  based on an implausible effect size reads as present. That judgement is a
  reviewer's.
- **`jev-1.13` reads literally.** A registration identifier quoted with no
  surrounding sentence may not register as a registration *statement*. The
  `true`/`false` criteria are where you fix that for your own field's
  conventions.
- **Inapplicability is not modelled.** There is no "not applicable" state, and
  adding one would mean asking the model to infer study design as well. Turning
  the item advisory in the sidebar is the intended workaround.
