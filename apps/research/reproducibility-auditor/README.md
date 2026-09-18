# ♻️ Reproducibility Auditor

Does this paper give a stranger enough detail to rebuild the result? Criterion
by criterion, with an explicit human tier.

## The problem

Reproducibility checklists — the NeurIPS checklist, ACM artifact badging, the ML
Reproducibility Checklist — are almost always filled in by the authors about
their own work. That is the same conflict of interest that makes reporting gaps
so persistent: you cannot see what you forgot to write.

The people who should apply them independently are reviewers, artifact
evaluation committees, and the graduate student who has been told to "build on
this paper". All three do it by hand, one paper at a time, and all three quietly
give up somewhere around paper eleven.

## Why this needs Jev

This app is a demonstration that the three primitives are not interchangeable,
and that picking the wrong one destroys information:

| Property | Primitive | Why |
|---|---|---|
| data available? code released? seeds fixed? | **Noul, one each** | They co-occur freely and are separately actionable. Nothing orders them. |
| how completely the environment is specified | **one Score** | These *are* degrees of one thing: no software named → framework named → versions given → a pinned lockfile. That is an ordered rubric, and a Score reports a position that can land *between* levels. |
| how artifacts are obtained | **one Choice** | Open download, registered access, on request, unavailable — mutually exclusive by construction, with an explicit `unclear` escape hatch. |

Collapsing the criteria into one "reproducibility score" would let a released
Dockerfile compensate for unobtainable data, which is not how reproducibility
works — unobtainable data is a veto. Splitting the environment rubric into four
Nouls would throw away the ordering and force you to reconcile four
probabilities that are not independent.

The verdict is conjunctive with a veto, in Python:

```python
blocked ⟺ any mandatory criterion clearly unmet, or no route to the artifacts
human   ⟺ any mandatory criterion ambiguous, or environment below the floor
likely  ⟺ everything mandatory met and the environment pinned
```

## The human tier is the feature

An audit that returns only *reproducible* / *not reproducible* is an audit
nobody trusts, because the genuinely interesting papers are the ambiguous ones.
Three tiers, and the middle one is populated on purpose: those are the papers
where the *text does not settle the question*, which is itself the finding you
wanted.

Which criteria are mandatory is a sidebar checkbox. Artifact evaluation
committees weight these differently from journals, and switching between those
policies re-runs **no inference** — the probabilities are already paid for.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Five synthetic papers are bundled: a fully-instrumented ML paper with a
lockfile, a clinical model where the data genuinely cannot be shared, a
theory-ish heuristics paper with no released implementation, a dataset paper
that is exemplary, and a field experiment that is preregistered but records no
package versions.

## Design notes

- **Every question asks what the paper *states*, never what is true.** "The
  authors presumably fixed a seed" is a different claim from "the paper says
  so", and only the second is auditable.
- **Score, not Noul, for the environment.** A Noul is not an intensity dial —
  P=0.5 means genuinely split, not "half-specified". `answers.score()` lands
  between levels, which is exactly right for a paper that pins some versions and
  not others.
- **`environment_floor` is a slider on a Score.** 2.0 means "libraries named
  with versions". Lower it for fields where that is unusual.
- **No arithmetic or dates.** GPU-hours, seed counts and embargo periods in the
  sample text are never asked about. Counting is a documented `jev-1.13` failure
  mode; parse in Python if you need numbers.

## This assists a human researcher

It triages; it does not certify. Nothing here runs the code, and "likely
reproducible" means *the paper describes enough*, not *it reproduces*. Those are
famously different claims — artifact evaluation exists because they diverge.

**Recall matters more than precision**, as in any screening task. A paper
wrongly sent to the human tier costs a few minutes of reading; a paper wrongly
marked reproducible wastes a month of someone's PhD. The defaults route
aggressively to the middle tier, and that is the intended bias.

## Limits

- **Reads text, does not execute anything.** A repository link that 404s reads
  as code released.
- **Cannot judge quality of a stated detail.** "Hyperparameters in Appendix B"
  scores as settings given, whether or not Appendix B is complete.
- **Section-scoped.** Availability statements often live outside the methods.
  Paste the whole relevant text, including the data-availability and code
  sections, or you will get false gaps.
- **`jev-1.13` degrades on large state full of irrelevant detail.** Feeding an
  entire 30-page PDF is worse than feeding the methods, results protocol and
  availability statements. Filter first.
- **"Not applicable" is not modelled.** A purely theoretical paper has no data
  to release. Un-tick the criterion rather than reading the block as a finding.
