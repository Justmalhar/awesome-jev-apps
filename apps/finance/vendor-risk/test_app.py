"""Offline checks for vendor-risk. No key, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import app

    vendors = json.loads((HERE / "sample_vendors.json").read_text(encoding="utf-8"))
    assert len(vendors) >= 6
    for vendor in vendors:
        assert set(vendor["documents"]) == {
            "company_profile", "security_questionnaire", "contract_summary", "public_record"
        }, vendor["vendor"]

    # -- criteria are independent properties, each with its own gate --
    ids = [c["id"] for c in app.CRITERIA]
    assert len(ids) == len(set(ids)), "criterion ids must be unique"
    assert len({c["gate"] for c in app.CRITERIA}) > 1, "gates that are all equal are one gate"
    for criterion in app.CRITERIA:
        assert 0.0 < criterion["gate"] < 1.0
        assert len(criterion["true"]) > 15 and len(criterion["false"]) > 15, criterion["id"]
    assert sum(1 for c in app.CRITERIA if c["hard_stop"]) == 1

    # -- one Noul per (vendor, criterion), one request --
    questions = app.build_questions(vendors)
    assert len(questions) == len(vendors) * len(app.CRITERIA)
    assert all(q["type"] == "noul" for q in questions.values()), (
        "independent risks are separate Nouls, never a Choice or a rubric"
    )
    assert set(questions[f"{ids[0]}_0"]["criteria"]) == {"true", "false"}

    # -- thresholding is per criterion, and must not be averaged --
    gates = {c["id"]: c["gate"] for c in app.CRITERIA}
    quiet = {i: 0.05 for i in ids}
    assert app.flagged(quiet, gates) == []
    one_loud = {**quiet, "regulatory_or_legal_exposure": 0.30}
    assert app.flagged(one_loud, gates) == ["regulatory_or_legal_exposure"], (
        "0.30 clears the 0.25 sanctions gate even though the mean is near zero"
    )
    assert sum(one_loud.values()) / len(one_loud) < 0.25, "an averaged score would have missed it"
    # the same 0.30 is BELOW several other gates -- that is the point of separate gates
    assert app.flagged({**quiet, "no_security_assurance": 0.30}, gates) == []
    at_gate = {**quiet, "data_leaves_region": 0.45}
    assert app.flagged(at_gate, gates) == ["data_leaves_region"], "gates are inclusive"

    # -- tiering --
    assert app.tier([], 3) == "standard"
    assert app.tier(["financial_distress"], 3) == "monitor"
    assert app.tier(["financial_distress", "no_security_assurance"], 3) == "monitor"
    assert app.tier(["financial_distress", "no_security_assurance", "contract_lacks_controls"], 3) == "enhanced_diligence"
    assert app.tier(["regulatory_or_legal_exposure"], 3) == "blocked", "a hard stop beats the count"
    assert app.tier(["financial_distress"], 1) == "enhanced_diligence", "the count threshold is a slider"


if __name__ == "__main__":
    check()
    print("ok")
