"""Offline integration tests for the local CV review and resume workflow."""

import io
import json
from collections import deque

import httpx
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from backend import db


ORIGIN = "http://127.0.0.1:8000"
ITEM = {
    "kind": "experience",
    "title": "Software engineer",
    "organization": "Example Company",
    "start": "2021",
    "end": "2024",
    "original": "Built Python APIs and wrote SQL queries.",
}
# A CV and the AI's structured reading of it: descriptions are line numbers.
CV_LINES = ["Ada Applicant", "ada@example.com", "Software engineer, Example Company, 2021 - 2024",
            "Built Python APIs and wrote SQL queries."]
EXTRACTED_ITEM = {key: ITEM[key] for key in ("kind", "title", "organization", "start", "end")} | {"description_lines": [4]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.main import app

    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "workspace")
    with TestClient(app, base_url=ORIGIN) as session:
        yield session


@pytest.fixture
def ollama_capabilities():
    """What the mocked Ollama server reports for the model; without "vision" it cannot read PDF pages."""
    return ["completion", "vision"]


@pytest.fixture
def model(client, monkeypatch, ollama_capabilities):
    """Exercise the AI HTTP/schema layer without permitting real connections."""
    response = client.put("/api/settings", json={
        "provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "llama3.2", "api_key": "",
    })
    assert response.status_code == 200, response.text
    responses = deque()
    requests = []
    original_client = httpx.AsyncClient

    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ollama_capabilities})
        requests.append({"url": str(request.url), "body": json.loads(request.content), "headers": dict(request.headers)})
        assert responses, "Unexpected AI/network request: tests must supply every response."
        output = responses.popleft()
        if isinstance(output, Exception):
            raise output
        content = output if isinstance(output, str) else json.dumps(output)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(200, json={"message": {"content": content}})

    def mocked_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    return responses, requests


def create_item(client, model, **overrides):
    responses, _ = model
    responses.append({"enhanced": "Developed backend services using Python and relational database queries."})
    response = client.post("/api/items", json={**ITEM, **overrides})
    assert response.status_code in (200, 201), response.text
    return response.json()


def confirm_item(client, item, **overrides):
    payload = {key: value for key, value in item.items() if key not in ("id", "warning")}
    response = client.put(f'/api/items/{item["id"]}', json={**payload, "confirmed": True, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def pdf_with_text(*lines):
    stream = io.BytesIO()
    document = canvas.Canvas(stream)
    for index, line in enumerate(lines):
        document.drawString(40, 760 - 20 * index, line)
    document.save()
    return stream.getvalue()


def import_cv(client, model, extraction, transcript=CV_LINES, filename="cv.pdf", mime="application/pdf"):
    """Upload a PDF CV; the mocked AI first reads its pages, then structures what it read."""
    model[0].extend([{"lines": transcript}, extraction])
    return client.post("/api/import", files={"file": (filename, pdf_with_text(*transcript), mime)})


@pytest.mark.parametrize("kind", ["experience", "project", "education", "publication"])
def test_pdf_import_reads_the_pages_and_copies_descriptions_from_them(client, model, kind):
    response = import_cv(client, model, {
        "profile": {"name": "Ada Applicant", "email": "ada@example.com", "headline": "Invented headline"},
        "items": [{**EXTRACTED_ITEM, "kind": kind}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    reading, structuring = (request["body"]["messages"][1] for request in model[1])
    assert len(reading["images"]) == 1 and reading["images"][0].startswith("iVBOR"), "Each page is sent as a PNG image."
    assert json.loads(reading["content"]) == {"page_count": 1}
    assert "images" not in structuring
    assert [line["text"] for line in json.loads(structuring["content"])["cv_lines"]] == CV_LINES
    state = client.get("/api/state").json()
    assert state["profile"]["name"] == "Ada Applicant"
    assert state["profile"]["headline"] == "", "Fields the CV does not contain are never saved."
    item = state["items"][0]
    assert {key: item[key] for key in ("kind", "title", "organization", "start", "end")} == {**{key: ITEM[key] for key in ("title", "organization", "start", "end")}, "kind": kind}
    assert item["original"] == "Built Python APIs and wrote SQL queries."
    assert item["enhanced"] == ""
    assert item["confirmed"] is True
    assert item["original"] in client.get("/api/resume").text
    exported = client.get("/api/resume.pdf")
    assert exported.status_code == 200, exported.text
    document_text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(exported.content)).pages)
    assert item["original"] in document_text


def test_description_lines_are_copied_in_cv_order_from_the_transcript(client, model):
    transcript = ["Ada Applicant", "Software engineer, Example Company, 2021 - 2024", "Built Python APIs and wrote SQL queries.",
                  "Designed the reporting service."]
    model[0].append({"lines": [transcript[0], "  " + transcript[1], *transcript[2:], ""]})
    model[0].append({"profile": {"name": "Ada Applicant"}, "items": [{**EXTRACTED_ITEM, "description_lines": [4, 3, 3, 99]}]})
    response = client.post("/api/import", files={"file": ("cv.pdf", pdf_with_text("Ada Applicant"), "application/pdf")})
    assert response.status_code == 200, response.text
    structuring = model[1][1]["body"]["messages"][1]
    assert [line["text"] for line in json.loads(structuring["content"])["cv_lines"]] == transcript
    item = client.get("/api/state").json()["items"][0]
    assert item["original"] == "Built Python APIs and wrote SQL queries.\nDesigned the reporting service."


def test_scanned_pdf_is_read_like_any_other_page(client, model):
    document = io.BytesIO()
    page = canvas.Canvas(document)
    page.rect(40, 700, 200, 60, fill=1)  # Visible content without any text layer, like a scan.
    page.save()
    model[0].extend([{"lines": CV_LINES}, {"profile": {"name": "Ada Applicant"}, "items": [EXTRACTED_ITEM]}])
    response = client.post("/api/import", files={"file": ("scan.pdf", document.getvalue(), "application/pdf")})
    assert response.status_code == 200, response.text
    assert db.get_state()["items"][0]["original"] == "Built Python APIs and wrote SQL queries."


def test_model_that_cannot_read_images_gets_an_actionable_error(client, model, ollama_capabilities):
    ollama_capabilities.remove("vision")
    response = client.post("/api/import", files={"file": ("cv.pdf", pdf_with_text(*CV_LINES), "application/pdf")})
    assert response.status_code == 422
    assert "cannot read PDF pages" in response.json()["detail"]
    assert not model[1], "No CV content is sent to a model that cannot read it."
    assert not db.get_state()["items"]


@pytest.mark.parametrize("filename,content,mime", [
    ("cv.docx", b"PK\x03\x04synthetic Word file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("cv.txt", b"Ada Applicant built Python APIs and SQL queries.", "text/plain"),
])
def test_only_pdf_cvs_are_accepted(client, model, filename, content, mime):
    response = client.post("/api/import", files={"file": (filename, content, mime)})
    assert response.status_code == 415
    assert "as a PDF" in response.json()["detail"]
    assert not model[1]


def test_reimporting_a_cv_refreshes_its_entries_instead_of_duplicating_them(client, model):
    manual = create_item(client, model, title="Volunteer mentor", organization="Code Club", original="Mentored students.")
    extraction = {"profile": {"name": "Ada Applicant"}, "items": [EXTRACTED_ITEM]}
    assert import_cv(client, model, extraction).status_code == 200
    imported = next(item for item in db.get_state()["items"] if item["title"] == ITEM["title"])
    db.mutate_state(lambda state: next(item for item in state["items"] if item["id"] == imported["id"]).update(enhanced="An AI rewording."))

    updated_cv = [*CV_LINES, "Led the migration to PostgreSQL."]
    response = import_cv(client, model, {**extraction, "items": [
        {**EXTRACTED_ITEM, "description_lines": [4, 5]},
        {"kind": "project", "title": "Led the migration to PostgreSQL.", "description_lines": []},
    ]}, transcript=updated_cv)
    assert response.status_code == 200, response.text
    assert (response.json()["added"], response.json()["updated"]) == (1, 1)
    items = db.get_state()["items"]
    assert [item["title"] for item in items] == ["Volunteer mentor", ITEM["title"], "Led the migration to PostgreSQL."]
    refreshed = items[1]
    assert refreshed["id"] == imported["id"]
    assert refreshed["original"] == "Built Python APIs and wrote SQL queries.\nLed the migration to PostgreSQL."
    assert refreshed["enhanced"] == "" and refreshed["confirmed"] is True
    assert items[0] == {key: value for key, value in manual.items() if key != "warning"}
    assert items[2]["original"] == ""


def test_entries_can_be_saved_without_a_description_and_without_an_ai_call(client, model):
    response = client.post("/api/items", json={**ITEM, "original": ""})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["original"] == item["enhanced"] == ""
    confirmed = confirm_item(client, item, title="Senior software engineer")
    assert confirmed["enhanced"] == "" and confirmed["confirmed"] is False
    assert client.post(f'/api/items/{item["id"]}/enhance').status_code == 400
    assert not model[1]


def test_imported_entry_can_be_edited_or_excluded_without_another_ai_call(client, model):
    imported = import_cv(client, model, {"profile": {"name": "Ada Applicant"}, "items": [EXTRACTED_ITEM]})
    assert imported.status_code == 200, imported.text
    item = client.get("/api/state").json()["items"][0]
    assert item["confirmed"] is True
    assert item["original"] in client.get("/api/resume").text
    edited = confirm_item(client, item, enhanced="Maintained the Python API for internal reports.")
    assert edited["confirmed"] is True
    preview = client.get("/api/resume").text
    assert edited["enhanced"] in preview
    assert item["original"] not in preview

    excluded = confirm_item(client, edited, confirmed=False)
    assert excluded["confirmed"] is False
    assert edited["enhanced"] not in client.get("/api/resume").text
    assert len(model[1]) == 2, "Only the import itself used the AI: reading the pages, then structuring them."


@pytest.mark.parametrize("filename,mime", [
    ("Resume", "application/octet-stream"),
    ("Resume.PDF ", "application/pdf"),
    ("Resume.pdf (1)", "application/octet-stream"),
    ("Resume.txt", "text/plain"),
])
def test_pdf_contents_are_detected_independently_of_browser_filename(client, model, filename, mime):
    response = import_cv(client, model, {"profile": {}, "items": [EXTRACTED_ITEM]}, filename=filename, mime=mime)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert len(model[1]) == 2
    assert len(client.get("/api/state").json()["items"]) == 1


def test_declared_pdf_without_extension_returns_pdf_error_not_unsupported_type(client, model):
    responses, requests = model
    response = client.post("/api/import", files={"file": ("Resume", b"This is not a valid PDF document.", "application/pdf")})
    assert response.status_code == 422
    assert "PDF could not be read" in response.json()["detail"]
    assert not requests
    assert not client.get("/api/state").json()["items"]


def test_import_ai_timeout_stops_extraction_and_keeps_workspace(client, monkeypatch):
    import asyncio
    from backend import main
    cancelled = []
    async def wait_forever(*args):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(main, "IMPORT_AI_TIMEOUT", 0.01)
    monkeypatch.setattr(main.ai, "strongest", AsyncMock(side_effect=lambda settings: (settings, None)))
    monkeypatch.setattr(main.ai, "transcribe_pdf", wait_forever)
    before = client.get("/api/state").json()
    response = client.post("/api/import", files={"file": ("cv.pdf", pdf_with_text(*CV_LINES), "application/pdf")})
    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]
    assert cancelled == [True]
    assert client.get("/api/state").json() == before


def test_invalid_pdf_has_actionable_error_and_no_state_mutation(client, model):
    before = db.get_state()
    response = client.post("/api/import", files={"file": ("broken.pdf", b"not a PDF", "application/pdf")})
    assert 400 <= response.status_code < 500
    assert "detail" in response.json()
    assert db.get_state() == before
    assert not model[1]


def test_blank_pdf_does_not_send_empty_text_to_ai(client, model):
    response = client.post("/api/import", files={"file": ("scan.pdf", pdf_with_text(""), "application/pdf")})
    assert 400 <= response.status_code < 500
    assert not model[1]
    assert not db.get_state()["items"]


@pytest.mark.parametrize("output", ["this is not JSON", {"profile": {}, "items": [{"kind": "experience", "description_lines": [1]}]}])
def test_malformed_ai_import_does_not_partially_save(client, model, output):
    before = db.get_state()
    response = import_cv(client, model, output)
    assert response.status_code >= 400
    assert "invalid response" in response.json()["detail"].lower()
    assert db.get_state() == before


def test_editing_confirmed_source_creates_new_draft(client, model):
    item = create_item(client, model)
    assert item["confirmed"] is False
    item = confirm_item(client, item, enhanced="Reviewed and corrected backend API description.")
    assert item["confirmed"] is True
    assert item["enhanced"] == "Reviewed and corrected backend API description."
    assert len(model[1]) == 1, "Confirming reviewed text must not replace it with a new model response."
    model[0].append({"enhanced": "Maintained Python APIs and database queries."})
    payload = {key: value for key, value in item.items() if key != "id"}
    response = client.put(f'/api/items/{item["id"]}', json={**payload, "original": "Maintained Python APIs and SQL queries.", "confirmed": True})
    assert response.status_code == 200, response.text
    revised = response.json()
    assert revised["confirmed"] is False
    assert revised["enhanced"] == "Maintained Python APIs and database queries."
    assert "Maintained Python APIs" not in client.get("/api/resume").text


def test_ai_unavailable_keeps_user_input_as_unconfirmed_draft(client, model):
    model[0].append(httpx.ConnectError("offline"))
    response = client.post("/api/items", json=ITEM)
    assert response.status_code in (200, 201), response.text
    item = response.json()
    assert item["original"] == ITEM["original"]
    assert item["enhanced"] == ITEM["original"]
    assert item["confirmed"] is False
    assert response.json().get("warning")
    assert db.get_state()["items"][0]["original"] == ITEM["original"]


def test_rephrasing_retry_preserves_review_on_failure_and_requires_review_on_success(client, model):
    item = confirm_item(client, create_item(client, model), enhanced="Carefully reviewed wording.")
    model[0].append(httpx.ConnectError("offline"))
    failed = client.post(f'/api/items/{item["id"]}/enhance')
    assert failed.status_code == 503
    assert db.get_state()["items"][0] == item
    model[0].append({"enhanced": "New AI draft for the same backend work."})
    retried = client.post(f'/api/items/{item["id"]}/enhance')
    assert retried.status_code == 200, retried.text
    assert retried.json()["enhanced"] == "New AI draft for the same backend work."
    assert retried.json()["original"] == item["original"]
    assert retried.json()["confirmed"] is False


def test_resume_html_and_pdf_include_only_confirmed_items_and_skills(client, model):
    reviewed = confirm_item(client, create_item(client, model), enhanced="Approved backend engineering contribution.")
    create_item(client, model, title="Unreviewed research project", original="Unreviewed claim about research.")
    response = client.put("/api/skills", json={"items": [{"name": "Python", "confirmed": True}, {"name": "Unverified Technology", "confirmed": False}]})
    assert response.status_code == 200, response.text
    response = client.put("/api/profile", json={"name": "Ada <script>alert(1)</script>", "email": "ada@example.com"})
    assert response.status_code == 200, response.text

    preview = client.get("/api/resume")
    assert preview.status_code == 200
    assert reviewed["enhanced"] in preview.text
    assert "Python" in preview.text
    assert "Unreviewed research project" not in preview.text
    assert "Unverified Technology" not in preview.text
    assert "<script>alert(1)</script>" not in preview.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in preview.text

    exported = client.get("/api/resume.pdf")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/pdf"
    document_text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(exported.content)).pages)
    assert reviewed["enhanced"] in document_text
    assert "Python" in document_text
    assert "Unreviewed research project" not in document_text
    assert "Unverified Technology" not in document_text


def test_suggestions_use_confirmed_content_and_preserve_manual_choices(client, model):
    reviewed = confirm_item(client, create_item(client, model))
    draft = create_item(client, model, title="Unreviewed project")
    assert client.put("/api/skills", json={"items": [{"name": "SQL", "confirmed": True}]}).status_code == 200
    model[0].append({"skills": [{"name": name, "evidence": "Built Python APIs and wrote SQL queries.", "rationale": "Supported by the confirmed description."} for name in ["Python", " python ", "SQL"]]})
    response = client.post("/api/suggestions/skills")
    assert response.status_code == 200, response.text
    sent = json.loads(model[1][-1]["body"]["messages"][1]["content"])
    assert [item["id"] for item in sent["confirmed_items"]] == [reviewed["id"]]
    assert draft["id"] not in json.dumps(sent)
    skills = client.get("/api/state").json()["skills"]
    assert len([skill for skill in skills if skill["name"].casefold() == "python"]) == 1
    assert next(skill for skill in skills if skill["name"] == "Python")["confirmed"] is False
    assert next(skill for skill in skills if skill["name"] == "SQL")["confirmed"] is True


def test_settings_hide_key_retain_it_when_blank_and_support_explicit_clear(client):
    config = {"provider": "compatible", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "api_key": "secret-test-key"}
    response = client.put("/api/settings", json=config)
    assert response.status_code == 200, response.text
    assert "secret-test-key" not in response.text
    state = client.get("/api/state")
    assert "secret-test-key" not in state.text
    assert "api_key" not in state.json()["settings"]
    assert state.json()["settings"]["api_key_set"] is True
    assert db.get_state()["settings"]["api_key"] == "secret-test-key"
    assert client.put("/api/settings", json={**config, "api_key": ""}).status_code == 200
    assert db.get_state()["settings"]["api_key"] == "secret-test-key"
    assert client.put("/api/settings", json={**config, "api_key": "", "clear_api_key": True}).status_code == 200
    assert db.get_state()["settings"]["api_key"] == ""
    assert client.get("/api/state").json()["settings"]["api_key_set"] is False


def test_configured_compatible_provider_uses_exact_endpoint_and_key(client, model):
    response = client.put("/api/settings", json={"provider": "compatible", "base_url": "http://127.0.0.1:1234/v1/", "model": "local-model", "api_key": "test-key"})
    assert response.status_code == 200, response.text
    model[0].append({"ok": True})
    response = client.post("/api/settings/test")
    assert response.status_code == 200, response.text
    request = model[1][0]
    assert request["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer test-key"
    assert request["body"]["model"] == "local-model"


def test_changing_ai_server_does_not_forward_previous_servers_key(client, model):
    config = {"provider": "compatible", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "api_key": "first-server-key"}
    assert client.put("/api/settings", json=config).status_code == 200
    config.pop("api_key")
    config["base_url"] = "http://127.0.0.1:5678/v1"
    assert client.put("/api/settings", json=config).status_code == 200
    model[0].append({"ok": True})
    assert client.post("/api/settings/test").status_code == 200
    assert "authorization" not in model[1][-1]["headers"]
    assert db.get_state()["settings"]["api_key"] == ""


def test_tailoring_ignores_invented_ids_and_preserves_confirmed_claims(client, model):
    first = confirm_item(client, create_item(client, model), enhanced="Verified API development experience.")
    second = confirm_item(client, create_item(client, model, title="Data engineer"), enhanced="Verified SQL data engineering experience.")
    draft = create_item(client, model, title="Unconfirmed role")
    skills = client.put("/api/skills", json={"items": [
        {"id": "skill-python", "name": "Python", "confirmed": True},
        {"id": "skill-sql", "name": "SQL", "confirmed": True},
        {"id": "skill-draft", "name": "Unconfirmed framework", "confirmed": False},
    ]})
    assert skills.status_code == 200
    db.mutate_state(lambda state: state["jobs"].append({"id": "job-one", "title": "Data engineer", "company": "Example", "description": "SQL and Python", "status": "saved"}))
    model[0].append({"item_ids": ["invented-item", draft["id"], second["id"], second["id"], first["id"]], "skill_ids": ["skill-draft", "skill-sql", "invented-skill"]})
    response = client.post("/api/jobs/job-one/tailor")
    assert response.status_code == 200, response.text
    tailored = response.json()["resume"]
    assert [item["id"] for item in tailored["items"]] == [second["id"], first["id"]]
    assert [item["enhanced"] for item in tailored["items"]] == [second["enhanced"], first["enhanced"]]
    assert tailored["skills"] == ["SQL", "Python"]
    assert client.get("/api/state").json()["jobs"][0]["status"] == "prepared"
    assert client.get("/api/jobs/job-one/resume").status_code == 200
    assert client.get("/api/jobs/job-one/resume.pdf").content.startswith(b"%PDF")

    updated = confirm_item(client, first, confirmed=False)
    assert updated["confirmed"] is False
    job = client.get("/api/state").json()["jobs"][0]
    assert "resume" not in job
    assert job["status"] == "saved"
    assert client.get("/api/jobs/job-one/resume").status_code == 400


def test_profile_edit_invalidates_a_prepared_resume(client, model):
    item = confirm_item(client, create_item(client, model))
    db.mutate_state(lambda state: state["jobs"].append({"id": "job-one", "title": "Developer", "company": "Example", "description": "Python APIs", "status": "saved"}))
    model[0].append({"item_ids": [item["id"]], "skill_ids": []})
    assert client.post("/api/jobs/job-one/tailor").status_code == 200
    assert client.put("/api/profile", json={"name": "Updated Applicant"}).status_code == 200
    assert "resume" not in client.get("/api/state").json()["jobs"][0]
    assert client.get("/api/jobs/job-one/resume.pdf").status_code == 400


def test_import_to_application_confirmation_workflow(client, model, monkeypatch):
    from backend import jobs
    from backend.network import validate_url_shape

    async def public_url_without_dns(url):
        return validate_url_shape(url)

    async def application_browser(session_id, url, profile, pdf):
        assert url == "https://jobs.example.com/python"
        assert profile["name"] == "Ada Applicant"
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        assert "Built Python APIs and wrote SQL queries." in text
        return {"filled_fields": 2, "resume_attached": True}

    monkeypatch.setattr(jobs, "validate_public_url", public_url_without_dns)
    monkeypatch.setattr(jobs, "start_application", application_browser)
    imported = import_cv(client, model, {"profile": {"name": "Ada Applicant"}, "items": [EXTRACTED_ITEM]})
    assert imported.status_code == 200
    item = client.get("/api/state").json()["items"][0]
    assert item["confirmed"] is True
    model[0].append({"skills": [{"name": "Python", "evidence": "Built Python APIs and wrote SQL queries.", "rationale": "A language used in the confirmed experience."}]})
    assert client.post("/api/suggestions/skills").status_code == 200
    skills = client.get("/api/state").json()["skills"]
    skills[0]["confirmed"] = True
    assert client.put("/api/skills", json={"items": skills}).status_code == 200
    assert client.put("/api/preferences", json={"track": "industry", "location": "", "remote_only": True}).status_code == 200
    model[0].append({"roles": [{"title": "Software engineer", "family": "Engineering", "fit": "strong", "reason": "Built backend APIs."}]})
    assert client.post("/api/suggestions/positions").status_code == 200
    positions = client.get("/api/state").json()["positions"]
    positions[0]["confirmed"] = True
    assert client.put("/api/positions", json={"items": positions}).status_code == 200
    response = client.post("/api/jobs", json={"title": "Software engineer", "company": "Example", "location": "Remote", "url": "https://jobs.example.com/python", "description": "Build Python APIs."})
    assert response.status_code in (200, 201), response.text
    job = response.json()
    model[0].append({"item_ids": [item["id"]], "skill_ids": [skills[0]["id"]]})
    assert client.post(f'/api/jobs/{job["id"]}/tailor').status_code == 200
    response = client.post(f'/api/jobs/{job["id"]}/apply')
    assert response.status_code == 200, response.text
    application = response.json()["application"]
    assert application["status"] == "in_progress"
    assert client.get("/api/state").json()["jobs"][0]["status"] == "prepared"
    submitted = client.post(f'/api/applications/{application["id"]}/confirm')
    assert submitted.status_code == 200
    state = client.get("/api/state").json()
    assert state["jobs"][0]["status"] == "applied"
    assert state["applications"][0]["status"] == "applied"
    assert not model[0]


def test_state_survives_new_client_and_database_connections(client, model):
    from backend.main import app

    item = confirm_item(client, create_item(client, model))
    assert client.put("/api/preferences", json={"track": "academia", "location": "Boston", "remote_only": True}).status_code == 200
    db.initialize()
    with TestClient(app, base_url=ORIGIN) as reopened:
        state = reopened.get("/api/state").json()
    assert state["items"][0]["id"] == item["id"]
    assert state["items"][0]["confirmed"] is True
    assert state["preferences"] == {"track": "academia", "location": "Boston", "remote_only": True}
    assert (db.DATA_DIR / "workspace.sqlite3").is_file()


def test_database_failed_mutation_rolls_back(client):
    original = db.get_state()

    def fail(state):
        state["profile"]["name"] = "Must not persist"
        raise RuntimeError("interrupted operation")

    with pytest.raises(RuntimeError, match="interrupted"):
        db.mutate_state(fail)
    assert db.get_state() == original


@pytest.mark.parametrize("origin", ["https://malicious.example", "null", "http://127.0.0.1:9999"])
def test_cross_origin_mutations_are_blocked(client, origin):
    response = client.put("/api/profile", json={"name": "Injected"}, headers={"Origin": origin})
    assert response.status_code == 403
    assert db.get_state()["profile"]["name"] == ""


def test_same_origin_mutations_are_allowed(client):
    response = client.put("/api/profile", json={"name": "User approved"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    assert db.get_state()["profile"]["name"] == "User approved"


def test_nonlocal_host_is_rejected(client):
    response = client.get("/api/state", headers={"Host": "attacker.example"})
    assert response.status_code in (400, 403)


def test_cross_site_fetch_without_origin_cannot_mutate(client):
    response = client.put("/api/profile", json={"name": "Injected"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert db.get_state()["profile"]["name"] == ""


def test_validation_errors_do_not_persist_bad_entries(client, model):
    response = client.post("/api/items", json={**ITEM, "kind": "invented", "title": ""})
    assert response.status_code == 422
    assert not db.get_state()["items"]
    assert not model[1]
