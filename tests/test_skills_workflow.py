"""CV imports are accepted while related skill suggestions still need review."""

import asyncio
from copy import deepcopy
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from backend import ai, db, master_resume, skills
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


CV_LINES = ["Example Applicant", "Technical Skills", "Databases: PostgreSQL, Qdrant"]


def cv():
    stream = BytesIO()
    document = canvas.Canvas(stream)
    for index, line in enumerate(CV_LINES):
        document.drawString(40, 760 - 20 * index, line)
    document.save()
    return stream.getvalue()


@pytest.fixture(autouse=True)
def read_pages(monkeypatch):
    """The AI's reading of the uploaded PDF's pages; the background master resume is not built here."""
    pages = []

    async def transcribe(settings, images):
        pages.append(images)
        return list(CV_LINES)

    async def same_model(settings):
        return settings, None

    monkeypatch.setattr(ai, "transcribe_pdf", transcribe)
    monkeypatch.setattr(ai, "strongest", same_model)
    monkeypatch.setattr(master_resume, "start", lambda lines: None)
    return pages


def test_full_import_keeps_skills_even_without_experience(client, monkeypatch, read_pages):
    async def extract(settings, lines):
        assert lines == CV_LINES
        return ai.ImportedResume(skills=[ai.ExtractedSkill(**candidate("Qdrant", kind="database").model_dump(), source_section="skills")])
    monkeypatch.setattr(ai, "extract_resume", extract)
    response = client.post("/api/import", files={"file": ("CV.PDF", cv(), "application/octet-stream")})
    assert len(read_pages) == 1 and len(read_pages[0]) == 1
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0
    assert response.json()["skill_count"] == 1
    stored = db.get_state()["skills"][0]
    assert stored["origin"] == "resume" and stored["support"] == "supported"
    assert stored["source_section"] == "skills"
    assert stored["confirmed"] is True
    assert "Qdrant" in client.get("/api/resume").text


@pytest.mark.parametrize("include_metadata", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
def test_cv_skill_section_survives_optional_review_roundtrip(client, include_metadata, confirmed):
    imported = ai.ExtractedSkill(**candidate("SQL", kind="language").model_dump(), source_section="skills")
    db.mutate_state(lambda state: skills.merge_candidates(state, [imported], origin="resume"))
    entry = db.get_state()["skills"][0]
    assert entry["confirmed"] is True
    entry["confirmed"] = confirmed
    if not include_metadata:
        entry = {key: entry[key] for key in ("id", "name", "confirmed")}
    response = client.put("/api/skills", json={"items": [entry]})
    assert response.status_code == 200, response.text
    stored = db.get_state()["skills"][0]
    assert stored["source_section"] == "skills"
    assert stored["origin"] == "resume"
    assert stored["confirmed"] is confirmed
    assert ("SQL" in client.get("/api/resume").text) is confirmed


def test_review_cannot_forge_cv_skill_section_provenance(client):
    db.mutate_state(lambda state: skills.merge_candidates(state, [candidate("SQL", support="related")], origin="suggestion"))
    entry = db.get_state()["skills"][0]
    entry.update(origin="resume", support="supported", source_section="skills", confirmed=True)
    response = client.put("/api/skills", json={"items": [entry, {
        "name": "Python", "confirmed": False, "origin": "resume", "source_section": "skills",
    }]})
    assert response.status_code == 200, response.text
    stored, manual = db.get_state()["skills"]
    assert stored["origin"] == "suggestion" and stored["support"] == "related"
    assert stored.get("source_section") is None
    assert stored["confirmed"] is True
    assert manual["origin"] == "manual" and manual.get("source_section") is None


def test_skills_only_import_preserves_entries_and_confirmed_choices(client, monkeypatch):
    db.mutate_state(lambda state: state.update(items=[background()], skills=[{"id": "known", "name": "SQL", "confirmed": True}]))
    before = db.get_state()
    async def extract(settings, lines):
        assert lines == CV_LINES
        return [candidate("SQL"), candidate("Qdrant", kind="database")]
    monkeypatch.setattr(ai, "extract_skills", extract)
    for _ in range(2):
        response = client.post("/api/import/skills", files={"file": ("cv.pdf", cv(), "application/pdf")})
        assert response.status_code == 200, response.text
    state = db.get_state()
    assert state["items"] == before["items"]
    assert state["profile"] == before["profile"]
    assert state["skills"][0] == before["skills"][0]
    assert len(state["skills"]) == 2
    assert state["skills"][1]["confirmed"] is True
    assert "Qdrant" in client.get("/api/resume").text
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
    stored = {skill["name"]: skill for skill in db.get_state()["skills"]}
    assert stored["SQL"]["confirmed"] is True
    assert stored["PostgreSQL"]["confirmed"] is False


def test_reimporting_accepted_skills_preserves_prepared_resume_and_user_choices(client, monkeypatch):
    accepted = {"id": "known", "name": "PostgreSQL", "confirmed": True,
                "origin": "manual", "support": "supported", "aliases": ["Postgres"],
                "evidence": "Added by you.", "rationale": "", "kind": "database"}
    prepared_job = {"id": "job", "title": "Engineer", "description": "Work with Postgres",
                    "status": "prepared", "match_score": 30, "matched_skills": ["PostgreSQL"],
                    "resume": {"profile": {}, "items": [], "skills": ["PostgreSQL"]}}
    db.mutate_state(lambda state: state.update(skills=[accepted], jobs=[prepared_job]))

    async def extract(*args):
        return [candidate("POSTGRESQL", kind="database")]

    monkeypatch.setattr(ai, "extract_skills", extract)
    response = client.post("/api/import/skills", files={"file": ("cv.pdf", cv(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == response.json()["updated"] == 0
    assert db.get_state()["skills"] == [accepted]
    assert db.get_state()["jobs"] == [prepared_job]


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


@pytest.mark.parametrize("other_section", ["experience", "other", None])
def test_reimport_retains_strongest_cv_section_provenance(other_section):
    state = db.default_state()
    weaker = ai.ExtractedSkill(**candidate("SQL").model_dump(), source_section=other_section)
    explicit = ai.ExtractedSkill(**{
        **candidate("SQL").model_dump(), "source_section": "skills", "evidence": "Technical Skills: SQL",
    })
    skills.merge_candidates(state, [weaker], origin="resume")
    # A saved draft from the old import workflow should be accepted on reimport.
    state["skills"][0]["confirmed"] = False
    original_id = state["skills"][0]["id"]
    assert skills.merge_candidates(state, [explicit], origin="resume") == (0, 1)
    stored = deepcopy(state["skills"][0])
    assert stored["source_section"] == "skills" and stored["evidence"] == "Technical Skills: SQL"
    assert stored["id"] == original_id and stored["confirmed"] is True
    assert skills.merge_candidates(state, [weaker], origin="resume") == (0, 0)
    assert state["skills"][0] == stored


@pytest.mark.parametrize("other_section", ["experience", "other", None])
def test_reimport_accepts_legacy_draft_without_losing_stronger_section_evidence(other_section):
    state = db.default_state()
    explicit = ai.ExtractedSkill(**{
        **candidate("SQL").model_dump(), "source_section": "skills", "evidence": "Technical Skills: SQL",
    })
    skills.merge_candidates(state, [explicit], origin="resume")
    state["skills"][0]["confirmed"] = False
    previous = deepcopy(state["skills"][0])
    weaker = ai.ExtractedSkill(**candidate("SQL").model_dump(), source_section=other_section)

    assert skills.merge_candidates(state, [weaker], origin="resume") == (0, 1)
    assert state["skills"] == [{**previous, "confirmed": True}]


@pytest.mark.parametrize("endpoint", ["/api/import", "/api/import/skills"])
@pytest.mark.parametrize("existing_draft", [True, False])
def test_imported_skills_refresh_matches_and_invalidate_prepared_resumes(client, monkeypatch, endpoint, existing_draft):
    imported_skill = ai.ExtractedSkill(**{
        **candidate("PostgreSQL", kind="database", aliases=["Postgres"]).model_dump(),
        "source_section": "skills", "evidence": "Technical Skills: PostgreSQL",
    })
    draft = {**candidate("PostgreSQL", kind="database", support="related").model_dump(),
             "id": "existing-draft", "origin": "suggestion", "confirmed": False}
    db.mutate_state(lambda state: state.update(
        items=[background()], skills=[draft] if existing_draft else [],
        jobs=[{"id": "job", "title": "Engineer", "description": "Work with Postgres",
               "status": "prepared", "match_score": 0, "matched_skills": [],
               "resume": {"profile": state["profile"], "items": [background()], "skills": []}}],
    ))

    async def extract_skills(*args):
        return [imported_skill]

    async def extract_resume(*args):
        return ai.ImportedResume(skills=[imported_skill])

    monkeypatch.setattr(ai, "extract_skills", extract_skills)
    monkeypatch.setattr(ai, "extract_resume", extract_resume)
    response = client.post(endpoint, files={"file": ("cv.pdf", cv(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["skill_count"] == (0 if existing_draft else 1)
    state = client.get("/api/state").json()
    assert len(state["skills"]) == 1
    stored = state["skills"][0]
    assert stored["confirmed"] is True
    assert stored["origin"] == "resume" and stored["support"] == "supported"
    assert stored["source_section"] == "skills" and stored["evidence"] == imported_skill.evidence
    assert stored["aliases"] == ["Postgres"]
    if existing_draft:
        assert stored["id"] == draft["id"]
    assert state["jobs"][0]["matched_skills"] == ["PostgreSQL"]
    assert state["jobs"][0]["match_score"] == 30
    assert state["jobs"][0]["status"] == "saved"
    assert "resume" not in state["jobs"][0]
    assert client.get("/api/jobs/job/resume").status_code == 400
    assert "PostgreSQL" in client.get("/api/resume").text


def test_accepted_imported_skill_can_be_renamed_or_removed(client):
    db.mutate_state(lambda state: skills.merge_candidates(state, [candidate("SQL")], origin="resume"))
    imported = db.get_state()["skills"][0]
    assert imported["confirmed"] is True
    response = client.put("/api/skills", json={"items": [{
        "id": imported["id"], "name": "Data analysis", "confirmed": True,
    }]})
    assert response.status_code == 200, response.text
    renamed = response.json()["items"][0]
    assert renamed["confirmed"] is True and renamed["origin"] == "manual"
    assert "Data analysis" in client.get("/api/resume").text
    assert "SQL" not in client.get("/api/resume").text
    assert client.put("/api/skills", json={"items": []}).status_code == 200
    assert db.get_state()["skills"] == []
    assert "Data analysis" not in client.get("/api/resume").text


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
