# 🕯️ text-adventure

A text adventure referee: the player improvises, Jev judges whether it works,
and **Python writes the prose**.

```bash
uv run --with httpx python adventure.py --script sample_playthrough.txt
```

```
> stand on the crate and squeeze through the grate
  You try, but the grate does not give. You need something to lever it.
  [succeeds on grate · danger 0.31 · 144 ms · in The flooded cellar]

> sing a song to the water
  You do it. Nothing about the room is different afterwards.
  [no_effect on water · danger 0.02 · 139 ms · in The flooded cellar]

> pry the grate open with my bare hands
  No. Not here, not with what you have.
  [impossible on grate · danger 0.88 · 151 ms · in The flooded cellar]
  It costs you skin. (1 scrape(s) so far.)

> lever the grate open with the crowbar
  The grate gives way.
  A brick tunnel running east, low enough that you stoop...
  [succeeds on grate · danger 0.24 · 147 ms · in The service tunnel]
```

## The problem

Every parser adventure ever written has the same wall: the author enumerates the
verbs, and the player immediately tries the one that isn't in the list. *Stand on
the crate. Wedge the crowbar in the door. Bail the water out with the lantern.*
Each of those is obviously reasonable, and each one gets "I don't know how to do
that" — the sentence that ends more play sessions than any puzzle.

The modern answer is to hand the whole game to an LLM, which produces the
opposite failure: a referee with no spine. It will let you talk your way through
a locked door, forget you are holding the crowbar, and invent a room that was
never in the world file. And it takes a second or two per turn to do it.

## Why this needs Jev

This is the clearest demo of **programmable common sense in a loop**. The loop is
the point: a referee is consulted on every single turn, so its cost and latency
are multiplied by the whole length of play. At roughly 150 ms the judgment is
faster than the player can read the previous line, so the game never visibly
stalls — and the referee stays affordable at thousands of turns.

The division of labour is the other half:

| | owns |
|---|---|
| **Jev** | does this action make sense here, what does it touch, would it hurt |
| **Python** | inventory, locks, exits, the scrape tally, and every word printed |

Jev cannot generate prose — it selects — and that constraint is exactly what
makes it a good referee. It cannot narrate you through a door that `apply_turn`
says is locked, because it never writes a sentence.

## How it works

One request per turn, three questions:

```python
"resolution": choice({"task": "Judge whether the player's action would work in this scene.",
                      "scene": "`scene`", "carrying": "`carrying`",
                      "player_action": "`player_action`"},
                     {"succeeds": "What the player described would plainly work with what is present...",
                      "blocked":  "Reasonable here, but something needed for it is missing...",
                      "impossible": "Nothing in this scene could make it work...",
                      "no_effect": "It would happen, and the scene would be exactly as it was...",
                      "unclear":  "The typed text is not an attempted action in this scene at all."}),
"target":    choice(..., {**room_objects, **carried, "nothing": "...operates on no listed object."}),
"dangerous": noul(..., true="Sharp edges, a fall, fire, or force applied to the player's own body",
                       false="Nothing about it would leave a mark"),
```

`dangerous` is a separate Noul because it is independent: an action can succeed
*and* cost you skin. Folding it into the resolution Choice would force the two
to compete for probability mass.

Then `apply_turn()` — pure Python, fully tested offline — checks the lock,
updates the inventory, and picks the narration line from a table.

**Per-call state is one room and one inventory**, never the world and never the
transcript. The model sees the scene it is judging and nothing else. That is
what keeps a turn at 150 ms and a few hundred tokens; dragging the whole play
history along would grow both without making a single judgment better.

## Run it

```bash
cp ../../../.env.example .env      # put your key in it
uv run --with httpx python adventure.py --script sample_playthrough.txt
uv run --with httpx python adventure.py --script sample_playthrough.txt --json
uv run --with httpx python adventure.py           # type your own, 'quit' to stop
```

Try `bail the water out with the lantern`, or `throw the crowbar at the
keypad`. Neither is in any verb table.

## Design notes

- **Choice for resolution, Noul for danger.** A turn resolves exactly one way, so
  resolution is a Choice — with `unclear` as the escape hatch, because a player
  who types "hello?" must not be handed a nominated outcome. Danger is orthogonal.
- **`target` is a second Choice, not parsing.** It tells Python *which* object the
  sentence acted on, which is what makes "wedge the crowbar into the door and
  lever it open" resolve against the door rather than the crowbar.
- **Python is authoritative on rules.** `apply_turn` re-checks `requires` against
  the real inventory. The offline test asserts that a confident `succeeds` on a
  locked grate does not move the player — that is the guard against the failure
  mode this whole design exists to avoid.
- **The scrape tally is Python arithmetic.** The model is never asked to keep a
  running count of anything; it cannot.

## Limits

- The referee has no memory across turns. Burning a rope in turn 3 does not stop
  you climbing it in turn 9 unless the world file models it. Persistent
  consequences are state you write, not something the model remembers.
- Prose is a fixed table, so the game reads terse. That is the trade: you cannot
  get generated narration out of a model that does not generate. Pair it with a
  writing model for flavour if you want both, and keep Jev on the judgment.
- `jev-1.13` reads literally. A poetic action ("let the dark take me") tends to
  land on `unclear`, which is the right failure but not a satisfying one.
- Three questions per turn at a few hundred tokens each is cheap, but it is not
  free — a 500-turn session is 500 requests. Cache nothing here; the whole point
  is that the scene changed.
- Latency shown is measured client-side per turn, including the network path.
