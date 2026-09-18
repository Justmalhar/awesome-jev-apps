# ⚡ autocomplete-ranker

Rank completion candidates by what the user actually means — the whole set in
one request, inside the render loop.

```bash
uv run streamlit run app.py
```

```
situation: Inside a function that has just opened a database transaction and wrote two rows.
typed:     conn.|

rank  candidate             blended  P(intended)  popularity  popularity-only rank
1     conn.commit()         0.564    0.62         0.62        2
2     conn.cursor()         0.301    0.13         0.70        1
3     conn.rollback()       0.207    0.15         0.31        4
...
Latency 142 ms · 1 request · 5 candidates
```

## The problem

Autocomplete ranks by frequency, because frequency is what you can compute in a
millisecond. So the shell offers `git checkout main` when you just staged a hunk
with `git add -p`, the editor offers `.cursor()` when you are three writes into
an open transaction, and the help-centre offers *how to create an API key* to
someone typing *why did my webhook stop*.

Every one of those is the popular answer and the wrong one. The information that
would fix it — what just happened, what the user is in the middle of — is right
there, and the ranker cannot use it because the ranker is a counter.

## Why this needs Jev

Autocomplete lives in the render loop. The suggestion has to be on screen before
the next keystroke or it is noise, which is a budget of about 200 ms *total*,
not 200 ms of model time. Nothing that reasons in a second can go here at any
price.

And the shape fits exactly: ranking a candidate set is **one Choice**, so N
candidates cost one round trip, not N. That is the same property
`semantic-ctrl-f` uses to score a whole document — here it is what makes a
per-keystroke ranker affordable at all.

## How it works

```python
"intended": choice(
    {"task": "Which candidate is the user actually heading towards?",
     "surface": context["surface"], "situation": "`situation`", "typed_so_far": "`typed`",
     "note": "Choose 'none' if none of them is where this is going."},
    {**{c["id"]: c["text"] for c in candidates},
     "none": "None of these is what the user is heading towards."},
),
"worth_showing": noul(
    {"task": "Would any of these candidates actually save the user typing?", ...},
    true="At least one candidate is plainly where this input is going",
    false="The user is typing something none of these covers, and a popup would be in the way",
),
```

The probabilities over the Choice are a full ranking, not just a winner — so one
call orders the entire list. `rank()` then blends that with the popularity prior
you already have:

```python
blended = w * P(intended) + (1 - w) * popularity
```

Slide `w` to 0 and you get your existing ranker back exactly, which is the
honest baseline the table compares against.

**Per-call state is one line of situation plus the typed prefix** — the
candidate texts ride in the Choice criteria. A few hundred tokens, and flat in
the length of the session. An autocomplete that shipped the whole file or the
full search history per keystroke would be both slower and worse, since
`jev-1.13` degrades on large state full of irrelevant detail.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx --with streamlit streamlit run app.py
```

Four bundled scenarios: a help-centre search, an editor popup, a shell history
suggestion, and one where the right answer simply is not in the list.

## Design notes

- **One Choice, not N Nouls.** The candidates are mutually exclusive — the user
  is heading towards one of them — and the probability distribution over a single
  Choice *is* the ranking. Asking "is this the one?" per candidate would be N
  round trips for a worse answer.
- **`worth_showing` is separate because a Choice cannot decline.** It must
  nominate a winner even in the `nothing-fits` scenario, and it will, at
  respectable confidence. The Noul is what keeps the popup shut.
- **Blending, not replacing.** Popularity is real signal, cheap, and already
  computed. The slider is there because the right weight is a product decision
  measured on your own traffic, not a constant anyone can hand you.
- **Ties break on candidate id.** Deterministic ordering, so the popup does not
  flicker between two equal candidates across renders.

## Limits

- The candidate set has to come from somewhere. This ranks; it does not generate
  candidates, and it cannot suggest the completion nobody proposed.
- Set size is bounded by context, and by taste: 200 candidates is a large state
  full of irrelevant detail, which is where `jev-1.13` degrades. Prefilter to
  ~20 with your cheap ranker, then rank those.
- Latency is measured client-side, so the 200 ms budget is round trip *plus*
  render. On a mobile network the network is the budget — measure before you put
  this on a keystroke rather than on a pause.
- No counting or arithmetic is asked of the model; the blend and the popularity
  prior are Python. Asking it to weigh two numbers would be a documented failure
  mode.
- Candidate text is the only description the model gets. Cryptic candidates
  (`--no-ff`) rank poorly unless you pass a human-readable gloss instead.
