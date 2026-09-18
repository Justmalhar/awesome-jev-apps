"""Offline checks for feed-ranker."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def check() -> None:
    import app

    stories = [{"title": "A compiler in Rust", "url": "u", "points": 1, "comments": 2}] * 3
    questions = app.build_questions(stories, "compilers")
    # 3 stories x (3 dimensions + 1 junk filter), all in one request.
    assert len(questions) == 3 * (len(app.DIMENSIONS) + 1) == 12, len(questions)

    for key, spec in app.DIMENSIONS.items():
        assert len(spec["levels"]) >= 2, key
        # Levels must describe situations, not be bare labels.
        assert all(len(level) > 15 for level in spec["levels"]), key


if __name__ == "__main__":
    check()
    print("ok")
