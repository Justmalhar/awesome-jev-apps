# 🏷️ Label Auditor

Point it at a labelled dataset. Get back the rows whose label contradicts their
own content, the rows that genuinely support two labels, and the confusion
pattern that tells you which annotation guideline is broken.

40 rows costs a fraction of a cent. 40,000 rows costs less than lunch — which is
the only reason anyone audits a whole training set instead of spot-checking 200
rows and hoping.

## The problem

The classifier plateaued at 0.86. You tried a bigger model, better embeddings,
class weights, focal loss. The ceiling did not move, because the ceiling is not
the model — it is the 4% of your training rows that are labelled wrong and the
6% that could defensibly take two labels. You are asking a model to learn a
function that the data says is inconsistent.

Finding those rows by hand means re-reading 40,000 tickets. Finding them with
cross-validation loss ranking gives you "rows the model got wrong", which is a
mixture of bad labels and genuinely hard examples, with no way to tell them
apart. So it doesn't get done, and the next quarter's model is trained on the
same broken set.

## Why this needs Jev

**The Score levels are the actions.** This is the structural trick. The obvious
design is "rate the label quality 0–10" and then hunt for a cutoff — which means
inventing a scale, fitting a threshold to it, and re-fitting whenever the data
changes. Instead the three levels are the three things you can actually do with
a row:

```python
LEVELS = [
    "The assigned label is clearly right for this content. Leave the row alone.",
    "The content genuinely supports more than one label. This row is ambiguous and "
    "belongs with a second annotator, or should be dropped.",
    "The assigned label contradicts the content. This row is mislabelled and should "
    "be corrected.",
]
```

There is no similarity scale being thresholded. A score of 1.0 is not "medium
badness", it is *ambiguous*, which is a different disposition from *wrong* —
ambiguous rows should go to a second annotator or be dropped, mislabelled rows
should be rewritten. A rating scale cannot distinguish those; a rubric whose
levels are dispositions does it by construction. The score lands *between*
levels, so 1.6 reads as "mostly contradiction, some genuine ambiguity", and the
sidebar decides where that tips into an automatic correction.

**Two questions, one request.** The verdict and "which label does the content
actually support?" are independent judgments over the same row, so both go into
the same `ask()`:

```python
questions[f"verdict_{index}"] = score({"task": "Judge the label this row was given "
                                               "against what the row actually says.",
                                       "row": reference}, LEVELS)
questions[f"suggest_{index}"] = choice({"task": "Ignoring the label already assigned, "
                                                "which label does this content support?",
                                        "row": reference, ...}, options)
```

Only the flagged rows' suggestions matter — but a second pass over the flagged
rows would re-ingest the same text to learn something the first pass could have
told us for free. Independent questions over the same state are answered in
parallel against one ingest. Batching is the whole economics of this app.

**The Choice carries its own escape hatch.** `no_label_fits` is not padding. Two
rows in the bundled sample (a meetup sponsorship request and a press enquiry) fit
none of the five labels, and without an escape hatch a Choice *must* file them
somewhere — at plausible confidence, because it always returns something. Those
rows mean the taxonomy has a hole, not that the annotator erred, and they route
to a human rather than to an automatic rewrite.

## How it works

The model judges rows. Pandas does every number:

```python
per_class["mislabel_rate"] = per_class["corrected"] / per_class["rows"]
pd.crosstab(flagged["label"], flagged["suggested"])     # the confusion pattern
share = flagged["label"].value_counts().iloc[0] / len(flagged)   # concentration
```

The model is never asked for a rate, a count, or "which class is worst". It
answers one row at a time and pandas aggregates. The concentration metric is the
one people act on: if 60% of the flagged mass sits in one class, that class's
annotation guideline is the bug.

The action comes from Python:

```python
def decide(verdict, assigned, suggested, confidence, *, correct_at, conf_gate):
    if verdict < REVIEW_AT:                        return "keep"
    if verdict < correct_at:                       return "review"
    if suggested == NO_FIT or suggested == assigned or confidence < conf_gate:
        return "review"
    return "correct"
```

Both `correct_at` and `conf_gate` are sidebar sliders, and the downloaded CSV
keeps the raw `verdict`, `suggested` and both confidences. Re-thresholding a
finished audit costs nothing — no second run, no second bill.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

40 synthetic support tickets ship with the app and are the default, so it runs
cold. Your own CSV needs `text` and `label`; the label set is read from the file,
so your taxonomy comes along automatically. Labels the app has no description for
get a generated one — good enough to run, worth replacing with a real definition
for a set you care about, because the boundary cases live in those descriptions.

## Design notes

**Why a Score and not two Nouls.** "Right", "ambiguous" and "contradicted" are
mutually exclusive dispositions on one ordered axis: more evidence for
contradiction is less evidence for the label being right. That is a rubric. It is
the opposite situation to `pii-scanner`, where the three properties are
independent and all three can be true at once — there they are three Nouls. The
test is always: can two of these be true simultaneously?

**Why the suggestion ignores the assigned label.** The Choice is deliberately
told to judge the content on its own terms. Showing it the assigned label while
asking what label the content supports invites agreement with whatever is already
there, which is exactly the error being audited.

**Why the confidence gate exists.** A Choice always returns a label. On
`"Please remove the seat for our leaver and refund the pro-rata"`, it will return
one with the probability mass split across `account_access` and `billing` — the
row is genuinely both. Low confidence on the replacement means there is nothing
safe to rewrite the row to, so it goes to a person instead of into the corrected
set.

**Why `REVIEW_AT` is a constant and `correct_at` is a slider.** The clean/not-clean
line is not a policy question — level 0 is "leave it alone". Where flagged
becomes *automatically corrected* is very much a policy question, and different
teams want it in different places.

## Limits

- **It audits the label against the text, not against your guideline.** If your
  guideline says billing complaints that mention cancellation are `cancellation`,
  the model does not know that, and will call those rows ambiguous. Encode such
  rules in the label descriptions (`LABEL_HINTS`) or expect noise from them.
- **Short text only.** State is the row. A dataset whose `text` is a 20-page
  document will blow the batch size long before it blows the context window, and
  the per-row cost stops being negligible.
- **It cannot tell you your true label noise rate.** It tells you how many rows
  it flagged at a given threshold. Those are different numbers, and the second is
  a function of where you put the slider.
- **Ambiguity is not symmetric with error.** A row can score high on
  contradiction because the content is confusing rather than because the label is
  wrong. That is what the second-annotator queue is for; do not automate it away.
- **`jev-1.13` cannot count or do arithmetic** — documented failure modes. Every
  rate, crosstab cell and percentage here is pandas. If you extend this, keep it
  that way.
- **Confidence is not correctness.** A confident `technical` suggestion on a row
  you know is `billing` means the text reads as technical, which is itself worth
  knowing — it is usually a sign the *text*, not the label, is the problem.
