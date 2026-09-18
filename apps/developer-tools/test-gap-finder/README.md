# 🕳️ test-gap-finder

Which behaviours in this module have no test that would catch them being wrong?

```bash
cp ../../../.env.example .env
uv run --with httpx python test_gap.py sample_module.py sample_tests.py
```

```
GAP!  refund(amount, reason)                   risk 2.84/3
GAP!  authorise(user, order)                   risk 2.77/3
gap   tax(rate)                                risk 1.92/3
ok    subtotal()                               covered by test_subtotal_sums_line_totals
ok    apply_discount(code, percent)            covered by test_apply_discount_reduces_subtotal
...
2 risky gap(s) at risk gate 2.0
```

*(Illustrative. The tool prints its own measured footer.)*

## The problem

Coverage tools answer "was this line executed?", which is not the question. The
sample suite has a test named `test_refund_is_logged`:

```python
def test_refund_is_logged():
    """Exercises refund() on the way to checking the audit log."""
    order.refund(Decimal("10.00"), "damaged")
    assert audit_log.last().event == "refund"
```

`refund()` is 100% covered. Its overdraft check, its sign check, and its
arithmetic are all executed, and nothing in the suite would fail if any of them
were wrong. Coverage says green. The name says `refund`. A grep-based gap
finder says covered.

## Why this needs Jev

**Structure in code, matching in Jev.** `ast` lists both sides exactly —
public functions and methods with their docstrings, signatures, and the
exceptions they raise; test functions with their bodies. `difflib` blocks the
pairs down to a shortlist per behaviour. Only the last step needs judgment:

```python
questions[f"covers_{i}"] = noul(
    {"task": "Does this test verify that this behaviour is correct?",
     "behaviour": {...}, "test": {...}},
    true="The test asserts something that would fail if this behaviour were wrong",
    false="The test exercises this code only incidentally, or asserts about something else entirely")
```

That `false` criterion is the whole app. It is the difference between execution
and verification, it cannot be computed from an AST, and asking a frontier
model for it across every behaviour × every candidate test is a cost nobody
signs off on. Nine behaviours × four candidates plus nine risk scores is 45
questions in one request — a fraction of a cent, cheap enough for CI.

## Blocking, and why it is allowed to be bad

Pairing every behaviour with every test is quadratic. `candidate_pairs()` keeps
the four most name-similar tests per behaviour, on word overlap of names and
docstrings. It is a deliberately crude filter: its only job is to get the right
test into the shortlist. It cannot decide anything, because name similarity is
exactly what `test_refund_is_logged` fools.

## Risk, so the output is a queue and not a list

A gap list nobody prioritises is a gap list nobody reads. Each behaviour also
gets a Score:

```python
"Nothing depends on this being right: a debug helper, a repr, or a thin pass-through",
"A mistake here shows up immediately in the next thing a developer runs, and is obvious when it does",
"A mistake here reaches a user as wrong output or a confusing error, but is recoverable once noticed",
"A mistake here loses data, mischarges money, or lets somebody through a check that should have stopped them",
```

`format_receipt` and `refund` are both untested. Only one of them should stop a
release, and `--risk-gate` is where you say which.

## Run it

```bash
uv run --with httpx python test_gap.py src/orders.py tests/test_orders.py
uv run --with httpx python test_gap.py src/orders.py tests/test_orders.py --json
uv run --with httpx python test_gap.py src/orders.py tests/test_orders.py --cover-gate 0.8
```

Exits `1` when an untested behaviour clears the risk gate, so it works as a
check on the modules you care most about.

## Design notes

**Why coverage is a Noul per pair and risk is a Score per behaviour.** They
live at different granularities and nothing is gained by forcing them together.
Coverage is a yes/no about a *pair* — did this test verify that behaviour —
and the behaviour's overall coverage is `max()` over its shortlist, computed in
Python. Risk is a property of the *behaviour* alone and is ordinal, so it is a
Score, asked once.

**Why the risk rubric is written from the consequence.** "Critical / major /
minor" would be a label the model maps onto its own priors. "Loses data,
mischarges money, or lets somebody through a check" is a situation it can
recognise in the code in front of it.

## Limits

- **Two files in.** One module, one test file. A behaviour tested from a
  different test module reads as a gap. Concatenate your test files, or run it
  per pair and merge in Python.
- **Python only**, and public names only — `_private` helpers and dunders are
  skipped on the assumption that they are tested through the surface.
- **Blocking can lose a true match.** A test named `test_admin_path` that
  verifies `authorise()` may not make the shortlist. Raise `--candidates`; it
  costs tokens linearly.
- **Sources are truncated to 900 characters.** A long test's assertions may be
  cut off, which biases towards reporting a gap.
- **No parametrisation awareness.** A single `@pytest.mark.parametrize` covering
  twelve cases is one test function here, and fixtures are invisible.
- **`jev-1.13` cannot count**, so there is no "how many assertions" heuristic
  anywhere in this — coverage is a judgment about whether an assertion would
  fail, not about how many exist.
- **A gap is not a bug.** Some behaviours genuinely do not need a test. The
  risk score is there to make that a decision rather than a backlog item.
