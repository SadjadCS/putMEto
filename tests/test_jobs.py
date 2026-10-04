"""Behavior tests for discovery, confirmation, and public-source boundaries."""

import asyncio
import json
import socket
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, jobs, network
from backend.browser import extract_jsonld


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize()
    db.mutate_state(lambda state: state.update(
        positions=[{"id": "p1", "name": "Software Engineer", "confirmed": True}],
        skills=[{"id": "s1", "name": "Python", "confirmed": True}, {"id": "s2", "name": "Go", "confirmed": False}],
    ))
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client


def test_disabled_unconfigured_sources_can_be_saved(workspace):
    response = workspace.put("/api/sources", json={"items": db.default_state()["sources"]})
    assert response.status_code == 200
    assert len(db.get_state()["sources"]) == len(db.default_state()["sources"])


def test_discovery_isolates_failure_deduplicates_and_preserves_status(workspace, monkeypatch):
    db.mutate_state(lambda state: state.update(sources=[
        {"id": "broken", "name": "Broken board", "kind": "greenhouse", "enabled": True},
        {"id": "working", "name": "Working board", "kind": "lever", "enabled": True},
    ]))

    async def fetch(source):
        if source["id"] == "broken":
            raise network.SourceError("The board is unavailable.")
        return [
            {"title": "Senior Software Engineer", "company": "Example", "description": "Build with Python", "url": "https://example.com/jobs/1?utm_source=test"},
            {"title": "Software Engineer", "company": "Example", "description": "Python", "url": "https://example.com/jobs/1"},
            {"title": "Sales Representative", "company": "Example", "description": "Go to customers", "url": "https://example.com/jobs/2"},
        ]

    monkeypatch.setattr(jobs, "_fetch_source", fetch)
    response = workspace.post("/api/jobs/discover")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert "Broken board" in response.json()["warnings"][0]
    job = db.get_state()["jobs"][0]
    assert job["matched_skills"] == ["Python"]
    workspace.patch(f"/api/jobs/{job['id']}", json={"status": "saved"})
    assert workspace.post("/api/jobs/discover").json()["count"] == 0
    assert db.get_state()["jobs"][0]["status"] == "saved"


def test_discovery_requiresconfirmed_positions(workspace):
    db.mutate_state(lambda state: state.update(positions=[{"name": "Engineer", "confirmed": False}]))
    assert workspace.post("/api/jobs/discover").status_code == 422


def test_remotive_uses_persistent_six_hour_cache(workspace, monkeypatch):
    fetch = AsyncMock(return_value={"jobs": [{"title": "Engineer"}]})
    monkeypatch.setattr(jobs, "fetch_json", fetch)
    assert asyncio.run(jobs._remotive()) == [{"title": "Engineer"}]
    assert asyncio.run(jobs._remotive()) == [{"title": "Engineer"}]
    assert fetch.await_count == 1
    path = db.DATA_DIR / "remotive-cache.json"
    payload = json.loads(path.read_text())
    payload["fetched_at"] -= jobs._REMOTIVE_TTL + 1
    path.write_text(json.dumps(payload))
    asyncio.run(jobs._remotive())
    assert fetch.await_count == 2


def test_application_is_only_applied_after_confirmation(workspace, monkeypatch):
    job = jobs.make_job({"title": "Software Engineer", "company": "Example", "url": "https://example.com/jobs/1", "description": "Python"}, "Example", db.get_state(), "prepared")
    job["resume"] = {"profile": {}, "items": [], "skills": []}
    db.mutate_state(lambda state: state["jobs"].append(job))
    launch = AsyncMock(return_value={"filled_fields": 2, "resume_attached": True})
    monkeypatch.setattr(jobs, "start_application", launch)
    response = workspace.post(f"/api/jobs/{job['id']}/apply")
    assert response.status_code == 200, response.text
    application = response.json()["application"]
    assert application["status"] == "in_progress"
    assert db.get_state()["jobs"][0]["status"] == "prepared"
    result = workspace.post(f"/api/applications/{application['id']}/confirm")
    assert result.status_code == 200
    assert db.get_state()["jobs"][0]["status"] == "applied"
    assert workspace.post(f"/api/jobs/{job['id']}/apply").status_code == 409


def test_apply_requires_prepared_resume(workspace, monkeypatch):
    job = jobs.make_job({"title": "Engineer", "company": "Example", "url": "https://example.com/jobs/1"}, "Example", db.get_state())
    db.mutate_state(lambda state: state["jobs"].append(job))
    launch = AsyncMock()
    monkeypatch.setattr(jobs, "start_application", launch)
    assert workspace.post(f"/api/jobs/{job['id']}/apply").status_code == 422
    launch.assert_not_called()


def test_manual_submission_confirmation_is_recorded(workspace):
    job = jobs.make_job({"title": "Engineer", "company": "Example", "url": "https://example.com/jobs/1"}, "Example", db.get_state())
    db.mutate_state(lambda state: state["jobs"].append(job))
    response = workspace.patch(f"/api/jobs/{job['id']}", json={"status": "applied"})
    assert response.status_code == 200
    application = db.get_state()["applications"][0]
    assert application["status"] == "applied"
    assert "confirmed by the user" in application["note"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.1/x", "http://[::1]/x", "http://169.254.169.254/latest", "file:///etc/passwd", "https://localhost/x", "https://user:secret@example.com/x", "https://example.com:22/x", "https://server.local/"])
def test_nonpublic_url_shapes_rejected(url):
    with pytest.raises(network.SourceError):
        network.validate_url_shape(url)


def test_public_hostname_resolving_privately_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443))])
    with pytest.raises(network.SourceError, match="private"):
        asyncio.run(network.validate_public_url("https://careers.example.com"))


def test_redirect_to_private_url_is_rejected_before_fetch(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secrets"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(network.SourceError):
        asyncio.run(network.fetch_json("https://example.com/jobs"))
    assert requests == ["https://example.com/jobs"]


def test_schema_graph_and_description_are_data():
    raw = json.dumps({"@graph": [{"@type": "Organization"}, {"@type": ["Thing", "JobPosting"], "title": "Engineer", "description": "<p>Python</p><script>steal()</script>"}]})
    postings = extract_jsonld(raw)
    assert len(postings) == 1
    assert jobs.plain_text(postings[0]["description"]) == "Python"
    assert extract_jsonld("not json") == []


def test_remote_and_location_preferences_and_word_boundaries():
    state = db.default_state()
    state["positions"] = [{"name": "Software Engineer", "confirmed": True}]
    state["skills"] = [{"name": "Go", "confirmed": True}]
    state["preferences"].update(remote_only=True, location="New York")
    job = {"title": "Software Engineer", "description": "Google tools", "location": "Remote — Worldwide", "remote": True}
    score, matched, relevant = jobs.score_job(job, state)
    assert relevant and matched == []
    job["location"] = "Remote — Germany"
    assert not jobs.score_job(job, state)[2]


def test_board_tokens_are_allowlisted():
    assert jobs._board_token("https://jobs.eu.lever.co/example", "lever") == ("example", True)
    assert jobs._board_token("https://job-boards.greenhouse.io/example/jobs/123", "greenhouse") == ("example", False)
    with pytest.raises(network.SourceError):
        jobs._board_token("https://attacker.example/company", "greenhouse")
