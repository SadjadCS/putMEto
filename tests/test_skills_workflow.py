"""Imports and review must keep evidenced skills separate from related options."""

import asyncio
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from backend import ai, db, skills
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def candidate(name, *, support="supported", kind="technique", aliases=None):
    return ai.SkillCandidate(name=name, kind=kind, support=support,
                             evidence="Orchestrated scheduled data pipelines using Apache Airflow. Used SQL databases.",
                             rationale="Grounded technique." if support == "supported" else "An adjacent option. Confirm actual knowledge first.",
                             aliases=aliases or [])


def background():
    return {"id": "experience", "kind": "experience", "title": "Engineer", "organization": "Example", "start": "2020", "end": "2022",
            "original": "Orchestrated scheduled data pipelines using Apache Airflow. Used SQL databases.", "enhanced": "Orchestrated scheduled data pipelines using Apache Airflow. Used SQL databases.", "confirmed": True}


def docx():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Example Applicant</w:t></w:r></w:p><w:p><w:r><w:t>Technical Skills</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Databases</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>PostgreSQL, Qdrant</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return stream.getvalue()


def test_docx_full_import_keeps_skills_even_without_experience(client, monkeypatch):
    async def extract(settings, text):
        assert "Technical Skills" in text and "Databases\tPostgreSQL, Qdrant" in text
        return ai.ExtractedResume(profile=ai.ProfileOutput(), items=[], skills=[ai.ExtractedSkill(**candidate("Qdrant", kind="database").model_dump())])
    monkeypatch.setattr(ai, "extract_resume", extract)
    response = client.post("/api/import", files={"file": ("cv.DOCX", docx(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0
    assert response.json()["skill_count"] == 1
    stored = db.get_state()["skills"][0]
    assert stored["origin"] == "resume" and stored["support"] == "supported"
    assert stored["confirmed"] is False
    assert "Qdrant" not in client.get("/api/resume").text


def test_skills_only_docx_import_preserves_entries_and_confirmed_choices(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()], skills=[{"id": "known", "name": "SQL", "confirmed": True}]))
    before = db.get_state()
    async def extract(settings, text):
        assert "PostgreSQL" in text
        return [candidate("SQL"), candidate("Qdrant", kind="database")]
    monkeypatch.setattr(ai, "extract_skills", extract)
    for _ in range(2):
        response = client.post("/api/import/skills", files={"file": ("cv.docx", docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert response.status_code == 200, response.text
    state = db.get_state()
    assert state["items"] == before["items"]
    assert state["profile"] == before["profile"]
    assert state["skills"][0] == before["skills"][0]
    assert len(state["skills"]) == 2
    assert state["skills"][1]["confirmed"] is False
    assert response.json()["count"] == 0


def test_imported_skills_are_valid_suggestion_context_without_confirmed_experience(client, monkeypatch):
    db.mutate_state(lambda state: skills.merge_candidates(state, [candidate("SQL", kind="language")], origin="resume"))
    seen = []
    async def suggest(settings, state, job):
        seen.append(state)
        return [candidate("PostgreSQL", kind="database", support="related")]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    response = client.post("/api/suggestions/skills")
    assert response.status_code == 200, response.text
    assert seen[0]["skills"][0]["origin"] == "resume"
    assert not any(skill["confirmed"] for skill in db.get_state()["skills"])


def test_related_database_does_not_match_or_enter_resume_until_confirmed(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()], jobs=[{
        "id": "job", "title": "Engineer", "description": "Work with Postgres", "url": "https://example.com/jobs/1", "status": "saved",
    }]))
    async def suggest(*args):
        return [candidate("Workflow orchestration"), candidate("PostgreSQL", kind="database", support="related", aliases=["Postgres"])]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    response = client.post("/api/suggestions/skills")
    assert response.status_code == 200
    proposed = response.json()["items"]
    assert all(not item["confirmed"] for item in proposed)
    from backend.jobs import score_job
    state = db.get_state()
    assert score_job(state["jobs"][0], state)[1] == []
    assert "PostgreSQL" not in client.get("/api/resume").text
    related = next(item for item in proposed if item["name"] == "PostgreSQL")
    related["confirmed"] = True
    # Metadata round-trip is optional; the server must preserve it independently.
    minimal = [{key: item[key] for key in ("id", "name", "confirmed")} for item in proposed]
    assert client.put("/api/skills", json={"items": minimal}).status_code == 200
    state = db.get_state()
    stored = next(item for item in state["skills"] if item["name"] == "PostgreSQL")
    assert stored["aliases"] == ["Postgres"] and stored["support"] == "related"
    assert state["jobs"][0]["matched_skills"] == ["PostgreSQL"]
    assert "PostgreSQL" in client.get("/api/resume").text


def test_dismissed_suggestion_does_not_return_or_erase_confirmed_skills(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()], skills=[{"id": "manual", "name": "Python", "confirmed": True}]))
    async def suggest(*args):
        return [candidate("Vector databases", support="related")]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    first = client.post("/api/suggestions/skills").json()
    assert first["count"] == 1
    retained = [item for item in first["items"] if item["confirmed"]]
    assert client.put("/api/skills", json={"items": retained}).status_code == 200
    assert "vector databases" in db.get_state()["dismissed_skills"]
    assert client.post("/api/suggestions/skills").json()["count"] == 0
    assert db.get_state()["skills"] == retained


def test_job_specific_skill_review_passes_context_but_never_auto_confirms(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()], jobs=[{"id": "job", "title": "Engineer", "description": "Qdrant required."}]))
    observed = []
    async def suggest(settings, state, job):
        observed.append(job)
        return [candidate("Qdrant", support="related", kind="database")]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    response = client.post("/api/suggestions/skills", json={"job_id": "job"})
    assert response.status_code == 200
    assert observed[0]["description"] == "Qdrant required."
    assert response.json()["items"][0]["confirmed"] is False
    assert client.post("/api/suggestions/skills", json={"job_id": "absent"}).status_code == 404


def test_stale_skill_suggestions_are_not_saved(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()]))
    async def suggest(*args):
        db.mutate_state(lambda state: state["items"][0].update(confirmed=False))
        return [candidate("Workflow orchestration")]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    assert client.post("/api/suggestions/skills").status_code == 409
    assert db.get_state()["skills"] == []


def test_related_option_cannot_demote_cv_evidence_or_modify_confirmation():
    state = db.default_state()
    skills.merge_candidates(state, [candidate("PostgreSQL", aliases=["Postgres"])], origin="resume")
    original = deepcopy(state["skills"])
    skills.merge_candidates(state, [candidate("POSTGRESQL", support="related")], origin="suggestion")
    assert state["skills"] == original
    state["skills"][0]["confirmed"] = True
    original = deepcopy(state["skills"])
    skills.merge_candidates(state, [candidate("PostgreSQL", kind="database")], origin="resume")
    assert state["skills"] == original


def test_agent_skill_tool_accepts_job_context_and_review_explains_related_options(client, monkeypatch):
    from backend import codex_tools
    db.mutate_state(lambda state: state.update(items=[background()], jobs=[{"id": "job", "title": "Engineer", "description": "Qdrant"}]))
    async def suggest(settings, state, job):
        assert job["id"] == "job"
        return [candidate("Qdrant", support="related", kind="database")]
    monkeypatch.setattr(ai, "suggest_skills", suggest)
    result = asyncio.run(codex_tools.execute_tool("putmeto_suggest_skills", {"job_id": "job"}))
    entry = result["items"][0]
    description = codex_tools.approval_description("putmeto_confirm_suggestions", {"kind": "skills", "items": [{"id": entry["id"], "name": entry["name"]}]})
    assert '"support": "related"' in description
    assert "Confirm actual knowledge first" in description
