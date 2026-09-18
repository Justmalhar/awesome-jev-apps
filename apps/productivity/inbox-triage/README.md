# 📥 Inbox Triage

What in your mail actually needs you today.

Point it at a folder of `.eml` files (every mail client can export them) or run
it against the bundled sample inbox.

## Why this needs Jev

### It sorts, it never rewrites

Jev cannot generate. That is a feature here: every subject line and body you see
is **your own text**, in your own words. There is no summary to mistrust, no
paraphrase that quietly drops the deadline, no hallucinated sender.

This is the difference between "AI reads my email" and "AI decides what I read."
Only the second one is safe to leave running.

### The judgment that matters

Not *"is this important?"* — unanswerable in the abstract, and the reason
generic priority-inbox features feel random. The useful question is **"does this
need an action from me, and by when?"**, which is three independent things:

```python
mine     = noul("Does this require an action or reply from the recipient personally?")
deadline = noul("Does this state or clearly imply a deadline?")
waiting  = noul("Is the sender following up because a previous message went unanswered?")
urgency  = score("How soon does this need attention?", [...4 concrete levels...])
kind     = choice("What kind of email is this?", {...})
```

These are separate questions because they vary independently. A newsletter can
have a deadline. A cc'd thread can be urgent for someone else. A polite "no rush"
follow-up from someone chasing you for the third time is *exactly* the thing a
single importance score buries — and exactly what the `waiting` Noul surfaces.

12 messages × 5 judgments = 60 questions, one request.

## Sorting is code

```python
if mine >= mine_gate and urgency >= today_gate:   today.append(...)
elif mine >= mine_gate:                           this_week.append(...)
else:                                             rest.append(...)
```

Both sliders re-sort the whole inbox instantly and spend nothing, because the
judgments don't change when your definition of "today" does. Somebody who checks
mail twice a day and somebody who checks it hourly want different gates and the
same evidence.

## Run it

```bash
cp ../../.env.example .env
uv run streamlit run app.py
```

For real mail, export a folder of `.eml` files and paste the path in the
sidebar. The bundled sample has the cases worth watching: an accountant chasing
you for the third time with a hard deadline, a "no rush" question that genuinely
isn't urgent, an overdue invoice, several automated receipts, and a recruiter.

## Privacy

Subjects, senders, and the first 1,200 characters of each body are sent to
whichever provider `providers.toml` selects. Read that provider's data policy
before pointing this at a real mailbox. Nothing is stored by this app — it holds
results in Streamlit session state and forgets them when you close the tab.

## Limits

- **No thread awareness.** Each message is judged alone; a long thread's final
  message may read as context-free. Grouping by `References`/`In-Reply-To`
  before sending would fix it and is a good first contribution.
- **No date arithmetic.** `jev-1.13` cannot compare dates, so "is this deadline
  within 48 hours?" is not asked. It detects that a deadline *exists*; parse and
  compare the date in Python if you need the countdown.
