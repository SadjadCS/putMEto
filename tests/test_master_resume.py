"""The master resume uses Codex's deepest reasoning but only the CV's own words."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from backend import ai, db, master_resume
from backend.codex_bridge import CodexBridge
from backend.resumes import build_resume, render_html, render_pdf


CV = [
    "Sam Example",
    "Experience",
    "AI Specialist | Clemson University Jul. 2025 – Present",
    "Sole engineer delivering agentic AI systems for partners.",
    "SCDOT AI Contract Assistant (South Carolina Department of Transportation)",
    "• Built an agentic system grounded in **3,500 historical contracts**.",
    "AI Research & Projects",
    "TigerAI | Clemson University Jun. 2024 – Apr. 2026",
    "• Built a no-code agentic platform.",
    "Skills",
    "Languages: Python, SQL",
]
MASTER = {
    "profile": {"name": "Sam Example", "headline": "Invented headline"},
    "items": [
        {"kind": "experience", "title": "AI Specialist", "organization": "Clemson University", "start": "Jul. 2025", "end": "Present",
         "description_lines": [4, 5, 6]},
        {"kind": "project", "title": "TigerAI", "organization": "Clemson University", "start": "Jun. 2024", "end": "Apr. 2026",
         "description_lines": [9]},
    ],
    "skills": [{"name": "Python", "kind": "language", "evidence": "Languages: Python, SQL", "rationale": "Skills section.",
                "source_section": "skills", "category": "Languages"}],
    "section_titles": {"experience": "Experience", "project": "AI Research & Projects", "skills": "Skills", "education": "Education"},
}


def models(*entries):
    return {"data": [{"id": name, "model": name, "isDefault": default,
                      "supportedReasoningEfforts": [{"reasoningEffort": level, "description": ""} for level in levels]}
                     for name, default, levels in entries]}


@pytest.mark.parametrize("listed,expected", [
    (models(("gpt-6-astra", True, ["low", "medium", "high", "xhigh", "max", "ultra"]),
            ("gpt-6-sol", False, ["low", "ultra"]), ("gpt-5.5", False, ["low", "xhigh"])), ("gpt-6-astra", "ultra")),
    (models(("everyday", True, ["low", "high"]), ("deep", False, ["low", "xhigh"])), ("deep", "xhigh")),
    (models(("unknown", True, ["turbo"])), (None, "high")),
])
def test_strongest_model_is_the_deepest_reasoning_level_preferring_the_default(monkeypatch, listed, expected):
    bridge = CodexBridge()
    monkeypatch.setattr(bridge, "_require_account", AsyncMock())
    monkeypatch.setattr(bridge, "_rpc", AsyncMock(return_value=listed))
    assert asyncio.run(bridge.strongest_model()) == expected


def test_deep_reasoning_is_requested_explicitly_with_a_longer_wait(monkeypatch):
    async def exercise():
        bridge, turns, timeouts = CodexBridge(), [], []
        original_wait_for = asyncio.wait_for

        async def rpc(method, params, **kwargs):
            if method == "thread/start":
                return {"thread": {"id": "master"}}
            if method == "turn/start":
                turns.append(params)
                bridge._notification("item/completed", {"threadId": "master", "item": {"type": "agentMessage", "id": "a", "text": "{}"}})
                bridge._notification("turn/completed", {"threadId": "master", "turn": {"status": "completed"}})
                return {"turn": {"id": "turn"}}
            return {}

        async def record(awaitable, timeout):
            timeouts.append(timeout)
            return await original_wait_for(awaitable, timeout)

        monkeypatch.setattr(bridge, "_require_account", AsyncMock())
        monkeypatch.setattr(bridge, "_rpc", rpc)
        monkeypatch.setattr(asyncio, "wait_for", record)
        await bridge.generate_json("Build.", {}, {"type": "object", "properties": {}}, model="gpt-6-astra", effort="ultra", timeout=2700)
        return turns[0], timeouts

    turn, timeouts = asyncio.run(exercise())
    assert (turn["model"], turn["effort"]) == ("gpt-6-astra", "ultra")
    assert timeouts == [2700]


def test_master_resume_is_organized_by_the_model_but_worded_only_from_the_cv(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(output_type=output_type, effort=effort, timeout=timeout, instructions=instructions)
        return output_type.model_validate(MASTER)

    monkeypatch.setattr(ai, "generate", generate)
    result = asyncio.run(ai.build_master_resume({}, CV, effort="ultra", timeout=900))
    assert (seen["output_type"], seen["effort"], seen["timeout"]) == (ai.MasterResume, "ultra", 900)
    assert "keep them inside that job" in seen["instructions"]
    job, project = result.items
    assert job.original == ("Sole engineer delivering agentic AI systems for partners.\n"
                            "SCDOT AI Contract Assistant (South Carolina Department of Transportation)\n"
                            "• Built an agentic system grounded in **3,500 historical contracts**.")
    assert result.profile.headline == "", "Nothing the CV doesn't say is kept."
    assert result.section_titles == {"experience": "Experience", "project": "AI Research & Projects", "skills": "Skills"}
    assert [(skill.name, skill.category) for skill in result.skills] == [("Python", "Languages")]


def assembled():
    return ai._assemble(ai.MasterResume.model_validate(MASTER), CV)


@pytest.fixture
def workspace():
    db.initialize()
    db.mutate_state(lambda state: state.update(items=[
        {"id": "quick", "kind": "project", "title": "SCDOT AI Contract Assistant", "original": "Quick reading.", "enhanced": "", "confirmed": True, "source": "cv"},
        {"id": "legacy", "kind": "experience", "title": "Imported before sources were recorded", "original": "", "enhanced": "", "confirmed": True},
        {"id": "mine", "kind": "experience", "title": "Volunteer mentor", "original": "Mentored students.", "enhanced": "", "confirmed": True, "source": "manual"},
    ], cv_transcript={"lines": CV, "uploaded_at": "2026-09-30T00:00:00+00:00"}))


def run_build(monkeypatch, edit=None):
    async def strongest(settings):
        return {**settings, "model": "gpt-6-astra"}, "ultra"

    async def build(settings, lines, effort=None, timeout=None):
        assert (settings["model"], effort, lines) == ("gpt-6-astra", "ultra", CV)
        if edit:
            db.mutate_state(edit)
        return assembled()

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "build_master_resume", build)

    async def exercise():
        master_resume.start(CV)
        await master_resume._task
    asyncio.run(exercise())


def test_master_resume_replaces_cv_entries_and_keeps_yours(workspace, monkeypatch):
    run_build(monkeypatch)
    state = db.get_state()
    assert [item["title"] for item in state["items"]] == ["AI Specialist", "TigerAI", "Volunteer mentor"]
    assert all(item["source"] == "cv" and item["confirmed"] and item["enhanced"] == "" for item in state["items"][:2])
    assert state["section_titles"]["project"] == "AI Research & Projects"
    assert state["master_resume"]["model"] == "gpt-6-astra" and state["master_resume"]["effort"] == "ultra"
    assert master_resume.status()["state"] == "done" and "2 entries" in master_resume.status()["message"]
    assert [skill["category"] for skill in state["skills"]] == ["Languages"]


def test_edits_made_while_building_are_never_overwritten(workspace, monkeypatch):
    run_build(monkeypatch, edit=lambda state: state["items"][0].update(original="My careful edit."))
    state = db.get_state()
    assert [item["id"] for item in state["items"]] == ["quick", "legacy", "mine"]
    assert state["items"][0]["original"] == "My careful edit."
    assert master_resume.status()["state"] == "failed" and "Rebuild" in master_resume.status()["message"]


def pdf(*lines):
    stream = BytesIO()
    page = canvas.Canvas(stream)
    for index, line in enumerate(lines):
        page.drawString(40, 760 - 20 * index, line)
    page.save()
    return stream.getvalue()


def test_uploading_with_codex_reads_with_the_strongest_model_and_starts_the_master_resume(monkeypatch):
    from backend.main import app
    readers, started = [], []

    async def strongest(settings):
        return {**settings, "model": "gpt-6-astra"}, "ultra"

    async def transcribe(settings, pages):
        readers.append(settings["model"])
        return list(CV)

    async def quick(settings, lines):
        return ai._assemble(ai.ExtractedResume.model_validate({key: MASTER[key] for key in ("profile", "items", "skills")}), lines)

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "transcribe_pdf", transcribe)
    monkeypatch.setattr(ai, "extract_resume", quick)
    monkeypatch.setattr(master_resume, "start", started.append)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/api/import", files={"file": ("cv.pdf", pdf("Sam Example"), "application/pdf")})
        assert response.status_code == 200, response.text
        assert response.json()["master_resume"] is True and "deepest reasoning" in response.json()["message"]
        assert "cv_transcript" not in client.get("/api/state").json()
    assert readers == ["gpt-6-astra"]
    assert started == [CV]
    state = db.get_state()
    assert state["cv_transcript"]["lines"] == CV
    assert {item["source"] for item in state["items"]} == {"cv"}


def test_rebuild_uses_the_saved_cv_and_entries_you_add_or_edit_stay_yours(monkeypatch):
    from backend.main import app
    started = []
    monkeypatch.setattr(master_resume, "start", started.append)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/master-resume/rebuild").status_code == 422
        db.mutate_state(lambda state: state.update(cv_transcript={"lines": CV, "uploaded_at": "2026-09-30T00:00:00+00:00"}))
        assert client.post("/api/master-resume/rebuild").status_code == 200
        assert started == [CV]
        item = client.post("/api/items", json={"kind": "experience", "title": "Mentor", "original": ""}).json()
        assert item["source"] == "manual"
        payload = {key: value for key, value in item.items() if key not in {"id"}} | {"confirmed": True, "source": "cv"}
        assert client.put(f'/api/items/{item["id"]}', json=payload).status_code == 200
    assert db.get_state()["items"][0]["source"] == "manual", "A sent-back source never changes who an entry belongs to."


def test_resume_uses_the_cvs_own_section_titles_when_an_ats_recognizes_them():
    state = {"profile": {"name": "Sam Example"}, "skills": [], "items": [
        {"kind": "project", "title": "TigerAI", "original": "• Built a no-code agentic platform.", "confirmed": True},
        {"kind": "experience", "title": "AI Specialist", "original": "• Built agents.", "confirmed": True}],
        "section_titles": {"project": "AI Research & Projects", "experience": "Professional Experience"}}
    resume = build_resume(state)
    html = render_html(resume)
    assert "<h2>Professional Experience</h2>" in html, "A standard title from the CV is kept."
    assert "<h2>Projects</h2>" in html and "AI Research" not in html, "An unusual one is replaced with the standard name."
    text = PdfReader(BytesIO(render_pdf(resume))).pages[0].extract_text()
    assert "PROJECTS" in text and "AI RESEARCH" not in text


def suggestion(title, family="AI Engineering", fit="strong"):
    return ai.RoleSuggestion(title=title, family=family, fit=fit, reason=f"The master resume shows {title} work.")


def test_suggested_roles_merge_without_losing_your_choices():
    from backend.roles import merge_roles
    state = {"positions": [
        {"id": "kept", "name": "LLM Engineer", "confirmed": True},
        {"id": "refresh", "name": "AI Architect", "confirmed": False, "origin": "suggestion", "family": "Old", "fit": "possible", "reason": "Old."},
        {"id": "stale", "name": "Data Analyst", "confirmed": False, "origin": "suggestion"},
        {"id": "checked", "name": "ML Engineer", "confirmed": True, "origin": "suggestion"},
    ], "dismissed_positions": ["cashier"]}
    added = merge_roles(state, [suggestion("ai architect"), suggestion("Head of AI", "Leadership"), suggestion("Cashier", "Retail"),
                                suggestion("Head of AI", "Leadership")])
    assert added == 1
    names = [item["name"] for item in state["positions"]]
    assert names == ["LLM Engineer", "AI Architect", "ML Engineer", "Head of AI"], "Stale unchecked suggestions go; declined ones never return."
    refreshed = state["positions"][1]
    assert (refreshed["family"], refreshed["fit"], refreshed["id"]) == ("AI Engineering", "strong", "refresh")
    assert state["positions"][2]["confirmed"] is True, "A checked suggestion stays even when it is no longer suggested."
    assert state["positions"][3]["origin"] == "suggestion" and state["positions"][3]["confirmed"] is False


def test_saving_roles_keeps_suggestion_details_and_remembers_declined_ones():
    from backend.main import app
    from backend.roles import merge_roles
    db.initialize()
    db.mutate_state(lambda state: merge_roles(state, [suggestion("AI Architect"), suggestion("Head of AI", "Leadership", "possible")]))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        architect, _ = client.get("/api/state").json()["positions"]
        response = client.put("/api/positions", json={"items": [{**architect, "confirmed": True}, {"name": "Research Engineer", "confirmed": False}]})
        assert response.status_code == 200, response.text
    saved = {item["name"]: item for item in db.get_state()["positions"]}
    assert saved["AI Architect"]["family"] == "AI Engineering" and saved["AI Architect"]["confirmed"] is True
    assert saved["Research Engineer"]["origin"] == "manual"
    assert db.get_state()["dismissed_positions"] == ["head of ai"]


def test_after_the_master_resume_the_same_model_lists_every_job_title(workspace, monkeypatch):
    listed = []

    async def suggest_roles(settings, state, effort=None, timeout=None):
        listed.append((settings["model"], effort, [item["title"] for item in state["items"] if item.get("source") == "cv"]))
        return [suggestion("AI Engineer"), suggestion("Head of AI", "Leadership", "possible")]

    monkeypatch.setattr(ai, "suggest_roles", suggest_roles)
    run_build(monkeypatch)
    assert listed == [("gpt-6-astra", "ultra", ["AI Specialist", "TigerAI"])], "Roles come from the finished master resume."
    assert [item["name"] for item in db.get_state()["positions"]] == ["AI Engineer", "Head of AI"]
    assert "Found 2 job titles" in master_resume.status()["message"]


def test_a_failed_role_listing_keeps_the_master_resume(workspace, monkeypatch):
    monkeypatch.setattr(ai, "suggest_roles", AsyncMock(side_effect=ai.AIError("busy")))
    run_build(monkeypatch)
    assert master_resume.status()["state"] == "done"
    assert "Suggest roles" in master_resume.status()["message"]
    assert [item["title"] for item in db.get_state()["items"]][:2] == ["AI Specialist", "TigerAI"]


def test_suggest_roles_runs_in_the_background_from_the_master_resume(monkeypatch):
    from backend.main import app
    started = []
    monkeypatch.setattr(master_resume, "start_roles", lambda: started.append(True))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/master-resume/roles").status_code == 400
        db.mutate_state(lambda state: state["items"].append({"id": "a", "kind": "experience", "title": "AI Specialist", "confirmed": True}))
        assert client.post("/api/master-resume/roles").status_code == 200
    assert started == [True]
