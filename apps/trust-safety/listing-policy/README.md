# 🏷️ Listing Policy

Runs a marketplace's **own** prohibited-items policy over a listing queue — one
Noul per clause, independent hazard Nouls beside them, enforcement in Python.

## The problem

Marketplace policy is a page of clauses that your legal team, your payment
processor, and one bad week in 2024 all contributed to. It is specific to the
categories you actually sell in. It changes when a regulator writes to you.

The review queue is staffed by people reading listings against that page. They
are fast at the obvious cases and slow at the ones that matter, they disagree
with each other at the margins, and the queue grows faster on weekends than the
rota does. Meanwhile a generic "is this listing bad?" classifier cannot tell you
whether a listing breaches **clause 4**, because clause 4 is about off-platform
payment and nobody trained a general model on your clause numbering.

## Why this needs Jev

The policy is a text box, so amending clause 4 changes the questions on the next
run — no fine-tune, no redeploy. Three structural properties make that work:

1. **One Noul per clause.** Clauses are independent and a listing can breach
   several at once, so each gets its own probability and its own gate. A single
   "violates policy" label would collapse exactly the dimension you need to tune
   per clause, and exactly the reporting your policy team wants back.
2. **Hazards are separate Nouls, not clauses.** Counterfeit tells, off-platform
   payment, and condition mismatch cut across every clause and can each be true
   independently. They are evidence, not authority: in `decide()` a hazard
   without a clause behind it routes to a human, never to a removal.
3. **Price.** Seven clauses × a thousand listings is seven thousand judgments —
   affordable at $0.042/Mtok, a budget meeting at frontier-model rates. That is
   what makes "re-run the whole queue after a policy amendment" a normal thing
   to do rather than a project.

## How it works

```python
for position, clause in enumerate(clauses):
    questions[f"clause_{index}_{position}"] = noul(
        {"task": "Does this listing breach the marketplace policy clause quoted below?",
         "clause": f"`policy[{position}].text`",
         "listing": reference},
        true="The listing does the thing this clause forbids, on the face of what is written",
        false="The listing does not do that thing, or the clause governs something else")

questions[f"counterfeit_{index}"]  = noul("... tells of a counterfeit of a branded item?")
questions[f"off_platform_{index}"] = noul("... pushes the buyer to pay outside the marketplace?")
questions[f"mismatch_{index}"]     = noul("... condition contradicts something else in the listing?")
questions[f"exposure_{index}"]     = score(..., EXPOSURE_LEVELS)      # what the buyer loses
questions[f"family_{index}"]       = choice(..., RESTRICTED_FAMILIES) # incl. none_apply
```

```python
def decide(breach, hazards, exposure, gates):
    flagged = [n for n, p in hazards.items() if p >= gates["hazard"]]
    if breach >= gates["breach"]:  return "remove" if exposure >= gates["remove"] else "human_review"
    if flagged:                    return "human_review"
    if breach >= gates["uncertain"]: return "human_review"
    return "approve"
```

A clause breach with low buyer exposure escalates rather than removes. Removal
is reserved for a clause breach *and* real money or safety on the line.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Seven sample clauses and ten synthetic listings ship with it: a replica handbag
with the usual hedging language, a streaming account sold for a bank transfer, a
recalled baby product, unlicensed supplement claims, a job lot with no
provenance, and five perfectly ordinary listings that must come back clean. It
runs cold.

## Design notes

- **Exposure is a Score.** "What does the buyer stand to lose" is a degree, and
  degrees need ordered levels that each describe a real situation. A Noul at 0.5
  would mean "yes and no equally likely", not "medium".
- **`none_apply` is mandatory.** Most listings belong to no restricted family. A
  Choice must return something, so without the escape hatch an ordinary
  bookshelf gets labelled `regulated_goods` at plausible confidence.
- **Low-confidence family labels are discarded in Python**, not pre-empted in
  the instruction. Policy lives in code so retuning costs nothing.
- **The model never counts.** Clause firing rates, queue sizes, and the worst
  clause per listing are Python. `jev-1.13` cannot count — documented failure
  mode, not a prompting problem.

## Limits

- **This assists human reviewers. It does not replace them.** Delisting removes
  someone's income; every decision that affects a user or a seller needs a human
  in the loop before it lands.
- **The thresholds are placeholders**, chosen so the sample data exercises all
  three routes. Tune them against listings your own reviewers have already
  actioned, and measure the false-removal cost of each gate before trusting it.
- **Text only.** Most counterfeit evidence is in the photographs — stitching,
  serial plates, a box that is the wrong shade. This app never sees an image, so
  a well-written listing for a fake will read clean.
- **No seller history.** Repeat offenders, velocity, linked accounts, and
  payout patterns are the strongest signals a marketplace has, and none of them
  are in a listing. Join this output to those signals; do not substitute it.
- `jev-1.13` reads literally and degrades on indirection. A clause that depends
  on an external recall register or a licensing regime cannot be applied, since
  that register is not in the state.
- Vague clauses give vague probabilities that hover near 0.5. Rewrite the
  clause; do not move the gate.
