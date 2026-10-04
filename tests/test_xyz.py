"""Resumes use Google XYZ bullets written from the master CV, never with invented numbers."""

import asyncio
import pytest
from fastapi.testclient import TestClient

from backend import ai, db, master_resume, resumes, xyz
from backend.resumes import build_resume, render_html


ENTRY = {
    "id": "scdot", "kind": "experience", "title": "AI Specialist", "organization": "Clemson University", "start": "Jul. 2025",
    "original": "Sole engineer delivering agentic AI systems.\n"
                "SCDOT AI Contract Assistant (South Carolina Department of Transportation)\n"
                "• Improved OCR accuracy on historical contracts from 72% to 99.9%.\n"
                "◦ Built a contract-drafting assistant for SCDOT experts.",
    "enhanced": "", "confirmed": True,
}


def rewrite(*texts, measured=True):
    return [ai.XYZBullet(text=text, measured=measured) for text in texts]


def test_only_bullets_are_rewritten_and_intro_lines_stay_as_written():
    assert [(marker, text) for _, marker, text in xyz.bullets(ENTRY["original"])] == [
        ("• ", "Improved OCR accuracy on historical contracts from 72% to 99.9%."),
        ("◦ ", "Built a contract-drafting assistant for SCDOT experts.")]
    assert [text for _, _, text in xyz.bullets("Led a team.\nShipped the app.")] == ["Led a team.", "Shipped the app."], \
        "Entries without bullet symbols are shown as bullets, so all their lines are rewritten."


def test_invented_numbers_are_rejected_and_the_original_bullet_is_kept():
    fields = xyz.assemble(ENTRY, rewrite(
        "Raised OCR accuracy from 72% to **99.9%** by building a multi-agent correction framework.",
        "Cut drafting time by 40% by building a contract-drafting assistant for SCDOT experts."))
    lines = fields["xyz"].splitlines()
    assert lines[:2] == ENTRY["original"].splitlines()[:2], "The intro and the sub-project heading stay as written."
    assert lines[2] == "• Raised OCR accuracy from 72% to **99.9%** by building a multi-agent correction framework."
    assert lines[3] == "◦ Built a contract-drafting assistant for SCDOT experts.", "40% is not in the CV, so the bullet is not used."
    assert fields["xyz_measured"] == [True, False]
    assert fields["xyz_basis"] == resumes.text_hash(ENTRY["original"])
    assert xyz.assemble(ENTRY, rewrite("Only one bullet came back.")) is None, "A rewrite that doesn't line up is not used."
    assert xyz.numbers("Saved $1,200 for 3 teams in 2025") == {"1200", "3", "2025"}


def test_resumes_use_xyz_wording_only_while_it_matches_the_entry():
    entry = {**ENTRY, **xyz.assemble(ENTRY, rewrite("Raised OCR accuracy from 72% to 99.9% by correcting errors with agents.",
                                                    "Built a drafting assistant by grounding it in SCDOT contracts."))}
    html = render_html(build_resume({"profile": {"name": "Sam"}, "skills": [], "items": [entry]}))
    assert "Raised OCR accuracy from 72% to 99.9% by correcting errors with agents." in html
    assert "Improved OCR accuracy" not in html
    changed = {**entry, "original": entry["original"] + "\n• Wrote the evaluation plan."}
    assert resumes.resume_text(changed) == changed["original"], "Once the wording changes, the old XYZ version is ignored."
    assert xyz.pending({"items": [changed]}) == [changed] and xyz.pending({"items": [entry]}) == []


def test_xyz_request_sends_each_entrys_bullets(monkeypatch):
    seen = {}

    async def rewrite_xyz(settings, entries, effort=None, timeout=None):
        seen.update(entries=entries, effort=effort)
        return ai.XYZRewrite(entries=[ai.XYZEntry(id="scdot", bullets=rewrite("Raised OCR accuracy from 72% to 99.9% by fixing errors.",
                                                                                  "Built a drafting assistant by grounding it in contracts."))])

    monkeypatch.setattr(ai, "rewrite_xyz", rewrite_xyz)
    written = asyncio.run(xyz.write({}, [ENTRY], effort="ultra"))
    assert seen["effort"] == "ultra" and seen["entries"][0]["bullets"] == [text for _, _, text in xyz.bullets(ENTRY["original"])]
    assert set(written) == {"scdot"}
    assert "Google's XYZ format" in ai.XYZ_INSTRUCTIONS and "Never invent" in ai.XYZ_INSTRUCTIONS


@pytest.fixture
def workspace():
    db.initialize()
    db.mutate_state(lambda state: state.update(items=[dict(ENTRY)], jobs=[
        {"id": "job", "title": "AI Engineer", "description": "Agents.", "status": "saved", "resume": {"items": []}}]))


def test_xyz_is_written_after_the_master_resume_and_refreshes_tailored_resumes(workspace, monkeypatch):
    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level or "ultra"

    async def rewrite_xyz(settings, entries, effort=None, timeout=None):
        assert (settings["model"], effort) == ("gpt-6-astra", "ultra")
        return ai.XYZRewrite(entries=[ai.XYZEntry(id="scdot", bullets=[
            ai.XYZBullet(text="Raised OCR accuracy from 72% to 99.9% by correcting errors with agents.", measured=True),
            ai.XYZBullet(text="Built a drafting assistant by grounding it in SCDOT contracts.", measured=False)])])

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "rewrite_xyz", rewrite_xyz)

    async def exercise():
        master_resume.start_xyz()
        await master_resume._task
    asyncio.run(exercise())
    state = db.get_state()
    assert xyz.current(state["items"][0]) and state["items"][0]["xyz_measured"] == [True, False]
    assert "resume" not in state["jobs"][0], "Tailored resumes are rebuilt with the XYZ wording."
    message = master_resume.status()["message"]
    assert "Google XYZ wording for 1 entry" in message and "1 bullet has no measurable result" in message


def test_you_can_edit_the_xyz_wording_and_editing_the_entry_keeps_it(workspace):
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        mine = "Sole engineer delivering agentic AI systems.\nSCDOT AI Contract Assistant\n• Raised OCR accuracy to 99.9% for 3,500 contracts.\n◦ Built a drafting assistant."
        saved = client.put("/api/items/scdot/xyz", json={"xyz": mine})
        assert saved.status_code == 200 and saved.json()["xyz_current"] is True
        assert saved.json()["xyz_measured"] == [True, False]
        item = client.get("/api/state").json()["items"][0]
        assert item["xyz"] == mine and item["xyz_current"] is True
        assert "Raised OCR accuracy to 99.9% for 3,500 contracts." in client.get("/api/resume").text
        payload = {key: item.get(key, "") for key in ("kind", "title", "organization", "start", "end", "original", "enhanced")}
        sent_back = {key: value for key, value in item.items() if key != "id"} | {"confirmed": True}
        assert client.put("/api/items/scdot", json=sent_back).status_code == 200, "Round-tripped XYZ fields are accepted."
        assert db.get_state()["items"][0]["xyz"] == mine, "Saving the entry keeps its XYZ wording."
        client.put("/api/items/scdot", json={**payload, "original": payload["original"] + "\n• Wrote the evaluation plan.", "confirmed": True})
        assert client.get("/api/state").json()["items"][0]["xyz_current"] is False, "Changed wording sets the old XYZ version aside."


def test_write_xyz_needs_a_confirmed_entry(monkeypatch):
    from backend.main import app
    started = []
    monkeypatch.setattr(master_resume, "start_xyz", lambda: started.append(True))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/master-resume/xyz").status_code == 400
        db.mutate_state(lambda state: state["items"].append(dict(ENTRY)))
        response = client.post("/api/master-resume/xyz")
        assert response.status_code == 200 and response.json()["xyz_pending"] == 1
    assert started == [True]
