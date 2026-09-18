#!/usr/bin/env python3
"""hallucination-detector -- is this generated answer grounded in the retrieved
context? Sentence by sentence.

    uv run --with httpx python app.py
    uv run --with httpx python app.py --data my_answers.json --json

Exit code 1 if any answer fails the grounding gate.

Sentences are split in PYTHON. jev-1.13 cannot count and cannot be asked to
enumerate, so the segmentation happens before anything is sent -- which also
means the indices in the output line up with real text a reviewer can read.

"Grounded" and "contradicts" are separate Nouls because a sentence the context
is simply silent about is neither. A third Noul catches the specific kind of
ungrounded sentence that does the most damage: a name, figure, date or ticket id
that appears nowhere in the context. A fourth spares the honest hedge -- "I
don't have a figure for that" is not a hallucination, and a single grounding
rubric would score it as the worst sentence in the answer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from jev_provider import JevClient, JevError, load_provider, noul, score  # noqa: E402

SAMPLE = HERE / "sample_answers.json"
CHARS_PER_TOKEN = 4
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

PROPERTIES = {
    "grounded": {
        "task": "Is this sentence supported by the provided context passages?",
        "true": "A reader with only those passages would assert this sentence",
        "false": "The passages do not support it, whether or not they argue against it",
    },
    "contradicts": {
        "task": "Does this sentence conflict with what the provided context passages say?",
        "true": "The passages assert something that cannot be true at the same time as this sentence",
        "false": "The passages agree with it, or say nothing about it",
    },
    "invented_specific": {
        "task": "Does this sentence introduce a name, figure, date, identifier or quotation that is absent from the provided context passages?",
        "true": "It supplies a concrete particular the passages never mention",
        "false": "Every particular in it also appears in the passages, or it states nothing particular",
    },
    "hedge": {
        "task": "Is this sentence the assistant talking about its own knowledge rather than making a claim about the subject?",
        "true": "It declines, caveats, or says the information is not available",
        "false": "It asserts something about the subject matter",
    },
}

HARM_LEVELS = [
    "If this sentence were wrong the reader would shrug and move on",
    "If this sentence were wrong the reader would form a mildly inaccurate picture",
    "If this sentence were wrong the reader would make a decision on a false premise",
    "If this sentence were wrong the reader would repeat it to someone who acts on it",
]


def load_data(path: Path | None) -> dict:
    source = path if path and path.is_file() else SAMPLE
    return json.loads(source.read_text(encoding="utf-8"))


def split_sentences(text: str) -> list[str]:
    """Segmentation is a Python job. Never ask the model to enumerate."""
    parts = [chunk.strip() for chunk in SENTENCE_END.split(text.strip()) if chunk.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def flatten(answers: list[dict]) -> list[dict]:
    """One row per sentence, carrying its answer id so results can be regrouped."""
    rows = []
    for answer in answers:
        for position, sentence in enumerate(split_sentences(answer["text"])):
            rows.append({"answer_id": answer["id"], "position": position, "text": sentence})
    return rows


def batch(rows: list[dict], limit_tokens: int) -> list[list[dict]]:
    """Split so each request's state fits the provider's context window."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for row in rows:
        size = max(1, len(json.dumps(row)) // CHARS_PER_TOKEN)
        if current and used + size > limit_tokens:
            groups.append(current)
            current, used = [], 0
        current.append(row)
        used += size
    if current:
        groups.append(current)
    return groups


def build_questions(rows: list[dict]) -> dict:
    """Five judgments per sentence, batched into ONE request per group."""
    questions: dict[str, dict] = {}
    for index in range(len(rows)):
        reference = {"sentence": f"`sentences[{index}].text`",
                     "context_passages": "`context`",
                     "question_being_answered": "`question`"}
        for name, spec in PROPERTIES.items():
            questions[f"{name}_{index}"] = noul(
                {"task": spec["task"], "target": reference},
                true=spec["true"], false=spec["false"],
            )
        questions[f"harm_{index}"] = score(
            {"task": "What would happen to a reader who relied on this sentence and it turned out to be false?",
             "target": reference},
            HARM_LEVELS,
        )
    return questions


def classify(props: dict[str, float], *, grounded_p: float, contradict_p: float,
             invented_p: float, hedge_p: float) -> str:
    """Per-sentence label. Pure Python, so thresholds are free to move."""
    if props["hedge"] >= hedge_p:
        return "hedge"
    if props["contradicts"] >= contradict_p:
        return "contradicted"
    if props["invented_specific"] >= invented_p and props["grounded"] < grounded_p:
        return "fabricated"
    if props["grounded"] >= grounded_p:
        return "grounded"
    return "unsupported"


def grade(labels: list[str], harms: list[float], *, harm_gate: float,
          bad_sentence_fraction: float) -> str:
    """Roll sentence labels up to one verdict for the whole answer."""
    scored = [(label, harm) for label, harm in zip(labels, harms) if label != "hedge"]
    if not scored:
        return "pass"
    bad = [(label, harm) for label, harm in scored
           if label in {"contradicted", "fabricated", "unsupported"}]
    if any(label in {"contradicted", "fabricated"} and harm >= harm_gate for label, harm in bad):
        return "fail"
    if len(bad) / len(scored) > bad_sentence_fraction:
        return "fail"
    return "review" if bad else "pass"


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-sentence grounding check on generated answers.")
    parser.add_argument("--data", type=Path, help="JSON: {question, context[], answers[]}")
    parser.add_argument("--grounded-p", type=float, default=0.60)
    parser.add_argument("--contradict-p", type=float, default=0.60)
    parser.add_argument("--invented-p", type=float, default=0.60)
    parser.add_argument("--hedge-p", type=float, default=0.70)
    parser.add_argument("--harm-gate", type=float, default=1.5,
                        help=f"0..{len(HARM_LEVELS) - 1}")
    parser.add_argument("--bad-fraction", type=float, default=0.34)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        provider = load_provider(start=HERE)
    except JevError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    data = load_data(args.data)
    rows = flatten(data["answers"])
    if not rows:
        print("no sentences to check", file=sys.stderr)
        return 2

    groups = batch(rows, int(provider.context_tokens * 0.4))
    print(f"checking {len(rows)} sentences from {len(data['answers'])} answers via {provider.name} "
          f"({len(groups)} request(s), {len(rows) * (len(PROPERTIES) + 1)} questions)",
          file=sys.stderr)

    judged: list[dict] = []
    try:
        with JevClient(provider=provider) as client:
            for group in groups:
                state = {"question": data["question"], "context": data["context"],
                         "sentences": group}
                answers = client.ask(state, build_questions(group))
                for index, row in enumerate(group):
                    props = {name: answers.noul(f"{name}_{index}") for name in PROPERTIES}
                    judged.append({
                        **row,
                        "properties": props,
                        "harm": answers.score(f"harm_{index}"),
                        "label": classify(props, grounded_p=args.grounded_p,
                                          contradict_p=args.contradict_p,
                                          invented_p=args.invented_p, hedge_p=args.hedge_p),
                    })
            cost, tokens, calls = client.total_cost_usd, client.total_input_tokens, client.calls
    except JevError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 2

    verdicts = {}
    for answer in data["answers"]:
        mine = [r for r in judged if r["answer_id"] == answer["id"]]
        verdicts[answer["id"]] = grade([r["label"] for r in mine], [r["harm"] for r in mine],
                                       harm_gate=args.harm_gate,
                                       bad_sentence_fraction=args.bad_fraction)

    if args.json:
        print(json.dumps({"sentences": judged, "verdicts": verdicts,
                          "cost_usd": cost, "input_tokens": tokens}, indent=2))
        return 1 if any(v == "fail" for v in verdicts.values()) else 0

    marks = {"grounded": "✅", "unsupported": "❔", "fabricated": "🚩",
             "contradicted": "❌", "hedge": "💬"}
    for answer in data["answers"]:
        verdict = verdicts[answer["id"]]
        print(f"\n── {answer['id']} ({answer.get('label', '')}) → {verdict.upper()} ──")
        for row in [r for r in judged if r["answer_id"] == answer["id"]]:
            print(f"  {marks[row['label']]} {row['label']:<13} harm {row['harm']:.2f}  "
                  f"{row['text'][:92]}")
            if row["label"] not in {"grounded", "hedge"}:
                print(f"       {' '.join(f'{k}={v:.2f}' for k, v in row['properties'].items())}")

    print(f"\n{calls} request(s) · {tokens:,} tokens · ${cost:.6f} measured · "
          f"${cost / max(1, len(rows)) * 1e6:,.2f} per million sentences checked")
    return 1 if any(v == "fail" for v in verdicts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
