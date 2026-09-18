"""Offline checks for semantic-ctrl-f."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    blocks = app.split_blocks((HERE / "sample_lease.txt").read_text(encoding="utf-8"))
    assert len(blocks) > 10, f"sample lease should split into many clauses, got {len(blocks)}"

    questions = app.build_questions(blocks, "Can I keep a cat?")
    options = questions["best_block"]["criteria"]
    # Every block must be addressable or the model cannot select it.
    assert len(options) == len(blocks) + 1, "one option per block, plus 'none'"
    assert "none" in options, "a Choice must have a no-match escape hatch"
    assert all(str(i) in options for i in range(len(blocks)))
    assert questions["has_answer"]["type"] == "noul"

    # Huge paragraphs must fall back to line splitting.
    assert len(app.split_blocks("x" * 700 + "\n" + "y" * 700)) == 2


if __name__ == "__main__":
    check()
    print("ok")
