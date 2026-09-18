# ⌨️ intent-palette

Type what you want in plain English; the palette ranks every action in the app
on **every keystroke**.

```bash
uv run streamlit run app.py
```

```
keystroke                                   top action              P      settled  verdict      ms
get thi|                                    Copy share link         0.191  0.204    keep_typing  147
get this in front of the|                   Invite collaborators    0.338  0.517    keep_typing  151
get this in front of the legal team wit|    Invite collaborators    0.402  0.664    show         144
...without letting them change it|          Toggle suggesting mode  0.561  0.902    show         139
```

## The problem

Command palettes are string matchers wearing a search box. `Cmd-K` then "make
read only" finds nothing, because the action is called *Toggle suggesting mode*
and the two strings share no characters. Users learn the vocabulary or give up,
and product teams paper over it with hand-written synonym lists that rot.

Embedding the catalog helps with vocabulary and fails on intent: "don't let
them edit it" and "let them edit it" sit next to each other in vector space.
A frontier LLM understands both — and takes a second per keystroke, which is
not a palette, it is a chatbot with extra steps.

## Why this needs Jev

The budget is a keystroke. At 60 wpm there are roughly 200 ms between
characters, so a judgment must land in less than that or it arrives after the
user has already typed past it. Jev answers in roughly 150 ms, measured, which
puts semantic ranking *inside* the input event rather than after it.

The second structural property matters just as much: the whole catalog is one
Choice, so 26 actions cost one request, not 26. Scaling the catalog costs
tokens, never round trips.

## How it works

One request per keystroke, two questions:

```python
"action": choice(
    {"task": "Select the action the user is trying to reach.",
     "typed_text": typed,
     "note": "Choose 'none' when no action in the catalog does this."},
    {**{a["id"]: f"{a['label']} - {a['description']}" for a in ACTIONS},
     "none": "Nothing in this catalog does what the typed text asks for."},
),
"settled": noul(
    {"task": "Is the typed text already specific enough to name one intended action?",
     "typed_text": typed},
    true="A reader of this text could name the single action the user wants",
    false="Still a fragment or an ambiguous opening that could lead to several different actions",
),
```

The Noul is doing the work a Choice structurally cannot. A Choice must return
something for `"g"`, and it will, at plausible confidence. `settled` is what
keeps the palette quiet until the intent has actually arrived.

**Per-call state is one string** — `{"typed_text": typed}`. The catalog rides in
the Choice criteria, which the provider ingests once per call and nothing else.
That is the entire reason the round trip stays inside a keystroke; the moment
you start shipping document context or user history per keystroke, the latency
budget is gone and this design stops working.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx --with streamlit streamlit run app.py
```

Press **Replay the whole typing sequence** to watch the ranking converge one
character at a time, with the measured latency of each keystroke beside it.

## Design notes

- **Choice for the action, Noul for readiness.** Exactly one action can run, so
  actions are a Choice. "Has the user finished expressing an intent" is an
  independent property and gets its own Noul — collapsing it into a `"still
  typing"` option would make it compete with real actions for probability mass.
- **Debounce is `should_fire()`, not a timer.** Minimum length plus a changed
  prefix. When the round trip is shorter than the inter-keystroke gap, a delay
  timer only adds latency the user can feel.
- **Both thresholds are sliders.** Tuning them re-runs no inference, because the
  decision is `decide()` in Python over two numbers.
- **Replay samples long queries.** 60 characters would be 57 requests; the
  replay takes 14 evenly spaced prefixes and always ends on the full string.

## Limits

- Latency is measured at the client, so it includes your network path. On a bad
  connection the round trip, not the model, is what breaks the budget.
- The catalog must fit the Choice criteria. 26 actions is nothing; a 2,000-action
  enterprise catalog needs a coarse Choice over groups first, then a second call
  over the winning group — the `agent-tool-router` pattern.
- `jev-1.13` reads literally. `"clean up"` gets you *Clear formatting* because
  that is what the catalog says; if your users mean *Resolve all comments*, that
  belongs in the option description, not in a synonym table.
- It selects, it does not execute, and it does not fill arguments. "Find and
  replace 'foo' with 'bar'" routes to `edit.find_replace` and hands you the raw
  string — extracting `foo` and `bar` is a separate step.
- No counting, arithmetic, or date comparison is asked of it anywhere, and none
  should be: those are documented `jev-1.13` failure modes.
