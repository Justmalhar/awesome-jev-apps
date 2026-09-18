"""Offline checks for citation-verifier. No key, no network."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    claims = app.load_claims(None)
    assert len(claims) >= 8
    assert {"id", "claim", "source_id", "source"} <= set(claims[0])

    questions = app.build_questions(claims)
    assert len(questions) == len(claims) * (len(app.PROPERTIES) + 1)

    # supported / contradicted must be SEPARATE nouls -- "silent" is neither.
    assert questions["stated_0"]["type"] == "noul"
    assert questions["contradicted_0"]["type"] == "noul"
    assert questions["stated_0"]["criteria"] != questions["contradicted_0"]["criteria"]
    for name in app.PROPERTIES:
        assert set(questions[f"{name}_0"]["criteria"]) == {"true", "false"}, name
    assert questions["drift_0"]["type"] == "score"
    assert all(len(level) > 15 for level in app.DRIFT_LEVELS)
    assert len(set(app.DRIFT_LEVELS)) == len(app.DRIFT_LEVELS)

    gates = {"support_p": 0.65, "contradict_p": 0.60, "subject_p": 0.50}
    base = {"stated": 0.0, "contradicted": 0.0, "same_subject": 0.9, "needs_inference": 0.0}

    assert app.verdict({**base, "stated": 0.9}, 0.2, **gates) == ("verified", False)
    assert app.verdict({**base, "contradicted": 0.8}, 3.0, **gates) == ("contradicted", False)
    # a source about something else is never a verdict on the claim
    assert app.verdict({**base, "stated": 0.95, "same_subject": 0.1}, 0.1, **gates) \
        == ("wrong-source", True)
    # silently unsupported but low drift is the subtle case a human should see
    assert app.verdict(base, 1.0, **gates) == ("unsupported", True)
    assert app.verdict(base, 3.0, **gates) == ("unsupported", False)
    # stated, but only via an outside assumption -> verified with a review flag
    assert app.verdict({**base, "stated": 0.8, "needs_inference": 0.7}, 1.0, **gates) \
        == ("verified", True)
    # boundaries
    assert app.verdict({**base, "contradicted": 0.60}, 1.0, **gates)[0] == "contradicted"
    assert app.verdict({**base, "stated": 0.65}, 1.0, **gates)[0] == "verified"

    assert len(app.batch(claims, 10**6)) == 1
    assert sum(len(g) for g in app.batch(claims, 30)) == len(claims)


if __name__ == "__main__":
    check()
    print("ok")
