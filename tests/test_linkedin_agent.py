"""LinkedIn agent contracts and offline DOM fixtures; never contacts LinkedIn."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, jobs, linkedin, linkedin_agent
from backend.network import SourceError


JOB_URL = "https://www.linkedin.com/jobs/view/9000000001/"
JOB = {
    "title": "Example Research Engineer", "company": "Example Company",
    "location": "Berlin, Germany", "url": JOB_URL,
    "description": "Build research software with Python.",
    "salary": "", "job_type": "", "posted_at": "", "remote": False,
}


SEMANTIC_STUDY = {
    "url": JOB_URL,
    "title": "Cashier | Example Retail | LinkedIn",
    "structure": {
        "headings": [{"tag": "h1", "text": "On-site"}, {"tag": "h2", "text": "About the job"}, {"tag": "h2", "text": "Set alert for similar jobs"}, {"tag": "h2", "text": "About the company"}],
        "main_text": "Example Retail\nCashier\nExample City, Example Region · Reposted 2 days ago · 17 applicants\nOn-site\nFull-time\nApply\nSave\nAbout the job\nCashiers provide helpful customer service.\nHandle payments and answer customer questions.\nSet alert for similar jobs\nMarketing alert copy must not enter the description.\nAbout the company\nMarketing employer copy must not enter the description.",
    },
}


def snapshot(job=None, **changes):
    return {
        "snapshot_id": "test-snapshot", "url": JOB_URL, "title": JOB["title"],
        "page_type": "job", "status": 200, "requires_action": False,
        "message": "Read rendered LinkedIn page.", "warnings": [], "jobs": [],
        "job": job, "fields": [], "actions": [], "captured_at": "2026-09-27T12:00:00Z",
        **changes,
    }


@pytest.fixture(autouse=True)
def isolated_agent(monkeypatch):
    monkeypatch.setattr(linkedin, "_session", None)
    monkeypatch.setattr(linkedin, "_search_lock", asyncio.Lock())
    monkeypatch.setattr(linkedin_agent, "_snapshot", None)


@pytest.mark.parametrize("method", ["inspect_page", "study_page"])
def test_inspection_without_browser_never_opens_one(monkeypatch, method):
    launch = AsyncMock()
    monkeypatch.setattr(linkedin, "_get_session", launch)
    with pytest.raises(SourceError):
        asyncio.run(getattr(linkedin_agent, method)())
    launch.assert_not_awaited()


@pytest.mark.parametrize("operation", ["inspect", "study", "read", "fill"])
def test_agent_operations_refuse_busy_shared_browser(monkeypatch, operation):
    launch = AsyncMock()
    monkeypatch.setattr(linkedin, "_get_session", launch)

    async def attempt():
        await linkedin._search_lock.acquire()
        try:
            with pytest.raises(SourceError, match="(?i)(busy|progress|running)"):
                if operation == "inspect":
                    await linkedin_agent.inspect_page()
                elif operation == "study":
                    await linkedin_agent.study_page()
                elif operation == "read":
                    await linkedin_agent.read_job(JOB_URL)
                else:
                    await linkedin_agent.fill_fields("test-snapshot", {"field-1": "Example"})
        finally:
            linkedin._search_lock.release()

    asyncio.run(attempt())
    launch.assert_not_awaited()


@pytest.mark.parametrize("url", [
    "https://linkedin.com.attacker.example/jobs/view/9000000001/",
    "https://www.linkedin.com/jobs/search/?currentJobId=9000000001",
    "https://user:password@www.linkedin.com/jobs/view/9000000001/",
    "http://127.0.0.1/jobs/view/9000000001/",
    "javascript:alert(1)",
])
def test_read_rejects_noncanonical_targets_before_browser_launch(monkeypatch, url):
    launch = AsyncMock()
    monkeypatch.setattr(linkedin, "_get_session", launch)
    with pytest.raises(SourceError):
        asyncio.run(linkedin_agent.read_job(url))
    launch.assert_not_awaited()


def test_semantic_study_parser_matches_visible_role_and_bounds_description():
    from backend.linkedin_layouts import parse_study_job
    observed = parse_study_job(SEMANTIC_STUDY)
    assert observed["title"] == "Cashier"
    assert observed["company"] == "Example Retail"
    assert observed["location"] == "Example City, Example Region"
    assert observed["url"] == JOB_URL
    assert "Cashiers provide helpful customer service." in observed["description"]
    assert "Handle payments and answer customer questions." in observed["description"]
    assert "Marketing" not in observed["description"]
    assert "Set alert" not in observed["description"]


@pytest.mark.parametrize("title", ["On-site | Example Retail | LinkedIn", "Cashier | Unobserved Company | LinkedIn", "Unobserved role | Example Retail | LinkedIn", "LinkedIn"])
def test_semantic_study_parser_cannot_invent_identity_from_document_title(title):
    from backend.linkedin_layouts import parse_study_job
    assert parse_study_job({**SEMANTIC_STUDY, "title": title}) is None


@pytest.mark.parametrize("label", ["On-site", "Full-time", "Remote", "Hybrid"])
def test_workplace_badges_cannot_be_normalized_as_job_cards(label):
    with pytest.raises(SourceError):
        linkedin.normalize_job({"title": label, "url": JOB_URL})


@pytest.fixture
def agent_session(monkeypatch):
    page = SimpleNamespace(url=JOB_URL, is_closed=lambda: False)

    async def navigate(url, **kwargs):
        page.url = url
        return SimpleNamespace(status=200)

    page.goto = AsyncMock(side_effect=navigate)
    session = {"page": page, "browser": SimpleNamespace(is_connected=lambda: True), "context": SimpleNamespace(pages=[page]), "requires_action": False}
    monkeypatch.setattr(linkedin, "_session", session)
    monkeypatch.setattr(linkedin, "_get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(linkedin, "validate_public_url", AsyncMock(side_effect=lambda url: url))
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value={"url": JOB_URL, "status": 200, "title": JOB["title"], "has_jobs": True}))
    monkeypatch.setattr(linkedin, "_extract_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(linkedin, "_extract_detail", AsyncMock(return_value=JOB))
    bundle = SimpleNamespace(evaluate=AsyncMock(return_value=True), dispose=AsyncMock())
    monkeypatch.setattr(linkedin_agent, "_extract_application_fields", AsyncMock(return_value=(bundle, [], False)))
    return session


def test_active_inspection_reads_only_existing_page(agent_session):
    result = asyncio.run(linkedin_agent.inspect_page())
    assert result["page_type"] == "job"
    assert result["job"] == JOB
    assert result["fields"] == []
    agent_session["page"].goto.assert_not_awaited()
    linkedin._get_session.assert_not_awaited()


def test_read_navigates_once_to_canonical_job_url(agent_session):
    result = asyncio.run(linkedin_agent.read_job("https://uk.linkedin.com/jobs/view/research-engineer-9000000001/?trackingId=opaque"))
    assert result["job"] == JOB
    assert result["requires_action"] is False
    agent_session["page"].goto.assert_awaited_once()
    assert agent_session["page"].goto.await_args.args[0] == JOB_URL
    assert result["status"] == 200


GATE_SNAPSHOTS = [
    ("login", {"url": "https://www.linkedin.com/login", "has_login_form": True}),
    ("checkpoint", {"url": "https://www.linkedin.com/checkpoint/challenge/123", "has_challenge": True}),
    ("rate_limited", {"url": JOB_URL, "status": 429, "has_jobs": True}),
]


@pytest.mark.parametrize("kind,raw", GATE_SNAPSHOTS)
def test_inspection_of_gate_returns_no_jobs_or_fields(agent_session, monkeypatch, kind, raw):
    agent_session["page"].url = raw["url"]
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value=raw))
    result = asyncio.run(linkedin_agent.inspect_page())
    assert result["requires_action"] is True
    assert result["page_type"] == kind
    assert result["job"] is None and result["jobs"] == [] and result["fields"] == []
    linkedin._extract_detail.assert_not_awaited()
    linkedin_agent._extract_application_fields.assert_not_awaited()
    agent_session["page"].goto.assert_not_awaited()


@pytest.mark.parametrize("kind,raw", GATE_SNAPSHOTS)
@pytest.mark.parametrize("already_known", [False, True])
def test_job_read_stops_at_pending_gate_without_navigation(agent_session, monkeypatch, kind, raw, already_known):
    agent_session.update(requires_action=already_known)
    agent_session["page"].url = raw["url"]
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value=raw))
    result = asyncio.run(linkedin_agent.read_job(JOB_URL))
    assert result["requires_action"] is True and result["page_type"] == kind
    agent_session["page"].goto.assert_not_awaited()
    linkedin._get_session.assert_not_awaited()


def test_old_rate_limit_does_not_block_read_after_user_changes_page(agent_session, monkeypatch):
    agent_session.update(requires_action=True, message="LinkedIn is rate limiting this session.", gate_url=JOB_URL)
    agent_session["page"].url = "https://www.linkedin.com/feed/"

    async def observe(page, status, has_jobs):
        return {"url": page.url, "status": status, "title": "LinkedIn", "has_jobs": False}

    monkeypatch.setattr(linkedin, "_page_snapshot", observe)
    result = asyncio.run(linkedin_agent.read_job(JOB_URL))
    assert result["requires_action"] is False
    assert result["job"]["url"] == JOB_URL
    agent_session["page"].goto.assert_awaited_once()


def test_member_search_inspection_reads_selected_job_id(agent_session, monkeypatch):
    member_url = "https://www.linkedin.com/jobs/search/?keywords=Research&currentJobId=9000000001"
    agent_session["page"].url = member_url
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value={"url": member_url, "title": "Research jobs", "has_jobs": True}))
    observed = asyncio.run(linkedin_agent.inspect_page())
    assert observed["job"]["url"] == JOB_URL
    assert linkedin._extract_detail.await_args.args[1]["url"] == JOB_URL
    agent_session["page"].goto.assert_not_awaited()


def test_unrelated_page_inspection_never_extracts_or_launches(agent_session):
    agent_session["page"].url = "https://example.com/application"
    with pytest.raises(SourceError):
        asyncio.run(linkedin_agent.inspect_page())
    linkedin._extract_cards.assert_not_awaited()
    linkedin._get_session.assert_not_awaited()
    agent_session["page"].goto.assert_not_awaited()


@pytest.mark.parametrize("kind,raw", GATE_SNAPSHOTS)
def test_fill_stops_at_new_gate_and_invalidates_references(agent_session, monkeypatch, kind, raw):
    element = SimpleNamespace(fill=AsyncMock(), select_option=AsyncMock())
    bundle = SimpleNamespace(evaluate=AsyncMock(return_value=True), dispose=AsyncMock())
    monkeypatch.setattr(linkedin_agent, "_snapshot", {"id": "fresh", "page": agent_session["page"], "session": agent_session, "url": JOB_URL, "bundle": bundle, "fields": {"field-1": {"element": element, "metadata": {"kind": "text", "identity": {}}}}})
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value=raw))
    with pytest.raises(SourceError):
        asyncio.run(linkedin_agent.fill_fields("fresh", {"field-1": "Do not enter"}))
    assert linkedin_agent._snapshot is None
    assert agent_session["requires_action"] is True
    element.fill.assert_not_awaited()
    element.select_option.assert_not_awaited()
    bundle.dispose.assert_awaited_once()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from backend import linkedin_routes

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize()
    app = FastAPI()
    app.include_router(linkedin_routes.router)
    with TestClient(app) as client:
        yield client


def test_agent_inspection_endpoint_returns_snapshot_without_saving(workspace, monkeypatch):
    observed = snapshot(JOB)
    inspect = AsyncMock(return_value=observed)
    monkeypatch.setattr(linkedin_agent, "inspect_page", inspect)
    response = workspace.post("/api/linkedin/agent/inspect", json={})
    assert response.status_code == 200, response.text
    assert response.json() == observed
    inspect.assert_awaited_once()
    assert db.get_state()["jobs"] == []


def test_agent_study_endpoint_returns_bounded_observation_without_saving(workspace, monkeypatch):
    observed = snapshot(JOB, structure={"headings": [{"tag": "h1", "text": JOB["title"], "className": ""}], "containers": [], "selector_counts": {}, "main_text": JOB["description"], "truncated": False})
    study = AsyncMock(return_value=observed)
    monkeypatch.setattr(linkedin_agent, "study_page", study)
    response = workspace.post("/api/linkedin/agent/study", json={})
    assert response.status_code == 200, response.text
    assert response.json() == observed
    study.assert_awaited_once()
    assert db.get_state()["jobs"] == []


def test_read_job_can_preview_without_saving(workspace, monkeypatch):
    read = AsyncMock(return_value=snapshot(JOB))
    monkeypatch.setattr(linkedin_agent, "read_job", read)
    response = workspace.post("/api/linkedin/agent/job", json={"url": JOB_URL, "save": False})
    assert response.status_code == 200, response.text
    assert response.json()["job"]["title"] == JOB["title"]
    assert response.json().get("saved_job") is None
    assert db.get_state()["jobs"] == []


def test_read_job_deduplicates_and_refreshes_observed_facts(workspace, monkeypatch):
    original = {
        **JOB, "id": "existing-job", "source": "LinkedIn", "status": "prepared",
        "description": "Old description", "created_at": "2026-09-01T12:00:00Z",
        "resume": {"summary": "Stale tailored document"},
    }
    db.mutate_state(lambda state: state["jobs"].append(original))
    read = AsyncMock(return_value=snapshot({**JOB, "company": "", "description": "Updated research responsibilities"}))
    monkeypatch.setattr(linkedin_agent, "read_job", read)
    response = workspace.post("/api/linkedin/agent/job", json={"url": JOB_URL})
    assert response.status_code == 200, response.text
    stored = db.get_state()["jobs"]
    assert len(stored) == 1
    assert stored[0]["id"] == "existing-job"
    assert stored[0]["created_at"] == original["created_at"]
    assert stored[0]["description"] == "Updated research responsibilities"
    assert stored[0]["company"] == JOB["company"]
    assert stored[0]["status"] == "saved"
    assert not stored[0].get("resume")


def test_read_job_preserves_applied_history(workspace, monkeypatch):
    historical = {"summary": "Resume used when applying"}
    original = {**JOB, "id": "applied-job", "source": "LinkedIn", "status": "applied", "resume": historical}
    db.mutate_state(lambda state: state["jobs"].append(original))
    monkeypatch.setattr(linkedin_agent, "read_job", AsyncMock(return_value=snapshot({**JOB, "description": "Changed listing"})))
    response = workspace.post("/api/linkedin/agent/job", json={"url": JOB_URL, "save": True})
    assert response.status_code == 200, response.text
    stored = db.get_state()["jobs"][0]
    assert stored["id"] == original["id"]
    assert stored["status"] == "applied"
    assert stored["resume"] == historical


def test_gate_snapshot_does_not_invent_or_save_a_job(workspace, monkeypatch):
    monkeypatch.setattr(linkedin_agent, "read_job", AsyncMock(return_value=snapshot(JOB, page_type="checkpoint", requires_action=True)))
    response = workspace.post("/api/linkedin/agent/job", json={"url": JOB_URL})
    assert response.status_code == 200, response.text
    assert response.json()["requires_action"] is True
    assert response.json().get("saved_job") is None
    assert db.get_state()["jobs"] == []


def test_unrecognized_job_layout_does_not_overwrite_saved_job(workspace, monkeypatch):
    original = {**JOB, "id": "saved-cashier", "source": "LinkedIn", "status": "saved", "title": "Cashier", "company": "Example Company", "location": "Example City, Example Region"}
    db.mutate_state(lambda state: state["jobs"].append(original))
    observed = snapshot(None, page_type="unknown", title="LinkedIn", warnings=["No recognizable job title was found on this page."])
    monkeypatch.setattr(linkedin_agent, "read_job", AsyncMock(return_value=observed))
    response = workspace.post("/api/linkedin/agent/job", json={"url": JOB_URL})
    assert response.status_code == 200, response.text
    assert response.json()["saved_job"] is None
    assert db.get_state()["jobs"] == [original]


def test_agent_fill_endpoint_uses_only_explicit_field_mapping(workspace, monkeypatch):
    expected = {"filled": ["opaque-field"], "snapshot": snapshot()}
    fill = AsyncMock(return_value=expected)
    monkeypatch.setattr(linkedin_agent, "fill_fields", fill)
    response = workspace.post("/api/linkedin/agent/fill", json={"snapshot_id": "test-snapshot", "values": {"opaque-field": "Applicant Name"}})
    assert response.status_code == 200, response.text
    assert response.json() == expected
    fill.assert_awaited_once_with("test-snapshot", {"opaque-field": "Applicant Name"})


@pytest.mark.parametrize("payload", [
    {"snapshot_id": "snapshot", "values": {}},
    {"snapshot_id": "snapshot", "values": {"ref": True}},
    {"snapshot_id": "snapshot", "values": {"ref": {"selector": "#password"}}},
    {"snapshot_id": "snapshot", "values": {"ref": "Name"}, "submit": True},
    {"snapshot_id": "", "values": {"ref": "Name"}},
])
def test_fill_request_rejects_nontext_or_implicit_actions(workspace, monkeypatch, payload):
    fill = AsyncMock()
    monkeypatch.setattr(linkedin_agent, "fill_fields", fill)
    response = workspace.post("/api/linkedin/agent/fill", json=payload)
    assert response.status_code == 422, response.text
    fill.assert_not_awaited()


def test_local_agent_job_lookup_filters_and_paginates_without_browser(workspace, monkeypatch):
    db.mutate_state(lambda state: state["jobs"].extend([
        {**JOB, "id": "one", "source": "LinkedIn"},
        {**JOB, "id": "two", "source": "LinkedIn", "title": "Python developer", "url": "https://www.linkedin.com/jobs/view/9000000002/"},
        {**JOB, "id": "three", "source": "Manual", "url": "https://example.com/job/3"},
    ]))
    inspect = AsyncMock()
    monkeypatch.setattr(linkedin_agent, "inspect_page", inspect)
    response = workspace.get("/api/linkedin/agent/jobs", params={"query": "Python", "limit": 1, "offset": 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["source"] == "LinkedIn"
    assert payload["limit"] == 1 and payload["offset"] == 1
    inspect.assert_not_awaited()


def test_saved_search_persists_defaults_and_deletion(workspace, monkeypatch):
    search = AsyncMock()
    monkeypatch.setattr(jobs, "search_linkedin_jobs", search)
    response = workspace.post("/api/linkedin/searches", json={"name": "All US and Europe roles"})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["keywords"] == ""
    assert record["regions"] == ["United States", "Europe"]
    assert record["remote_only"] is False
    assert record["limit"] == 10
    assert record["last_run_at"] is None and record["last_result"] is None
    assert workspace.get("/api/linkedin/searches").json()["items"] == [record]
    assert db.get_state()["linkedin_searches"] == [record]
    search.assert_not_awaited()
    response = workspace.delete(f"/api/linkedin/searches/{record['id']}")
    assert response.status_code == 200, response.text
    assert workspace.get("/api/linkedin/searches").json()["items"] == []


def test_saved_search_run_reuses_exact_spec_and_records_gate(workspace, monkeypatch):
    created = workspace.post("/api/linkedin/searches", json={"name": "Research in Europe", "keywords": "Research", "regions": ["Europe"], "remote_only": True, "limit": 3})
    assert created.status_code == 201, created.text
    record = created.json()
    result = {"jobs": [], "found": 0, "count": 0, "requires_action": True, "message": "Wait before retrying.", "warnings": [], "regions": ["Europe"], "search_urls": []}
    search = AsyncMock(return_value=result)
    monkeypatch.setattr(jobs, "search_linkedin_jobs", search)
    response = workspace.post(f"/api/linkedin/searches/{record['id']}/run", json={})
    assert response.status_code == 200, response.text
    search.assert_awaited_once()
    body = search.await_args.args[0]
    assert body.keywords == "Research"
    assert body.regions == ["Europe"]
    assert body.remote_only is True and body.limit == 3 and body.save is True
    stored = db.get_state()["linkedin_searches"][0]
    for key in ("id", "name", "keywords", "regions", "remote_only", "limit", "created_at"):
        assert stored[key] == record[key]
    assert stored["last_run_at"]
    assert stored["last_result"]["requires_action"] is True


@pytest.mark.parametrize("payload", [
    {"name": ""}, {"name": "Missing regions", "regions": []},
    {"name": "Invalid limit", "limit": 26},
    {"name": "Unexpected script", "javascript": "document.cookie"},
])
def test_saved_search_validation(workspace, payload):
    response = workspace.post("/api/linkedin/searches", json=payload)
    assert response.status_code == 422, response.text
    assert db.get_state()["linkedin_searches"] == []


def test_missing_saved_search_returns_404(workspace):
    assert workspace.post("/api/linkedin/searches/missing/run", json={}).status_code == 404
    assert workspace.delete("/api/linkedin/searches/missing").status_code == 404


def test_saved_search_names_are_unique_after_trimming(workspace):
    assert workspace.post("/api/linkedin/searches", json={"name": " Research "}).status_code == 201
    duplicate = workspace.post("/api/linkedin/searches", json={"name": "research"})
    assert duplicate.status_code == 409, duplicate.text
    assert len(db.get_state()["linkedin_searches"]) == 1


def test_saved_search_deletion_keeps_discovered_jobs(workspace):
    created = workspace.post("/api/linkedin/searches", json={"name": "Research"}).json()
    db.mutate_state(lambda state: state["jobs"].append({**JOB, "id": "saved-job", "source": "LinkedIn"}))
    assert workspace.delete(f"/api/linkedin/searches/{created['id']}").status_code == 200
    assert db.get_state()["jobs"][0]["id"] == "saved-job"


def test_local_job_lookup_excludes_private_tailored_resume(workspace):
    db.mutate_state(lambda state: state["jobs"].append({**JOB, "id": "private-job", "source": "LinkedIn", "resume": {"email": "private@example.test"}}))
    response = workspace.get("/api/linkedin/agent/jobs")
    assert response.status_code == 200, response.text
    assert "resume" not in response.json()["items"][0]
    assert "private@example.test" not in response.text


def test_agent_tool_manifest_has_valid_parameter_schemas_and_no_submit(workspace):
    response = workspace.get("/api/linkedin/agent/tools")
    assert response.status_code == 200, response.text
    tools = response.json()["tools"]
    names = {tool["name"] for tool in tools}
    assert {"linkedin_inspect", "linkedin_study", "linkedin_read_job", "linkedin_fill", "linkedin_search", "linkedin_saved_jobs", "linkedin_save_search", "linkedin_run_search"} <= names
    assert not any("submit" in tool["name"] or "script" in tool["name"] for tool in tools)
    for tool in tools:
        assert tool["method"] in {"GET", "POST"}
        assert tool["path"].startswith("/api/linkedin/")
        assert tool["parameters"]["type"] == "object"
        assert tool["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000", "http://example.com", "http://localhost.attacker.test:8000",
    "http://user:secret@localhost:8000", "http://localhost:8000/path",
    "http://localhost:8000?api=other", "http://localhost:99999", "file:///tmp/app",
])
def test_sdk_refuses_nonlocal_or_ambiguous_base_urls(url):
    import linkedin_agent as sdk
    with pytest.raises(sdk.AgentError):
        sdk.LinkedInAgent(url)


def test_sdk_invoke_uses_declared_tool_names_and_explicit_request_bodies(monkeypatch):
    import linkedin_agent as sdk
    agent = sdk.LinkedInAgent()
    request = Mock(return_value={"ok": True})
    monkeypatch.setattr(agent, "_request", request)
    assert agent.invoke("linkedin_fill", {"snapshot_id": "snap", "values": {"field": "Answer"}}) == {"ok": True}
    request.assert_called_once_with("POST", "/linkedin/agent/fill", {"snapshot_id": "snap", "values": {"field": "Answer"}})
    request.reset_mock()
    agent.invoke("linkedin_search", {})
    request.assert_called_once_with("POST", "/linkedin/search", {"keywords": "", "regions": ["United States", "Europe"], "limit": 10, "remote_only": False, "save": True})
    request.reset_mock()
    for name, arguments in (("submit_application", {}), ("linkedin_fill", {"selector": "#password"}), ("linkedin_inspect", {"script": "document.cookie"}), ("linkedin_search", "not a mapping")):
        with pytest.raises(sdk.AgentError):
            agent.invoke(name, arguments)
    request.assert_not_called()


def test_sdk_saved_job_query_encodes_user_text(monkeypatch):
    import linkedin_agent as sdk
    from urllib.parse import parse_qs, urlsplit
    agent = sdk.LinkedInAgent()
    request = Mock(return_value={})
    monkeypatch.setattr(agent, "_request", request)
    agent.saved_jobs('C++ & "research"', 2, 3)
    method, path = request.call_args.args
    assert method == "GET"
    assert parse_qs(urlsplit(path).query) == {"query": ['C++ & "research"'], "limit": ["2"], "offset": ["3"]}


def test_sdk_study_calls_read_only_observation_endpoint(monkeypatch):
    import linkedin_agent as sdk
    agent = sdk.LinkedInAgent()
    request = Mock(return_value={})
    monkeypatch.setattr(agent, "_request", request)
    agent.invoke("linkedin_study", {})
    request.assert_called_once_with("POST", "/linkedin/agent/study", {})


def test_sdk_rejects_server_redirects_before_following_them():
    import linkedin_agent as sdk
    with pytest.raises(sdk.AgentError, match="redirect"):
        sdk._NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://attacker.example/")


async def _offline_browser(monkeypatch, check):
    """Exercise the real agent with rendered fixture DOM and zero network access."""
    playwright = pytest.importorskip("playwright.async_api")
    chrome = Path(os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    options = {"headless": True, **({"executable_path": str(chrome)} if chrome.exists() else {"channel": "chrome"})}
    fixture = (Path(__file__).parent / "fixtures" / "linkedin_agent_application.html").read_text()
    async with playwright.async_playwright() as runner:
        browser = await runner.chromium.launch(**options)
        try:
            context = await browser.new_context(service_workers="block")

            async def intercepted(route):
                if route.request.url == JOB_URL:
                    await route.fulfill(status=200, content_type="text/html", body=fixture)
                else:
                    await route.abort()

            await context.route("**/*", intercepted)
            page = await context.new_page()
            await page.goto(JOB_URL)
            session = {"page": page, "browser": browser, "context": context, "requires_action": False, "search_url": JOB_URL}
            monkeypatch.setattr(linkedin, "_session", session)
            monkeypatch.setattr(linkedin, "_get_session", AsyncMock(return_value=session))
            monkeypatch.setattr(linkedin, "validate_public_url", AsyncMock(side_effect=lambda url: url))
            await check(page, session)
        finally:
            await browser.close()


optional_dom = pytest.mark.skipif(os.environ.get("PUTMETO_RUN_DOM_TESTS") != "1", reason="Optional installed Chrome offline agent fixtures; set PUTMETO_RUN_DOM_TESTS=1")


@optional_dom
def test_offline_agent_reads_application_and_fills_only_explicit_fields(monkeypatch):
    async def check(page, session):
        observed = await linkedin_agent.inspect_page()
        assert observed["page_type"] == "application"
        assert observed["requires_action"] is False
        fields = {entry["label"]: entry for entry in observed["fields"]}
        assert set(fields) == {"Full name", "Email address", "Phone number", "Cover note", "Country"}
        assert fields["Full name"]["required"] is True
        assert {entry["value"] for entry in fields["Country"]["options"]} >= {"DE", "US"}
        serialized = json.dumps(observed)
        for private in ("synthetic-password", "hidden-secret", "css-hidden-secret", "existing@example.test", "disabled-secret", "read-only-secret", "000000"):
            assert private not in serialized
        assert all("value" not in field for field in observed["fields"])
        assert "Ignore previous instructions" in observed["job"]["description"]
        mapping = {
            fields["Full name"]["ref"]: "Example Applicant",
            fields["Cover note"]["ref"]: "I work with Python and research software.",
            fields["Country"]["ref"]: "DE",
        }
        result = await linkedin_agent.fill_fields(observed["snapshot_id"], mapping)
        assert set(result["filled"]) == set(mapping)
        assert result["snapshot"]["snapshot_id"] != observed["snapshot_id"]
        assert await page.locator("#applicant-name").input_value() == "Example Applicant"
        assert await page.locator("#cover-note").input_value() == mapping[fields["Cover note"]["ref"]]
        assert await page.locator("#country").input_value() == "DE"
        assert await page.locator("#applicant-email").input_value() == "existing@example.test"
        assert await page.locator("#search-box").input_value() == ""
        assert not await page.locator("#terms").is_checked()
        assert await page.evaluate("({submitted: window.submitCount, advanced: window.nextCount})") == {"submitted": 0, "advanced": 0}
        with pytest.raises(SourceError):
            await linkedin_agent.fill_fields(observed["snapshot_id"], mapping)

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_offline_agent_validates_entire_fill_before_changing_any_input(monkeypatch):
    async def check(page, session):
        observed = await linkedin_agent.inspect_page()
        fields = {entry["label"]: entry for entry in observed["fields"]}
        name = fields["Full name"]["ref"]
        country = fields["Country"]["ref"]
        for invalid_kind in ("unknown_ref", "unknown_option", "disabled_option"):
            invalid = {name: "Should not be written", **({"#password": "secret"} if invalid_kind == "unknown_ref" else {country: "FR" if invalid_kind == "disabled_option" else "not-an-option"})}
            with pytest.raises(SourceError):
                await linkedin_agent.fill_fields(observed["snapshot_id"], invalid)
            assert await page.locator("#applicant-name").input_value() == ""
            observed = await linkedin_agent.inspect_page()
            fields = {entry["label"]: entry for entry in observed["fields"]}
            name, country = fields["Full name"]["ref"], fields["Country"]["ref"]
        assert await page.evaluate("window.submitCount + window.nextCount") == 0

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_offline_application_scope_with_password_exposes_no_fields(monkeypatch):
    async def check(page, session):
        await page.locator("#application-form").evaluate("form => form.append(document.querySelector('#password'))")
        observed = await linkedin_agent.inspect_page()
        assert observed["fields"] == []
        assert "synthetic-password" not in json.dumps(observed)

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
@pytest.mark.parametrize("mutation", [
    "document.querySelector('#applicant-name').outerHTML = '<input id=applicant-name name=full_name>'",
    "document.querySelector('label[for=applicant-name]').textContent = 'Account password'",
    "document.querySelector('#applicant-name').type = 'password'",
    "document.querySelector('#application-form').style.display = 'none'",
    "document.querySelector('h1').textContent = 'Different employer and role'",
    "document.querySelector('.jobs-easy-apply-modal h2').textContent = 'Apply to a different company'",
])
def test_offline_agent_rejects_changed_page_or_field_after_inspection(monkeypatch, mutation):
    async def check(page, session):
        observed = await linkedin_agent.inspect_page()
        name = next(field["ref"] for field in observed["fields"] if field["label"] == "Full name")
        await page.evaluate(mutation)
        with pytest.raises(SourceError):
            await linkedin_agent.fill_fields(observed["snapshot_id"], {name: "Must remain blank"})
        assert await page.locator("#applicant-name").input_value() == ""
        assert await page.evaluate("window.submitCount + window.nextCount") == 0

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_offline_agent_rejects_snapshot_after_same_url_reload(monkeypatch):
    async def check(page, session):
        observed = await linkedin_agent.inspect_page()
        name = next(field["ref"] for field in observed["fields"] if field["label"] == "Full name")
        await page.reload()
        with pytest.raises(SourceError):
            await linkedin_agent.fill_fields(observed["snapshot_id"], {name: "Must remain blank"})
        assert await page.locator("#applicant-name").input_value() == ""

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_unrecognized_member_layout_does_not_treat_workplace_heading_as_job_title(monkeypatch):
    async def check(page, session):
        await page.set_content(f"""<!doctype html><title>LinkedIn</title><main><a href="{JOB_URL}"><h1>On-site</h1></a><a href="{JOB_URL}">Full-time</a><p>Workplace type</p><button>Easy Apply</button></main>""")
        assert await linkedin._extract_cards(page) == []
        with pytest.raises(SourceError):
            await linkedin._extract_detail(page, {"url": JOB_URL, "title": ""})
        # A previously observed search-card title must also survive this layout.
        seeded = await linkedin._extract_detail(page, {"url": JOB_URL, "title": "Cashier", "company": "Example Company"})
        assert seeded["title"] == "Cashier"
        assert seeded["company"] == "Example Company"
        observed = await linkedin_agent.inspect_page()
        assert observed["job"] is None
        assert observed["page_type"] == "unknown"
        assert observed["warnings"]
        assert observed["fields"] == []

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_semantic_member_layout_reads_observed_role_without_workplace_badges(monkeypatch):
    async def check(page, session):
        fixture = (Path(__file__).parent / "fixtures" / "linkedin_member_semantic.html").read_text()
        await page.set_content(fixture)
        assert await linkedin._extract_cards(page) == []
        observed = await linkedin_agent.inspect_page()
        assert observed["requires_action"] is False
        assert observed["page_type"] == "job"
        job = observed["job"]
        assert job["url"] == JOB_URL
        assert job["title"] == "Cashier"
        assert job["company"] == "Example Retail"
        assert job["location"] == "Example City, Example Region"
        assert "Cashiers provide helpful customer service" in job["description"]
        assert "Handle payments and answer customer questions." in job["description"]
        assert "This role works with the store team" in job["description"]
        for unwanted in ("Set alert", "Marketing alert copy", "About the company", "Marketing employer copy", "Notifications"):
            assert unwanted not in job["description"]

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
@pytest.mark.parametrize("title", ["Different role | Different employer | LinkedIn", "Cashier | Unobserved company | LinkedIn", "Unobserved role | Example Retail | LinkedIn"])
def test_semantic_member_fallback_requires_title_and_company_visible_on_page(monkeypatch, title):
    async def check(page, session):
        fixture = (Path(__file__).parent / "fixtures" / "linkedin_member_semantic.html").read_text()
        await page.set_content(fixture)
        await page.evaluate("title => document.title = title", title)
        with pytest.raises(SourceError):
            await linkedin._extract_detail(page, {"url": JOB_URL, "title": ""})
        observed = await linkedin_agent.inspect_page()
        assert observed["job"] is None
        assert observed["jobs"] == []

    asyncio.run(_offline_browser(monkeypatch, check))


@optional_dom
def test_study_page_excludes_application_answers_and_other_editable_text(monkeypatch):
    async def check(page, session):
        await page.locator("#cover-note").fill("private-cover-letter-answer")
        await page.evaluate("""() => {
            const fixture = document.createElement('section');
            fixture.innerHTML = '<div contenteditable=true>private-editable-answer</div><div role=textbox>private-textbox-answer</div><select><option>private-option-answer</option></select><span hidden>private-hidden-answer</span>';
            document.querySelector('main').append(fixture);
        }""")
        previous_url = page.url
        observed = await linkedin_agent.study_page()
        structure = observed["structure"]
        assert structure
        assert any(heading["text"] == JOB["title"] for heading in structure["headings"])
        assert "Build research software" in structure["main_text"]
        assert len(structure["headings"]) <= 40
        assert len(structure["containers"]) <= 40
        assert len(structure["main_text"]) <= 20_000
        serialized = json.dumps(observed)
        for private in ("private-cover-letter-answer", "private-editable-answer", "private-textbox-answer", "private-option-answer", "private-hidden-answer", "existing@example.test", "synthetic-password", "hidden-secret"):
            assert private not in serialized
        assert "Apply to Example Company" not in json.dumps(structure)
        assert page.url == previous_url
        assert await page.locator("#cover-note").input_value() == "private-cover-letter-answer"
        assert await page.evaluate("window.submitCount + window.nextCount") == 0
        linkedin._get_session.assert_not_awaited()

    asyncio.run(_offline_browser(monkeypatch, check))


@pytest.mark.parametrize("kind,raw", GATE_SNAPSHOTS)
def test_study_gate_omits_layout_diagnostics(agent_session, monkeypatch, kind, raw):
    agent_session["page"].url = raw["url"]
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value=raw))
    observed = asyncio.run(linkedin_agent.study_page())
    assert observed["requires_action"] is True
    assert observed["page_type"] == kind
    assert observed["structure"] is None
    linkedin._extract_detail.assert_not_awaited()
    agent_session["page"].goto.assert_not_awaited()
