# 📡 Fraud Signals

Payment narratives in, feature matrix out. Eleven probability columns and a score,
joined to your case table on `case_id`, ready for the model that actually makes
the decision.

**This app does not tell you whether a payment is fraud.** There is no risk score
in it and no threshold anywhere. That is the design, not an omission.

## The problem

Authorised push payment fraud is a text problem wearing a payments costume. The
transaction itself looks fine — a customer moving their own money to an account
they chose. Everything that distinguishes a house completion from a safe-account
scam is in what the customer *says* when asked why:

> "Man from the bank fraud team rang me… He stayed on the line while I moved my
> savings to the secure holding account he gave me. He told me not to mention it
> to the branch because staff might be involved."

Transaction-graph features cannot see any of that. So the narrative gets read by
a human, in a call, under time pressure, with a payment queued — or it gets a
keyword rule that fires on "safe account" and nothing else.

## Why this needs Jev — and why it must not give a verdict

**The features are real, and they are readable.** Third-party instruction,
secrecy, authority impersonation, promised returns, an unmet relationship, a
refused inspection, a late change of bank details. These are properties of a
narrative, they co-occur in specific patterns per scam type, and they are exactly
the columns a gradient-boosted model wants beside your velocity, device and
beneficiary-age features.

**The decision is not.** Blocking a payment is a calibrated trade-off between a
customer whose house purchase fails on the day and a pensioner who loses
everything. That trade-off comes from labelled outcomes and a loss function you
own. An LLM has no access to either, and a threshold picked inside a prompt is
that trade-off made by accident, invisibly, by someone who has never seen your
false-positive cost.

So the app is eleven Nouls and one Score, and it stops:

```python
questions[f"{feature['id']}_{index}"] = noul(
    {"task": "Judge whether this one property is present in the customer's account of the payment.",
     "property": feature["label"],
     "payment": reference,
     "note": "Judge only what the account says. Do not infer whether the payment is "
             "fraudulent; that is decided elsewhere."},
    true=feature["true"], false=feature["false"],
)
```

`test_app.py` asserts the app contains no `risk_score`, no `is_fraud`, no
`decide()`. If someone adds one, the build fails.

**Why Nouls and not one Choice of scam type.** Because PC-1014 is a job scam
*and* an advance-fee scam *and* has a secrecy instruction. Real scams trip four
or five of these at once; a Choice would force one label and throw the rest away.
Independent properties, independent columns — that is the entire reason the
downstream model can learn that `secrecy_instruction × promised_return` is worth
more than either alone.

**Raw probabilities go downstream.** No rounding, no bucketing to booleans.
`0.62` and `0.98` are different evidence, and a tree will happily split on the
difference. Thresholding at export throws away the only thing that made this
worth doing.

## How it works

```
narratives  ->  11 Nouls + 1 Score per case, ONE request
            ->  feature_row()    fixed column order, fails loudly if ragged
            ->  feature_frame()  pinned schema, versioned as "fraud-signals/1"
            ->  fraud_features.csv
```

The Streamlit page shows two diagnostics, and both are about **feature quality,
not fraud**: prevalence (a feature present in every case or in none is a column
your model cannot use) and pairwise correlation (two features that move together
are one feature with extra steps).

Column order is pinned and the schema is versioned, because the thing downstream
of this is a model that breaks silently when a column moves.

## Run it

```bash
cp ../../../.env.example .env
uv run streamlit run app.py
```

Bundled: 15 payment cases — a safe-account scam, a romance scam, a task-work
scam, an investment platform, a purchase scam, an invoice redirect, a puppy
deposit — mixed with entirely ordinary payments (rent to a flatmate, a gas bill,
a game) and, importantly, a genuine house completion that looks superficially
alarming and should come out clean on the features that matter.

## Design notes

- **Feature definitions are data.** Add a column by adding a dict with `id`,
  `label`, `true`, `false`. Bump `SCHEMA_VERSION` when you do — the downstream
  model needs to know its inputs changed.
- **The instruction tells the model what *not* to infer.** "Do not infer whether
  the payment is fraudulent; that is decided elsewhere" is load-bearing.
  `jev-1.13` reads literally, and without that line, features start absorbing an
  overall fraud impression and correlate with each other instead of with the
  evidence.
- **The Score measures corroboration, not risk.** Its three levels are three
  recognisable situations — took the other party's word, added their own detail,
  verified through a channel they chose themselves. That last one is what makes
  PC-1013 (the solicitor rung on a number from the website) different from
  PC-1009 (bank details that arrived by email).
- **`presence_gate` is a diagnostics slider only.** It affects the prevalence
  chart and nothing else. The exported matrix is raw.

## Limits

- **Garbage narrative, useless row.** PC-1006's entire account is "Bought a
  game." Eleven features come back near zero, which is correct and also
  uninformative. The quality ceiling of this app is the quality of your call
  notes.
- **Features are unvalidated until you validate them.** Nobody has shown these
  eleven separate signal on real labelled data. Prevalence and correlation are in
  the UI so you can throw out the ones that do not — expect to.
- **A narrative is a summary written by someone.** A call handler who has already
  decided this is a scam writes a different note than one who has not. That bias
  lands in the features and the model will learn it.
- **No arithmetic, no dates, no counting.** `jev-1.13` cannot do any of them, so
  amount, velocity, beneficiary age and time-of-day are absent. They are your
  existing features and they belong in the same matrix, joined on `case_id`.
- **This is the cheap half of a fraud system.** The expensive half is the labelled
  outcome data, the calibration, and the appeals process for the customers you get
  wrong. Nothing here helps with any of that.

## Privacy

Customer narratives, payee names and payment references are sent to whichever
provider `providers.toml` selects. These narratives are among the most sensitive
text a bank holds — they routinely contain health, relationship and vulnerability
information disclosed in distress. Confirm your lawful basis and read the
provider's retention policy before this touches a real case file, and consider
whether the narrative needs pseudonymising first.
