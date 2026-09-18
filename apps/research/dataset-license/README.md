# ⚖️ Dataset Licence

Judge a stated research intent against the licence text of every dataset you are
about to use.

> ## ⚠️ This is not legal advice
>
> This tool does not clear a dataset for use, does not constitute legal advice,
> and does not create any professional relationship. It is a **triage aid**: it
> tells a research group which of forty datasets need a qualified person to look
> at them, and roughly why. Licence interpretation depends on jurisdiction,
> on facts outside the licence text, and on rights the licensor may not actually
> hold. Every "consistent with intent" result still needs human sign-off before
> anything ships. If the answer matters, ask your institution's research
> contracts office or a solicitor.

## The problem

A lab assembles a project from eleven public datasets. Each arrives with its own
terms: a permissive notice, a data use agreement, a non-commercial share-alike
licence, a "terms of use" page that takes no position on model distribution.
Somebody — usually the most junior person — is asked to check "whether we can
use them".

They cannot answer that, because it is not a question. *Can we analyse it
internally* and *can we ship a model trained on it* have different answers from
the same licence, and the second one is the one that surfaces eighteen months
later when the work is being commercialised.

## Why this needs Jev

**Intent is state, and every (dataset × intended use) pair is an independent
judgment.** That is a rectangle of questions over a shared corpus of licence
text — precisely the shape that batches into one request.

The design choice that matters is **two Nouls per pair, not one**:

```python
questions[f"grant_{index}_{key}"]  = noul(... "affirmatively permit" ...)
questions[f"forbid_{index}_{key}"] = noul(... "restrict or prohibit" ...)
```

They are not complements, and the four corners are four real situations:

| grant | restriction | situation |
|---|---|---|
| high | low | permitted |
| high | high | **the licence contradicts itself across clauses** |
| low | low | **the licence is silent — and silence is not permission** |
| low | high | prohibited |

A single "is this allowed?" probability of 0.5 cannot distinguish *silent* from
*contradictory*, and those need opposite responses. The "silent" case is the one
that burns people: a terms-of-use page that never mentions model distribution is
not a grant, and the honest output is `not_granted`, not a coin flip.

The combination rule is a **veto, in Python**:

```python
blocked      ⟺ any intended use is prohibited or not granted
seek_advice  ⟺ any intended use is unclear
consistent   ⟺ every intended use is cleanly permitted
```

Never a weighted average. One non-commercial clause disqualifies a commercial
use no matter how generous every other clause is — that is what "any violation
disqualifies" means, and averaging gets it exactly backwards.

Obligations (attribution, share-alike, registration, no re-identification,
ethics approval) are **separate Nouls again**, because they attach to a use
without forbidding it, and they co-occur freely.

## How it works

Every use is judged for every dataset, even ones you did not tick. That is
deliberate: un-ticking "release the model publicly" in the UI re-decides the
whole table for **zero** additional inference, which is what lets you explore
"what intent would make this dataset usable?" interactively.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Six **synthetic** licence texts are bundled — written for this app, not copied
from any real licence — spanning a permissive notice, a restrictive clinical
data use agreement, non-commercial share-alike terms, a public domain
dedication, an academic web-corpus terms of use that pointedly *takes no
position* on releasing trained models, and an open government licence.

## Design notes

- **Uses are ticked in the UI, not free text.** A fixed vocabulary keeps the
  question crisp; `jev-1.13` reads literally and vague intent produces vague
  judgments.
- **The licence family is a Choice with `unclear`.** It is genuinely mutually
  exclusive and useful for sorting, but it is *descriptive only* — no policy
  branches on it, because "it's basically MIT" is how people get this wrong.
- **Obligations are not verdicts.** Attribution required does not block
  anything; it is a to-do list attached to a permitted use.
- **No dates, no arithmetic.** Embargo periods and expiry dates in licence text
  are never compared by the model — `jev-1.13` cannot compare dates. Parse them
  in Python if you need them.

## This assists a human researcher

The output is a queue, not a decision. Used as intended, a group of eleven
datasets becomes *"three need contracts to look at them, one is blocked as
stated, seven are consistent with what we said we would do — and here is the
obligations checklist for those seven."*

**Recall matters far more than precision**, even more than in literature
screening, because the failure modes are wildly asymmetric: a dataset wrongly
sent for legal review costs an email, and a dataset wrongly cleared costs a
retraction or a claim. The thresholds default to sending anything ambiguous to a
human, and the correct response to a noisy run is to *widen* that band.

## Limits

- **Reads the text you give it, and nothing else.** Licences incorporate other
  documents by reference ("subject to the Provider's Acceptable Use Policy") and
  those are not fetched.
- **Cannot know whether the licensor holds the rights they are granting.** The
  bundled web-corpus sample exists to make this visible: its terms disclaim
  exactly that, and no tool reading the licence can resolve it.
- **Jurisdiction-blind.** Database rights, text-and-data-mining exceptions and
  fair use vary by country and are not modelled at all.
- **Personal data is a separate regime.** GDPR and equivalents apply regardless
  of what the licence says. A permissive licence over personal data is not
  permission to process it.
- **`jev-1.13` degrades on large state full of irrelevant detail.** Paste the
  licence, not the entire terms-and-conditions page including cookie policy.
