"""Offline checks for changelog-classifier. No key, no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check() -> None:
    import changelog as app

    prs = json.loads((HERE / "sample_prs.json").read_text(encoding="utf-8"))
    prepared = app.prepare(prs)
    assert len(prepared) == len(prs)

    # -- candidate lines are sliced by Python, verbatim ---------------------
    by_number = {p["number"]: p for p in prepared}
    scheduled = by_number[2211]
    assert scheduled["candidates"]["title"] == "Add scheduled exports to the reports page"
    assert scheduled["candidates"]["summary"].endswith("emailed.")
    assert scheduled["candidates"]["summary"] in " ".join(scheduled["body"].split())
    assert by_number[2218]["candidates"]["breaking_note"].startswith("Breaking:")
    assert by_number[2230]["candidates"]["breaking_note"].startswith("Migration:")
    assert by_number[2214]["candidates"]["breaking_note"] == ""
    assert by_number[2245]["candidates"]["summary"] == ""
    assert app.first_sentence("") == ""
    assert app.first_sentence("No terminator here") == "No terminator here"
    assert all(len(c) <= 240 for p in prepared for c in p["candidates"].values())

    # -- four judgments per PR, one request ---------------------------------
    questions = app.build_questions(prepared)
    assert len(questions) == 4 * len(prepared), len(questions)
    assert questions["section_0"]["type"] == "choice"
    assert questions["entry_0"]["type"] == "choice"
    assert questions["user_visible_0"]["type"] == "noul"
    assert questions["breaking_0"]["type"] == "noul"

    # -- both Choices need somewhere to put "nothing fits" ------------------
    assert "unclear" in questions["section_0"]["criteria"]
    assert "none" in questions["entry_0"]["criteria"]
    assert all(len(text) > 15 for text in app.SECTIONS.values())
    # every selectable entry source (bar the escape hatch) names a real string
    assert set(app.ENTRY_SOURCES) - {"none"} == set(prepared[0]["candidates"])
    # breaking and user-visible are never sections
    assert "breaking" not in app.SECTIONS and "user_facing" not in app.SECTIONS

    # -- assembly is Python: boundaries -------------------------------------
    def entry(**kwargs):
        base = {"number": 1, "title": "t", "line": "l", "section": "fix",
                "user_visible": 0.0, "breaking": 0.0}
        return {**base, **kwargs}

    gates = {"user_gate": 0.6, "breaking_gate": 0.6}
    assert list(app.assemble([entry(breaking=0.6)], **gates)) == ["breaking"]
    assert list(app.assemble([entry(breaking=0.59)], **gates)) == ["fix"]
    # an "internal" change everybody can see is rescued into Fixes
    assert list(app.assemble([entry(section="internal", user_visible=0.6)], **gates)) == ["fix"]
    assert list(app.assemble([entry(section="internal", user_visible=0.59)], **gates)) == ["internal"]
    assert list(app.assemble([entry(section="unclear")], **gates)) == ["unclear"]
    # breaking wins over everything, including an unclear section
    assert list(app.assemble([entry(section="unclear", breaking=0.9)], **gates)) == ["breaking"]

    # -- rendering copies the selected line verbatim ------------------------
    markdown = app.render(app.assemble(
        [entry(number=7, line="Add scheduled exports", section="feature")], **gates))
    assert "- Add scheduled exports (#7)" in markdown, markdown
    assert "## Feature" in markdown
    assert app.render({}).strip() == "# Release notes"


if __name__ == "__main__":
    check()
    print("ok")
