"""Offline coverage for LinkedIn search, review boundaries, and saved jobs."""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend import db, jobs, linkedin
from backend.network import SourceError


SEARCH_URL = "https://www.linkedin.com/jobs/search/?keywords=Python"
RAW_JOB = {
    "title": "Python Software Engineer",
    "company": "Example Company",
    "location": "Remote — Worldwide",
    "url": "https://www.linkedin.com/jobs/view/python-software-engineer-at-example-9000000001/?trackingId=abc",
    "description": "Build Python backend services and maintain SQL queries.",
    "remote": True,
}


def search_result(*entries, requires_action=False):
    return {
        "jobs": list(entries),
        "requires_action": requires_action,
        "message": "Complete sign-in in the LinkedIn browser and retry." if requires_action else "Read LinkedIn job results.",
        "search_url": SEARCH_URL,
        "warnings": [],
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from backend.main import app

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def test_search_url_encodes_literal_keywords_and_location():
    url = linkedin.build_search_url('C++ engineer & "machine learning"', "New York, NY", True)
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "www.linkedin.com"
    assert parsed.path.rstrip("/") == "/jobs/search"
    query = parse_qs(parsed.query)
    assert query["keywords"] == ['C++ engineer & "machine learning"']
    assert query["location"] == ["New York, NY"]
    assert query["f_WT"] == ["2"]
    assert not parsed.fragment
    assert "New York" not in url


def test_search_url_omits_unrequested_filters():
    query = parse_qs(urlsplit(linkedin.build_search_url("Research scientist")).query)
    assert query["keywords"] == ["Research scientist"]
    assert "location" not in query
    assert "f_WT" not in query


@pytest.mark.parametrize("keywords", ["", "   "])
def test_all_title_search_url_omits_empty_keyword_filter(keywords):
    query = parse_qs(urlsplit(linkedin.build_search_url(keywords, "United States")).query)
    assert "keywords" not in query
    assert query["location"] == ["United States"]


def test_canonical_linkedin_job_ids_deduplicate_guest_and_member_links():
    variants = [
        "https://www.linkedin.com/jobs/view/9000000001?refId=one&trackingId=abc",
        "https://www.linkedin.com/jobs/view/python-software-engineer-at-example-9000000001/?trk=public_jobs",
        "https://uk.linkedin.com/jobs/view/9000000001#details",
    ]
    canonical = {linkedin.canonical_job_url(url) for url in variants}
    assert len(canonical) == 1
    result = urlsplit(canonical.pop())
    assert result.hostname == "www.linkedin.com"
    assert result.path.rstrip("/") == "/jobs/view/9000000001"
    assert result.query == result.fragment == ""


@pytest.mark.parametrize("url", [
    "https://linkedin.com.attacker.example/jobs/view/9000000001",
    "https://attacker.example/linkedin.com/jobs/view/9000000001",
    "http://127.0.0.1/jobs/view/9000000001",
    "javascript:alert(1)",
    "https://user:secret@www.linkedin.com/jobs/view/9000000001",
    "https://www.linkedin.com:8123/jobs/view/9000000001",
    "https://www.linkedin.com/jobs/view/not-a-job-id",
    "https://www.linkedin.com/jobs/search/?currentJobId=9000000001",
    "https://www.linkedin.com/company/example",
])
def test_canonical_job_url_rejects_unrelated_or_invalid_targets(url):
    with pytest.raises(SourceError):
        linkedin.canonical_job_url(url)


def test_normalization_keeps_observed_job_fields_without_inventing_claims():
    normalized = linkedin.normalize_job(RAW_JOB)
    assert normalized["title"] == RAW_JOB["title"]
    assert normalized["company"] == RAW_JOB["company"]
    assert normalized["description"] == RAW_JOB["description"]
    assert normalized["url"] == linkedin.canonical_job_url(RAW_JOB["url"])
    sparse = linkedin.normalize_job({"title": "Research fellow", "url": "https://www.linkedin.com/jobs/view/9000000002"})
    assert sparse["company"] == ""
    assert sparse["description"] == ""
    assert not sparse.get("salary")


@pytest.mark.parametrize("entry", [
    {"url": RAW_JOB["url"]},
    {"title": "Engineer"},
    {"title": " ", "url": RAW_JOB["url"]},
    {"title": "Engineer", "url": "https://example.com/jobs/123"},
])
def test_normalization_rejects_incomplete_or_untrusted_cards(entry):
    with pytest.raises(SourceError):
        linkedin.normalize_job(entry)


def test_navigation_signin_text_does_not_hide_public_job_results():
    page = {"url": SEARCH_URL, "status": 200, "title": "Python jobs", "text": "Sign in Join now Python Software Engineer. Sign in to view more jobs", "has_jobs": True, "has_login_form": True, "has_challenge": False}
    assert linkedin.classify_page(page) == "ready"


@pytest.mark.parametrize("changes,expected", [
    ({"url": "https://www.linkedin.com/login", "has_login_form": True}, "login"),
    ({"url": "https://www.linkedin.com/checkpoint/challenge/123", "has_challenge": True}, "checkpoint"),
    ({"status": 429, "has_jobs": True}, "rate_limited"),
    ({"text": "No matching jobs found"}, "empty"),
])
def test_blocked_and_empty_pages_have_explicit_distinct_states(changes, expected):
    page = {"url": SEARCH_URL, "status": 200, "title": "LinkedIn", "text": "", "has_jobs": False, "has_login_form": False, "has_challenge": False}
    assert linkedin.classify_page({**page, **changes}) == expected


def test_unrecognized_markup_with_nonzero_jobs_is_not_reported_as_empty():
    page = {"url": SEARCH_URL, "status": 200, "title": "100 jobs", "text": "100 jobs matching Python", "has_jobs": False, "has_login_form": False, "has_challenge": False}
    assert linkedin.classify_page(page) == "unknown"


@pytest.fixture
def browser_session(monkeypatch):
    page = SimpleNamespace(url=SEARCH_URL, is_closed=lambda: False, wait_for_selector=AsyncMock())

    async def navigate(url, **kwargs):
        page.url = url
        return SimpleNamespace(status=200)

    page.goto = AsyncMock(side_effect=navigate)
    session = {"page": page, "browser": SimpleNamespace(is_connected=lambda: True), "requires_action": False}
    monkeypatch.setattr(linkedin, "_get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(linkedin, "validate_public_url", AsyncMock(side_effect=lambda url: url))
    monkeypatch.setattr(linkedin, "_search_lock", asyncio.Lock())
    monkeypatch.setattr(linkedin, "_extract_cards", AsyncMock(return_value=[linkedin.normalize_job({**RAW_JOB, "description": ""})]))
    monkeypatch.setattr(linkedin, "_extract_detail", AsyncMock(return_value=linkedin.normalize_job(RAW_JOB)))
    return session


def test_browser_search_reads_real_cards_and_details_and_respects_limit(browser_session, monkeypatch):
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(side_effect=[
        {"url": SEARCH_URL, "status": 200, "has_jobs": True, "has_login_form": True},
        {"url": RAW_JOB["url"], "status": 200, "has_jobs": False},
    ]))
    result = asyncio.run(linkedin.search_linkedin("Python", limit=1))
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["description"] == RAW_JOB["description"]
    assert result["requires_action"] is False
    assert browser_session["page"].goto.await_count == 2
    linkedin._extract_detail.assert_awaited_once()


def test_pending_security_challenge_keeps_window_without_new_navigation(browser_session, monkeypatch):
    browser_session["requires_action"] = True
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(return_value={"url": "https://www.linkedin.com/checkpoint/challenge/123", "has_challenge": True}))
    result = asyncio.run(linkedin.search_linkedin("Python"))
    assert result["requires_action"] is True
    assert result["jobs"] == []
    assert "security check" in result["message"]
    browser_session["page"].goto.assert_not_awaited()
    linkedin._extract_detail.assert_not_awaited()


def test_detail_login_gate_preserves_only_observed_search_cards(browser_session, monkeypatch):
    monkeypatch.setattr(linkedin, "_page_snapshot", AsyncMock(side_effect=[
        {"url": SEARCH_URL, "status": 200, "has_jobs": True},
        {"url": "https://www.linkedin.com/login", "has_login_form": True},
    ]))
    result = asyncio.run(linkedin.search_linkedin("Python"))
    assert result["requires_action"] is True
    assert result["warnings"]
    assert result["jobs"][0]["title"] == RAW_JOB["title"]
    assert result["jobs"][0]["description"] == ""
    linkedin._extract_detail.assert_not_awaited()


def test_completed_login_retry_can_read_cards_despite_guest_login_prompt(browser_session, monkeypatch):
    browser_session["requires_action"] = True

    async def snapshot(page, status, has_jobs):
        is_search = "/jobs/search/" in page.url
        return {"url": page.url, "status": status, "has_jobs": has_jobs, "has_login_form": is_search, "text": "Sign in to view more jobs" if is_search else "Python role"}

    monkeypatch.setattr(linkedin, "_page_snapshot", snapshot)
    result = asyncio.run(linkedin.search_linkedin("Python"))
    assert result["requires_action"] is False
    assert result["jobs"][0]["description"] == RAW_JOB["description"]


def test_linkedin_launch_uses_private_persistent_workspace_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    browser = SimpleNamespace(is_connected=lambda: True)
    context = SimpleNamespace(browser=browser, route=AsyncMock())
    manager = SimpleNamespace(__aenter__=AsyncMock(return_value=context), __aexit__=AsyncMock())
    factory = Mock(return_value=manager)
    package = ModuleType("camoufox")
    package.__path__ = []
    module = ModuleType("camoufox.async_api")
    module.AsyncCamoufox = factory
    monkeypatch.setitem(sys.modules, "camoufox", package)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", module)

    launched_manager, launched_browser, launched_context = asyncio.run(linkedin._launch(headless=True))
    assert launched_manager is manager
    assert launched_context is context
    assert launched_browser is browser
    options = factory.call_args.kwargs
    assert options["persistent_context"] is True
    assert options["headless"] is True
    assert options["service_workers"] == "block"
    assert options["accept_downloads"] is False
    profile = Path(options["user_data_dir"])
    assert profile == tmp_path / "linkedin-browser-profile"
    assert profile.is_dir()
    assert profile.stat().st_mode & 0o777 == 0o700
    context.route.assert_awaited_once()


def test_closing_linkedin_session_retains_persistent_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    profile = tmp_path / "linkedin-browser-profile"
    profile.mkdir()
    sentinel = profile / "synthetic-state.txt"
    sentinel.write_text("Test browser state; no account credentials.")
    manager = SimpleNamespace(__aexit__=AsyncMock())
    monkeypatch.setattr(linkedin, "_session", {"manager": manager})
    monkeypatch.setattr(linkedin, "_cleanup_tasks", set())
    asyncio.run(linkedin.close_linkedin())
    manager.__aexit__.assert_awaited_once()
    assert linkedin._session is None
    assert sentinel.read_text() == "Test browser state; no account credentials."


@pytest.mark.skipif(os.environ.get("PUTMETO_RUN_PERSISTENCE_TESTS") != "1", reason="Optional Camoufox restart check with a synthetic cookie; set PUTMETO_RUN_PERSISTENCE_TESTS=1")
def test_persistent_camoufox_profile_retains_cookie_and_local_storage_after_restart(tmp_path, monkeypatch):
    pytest.importorskip("camoufox.async_api")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)

    async def restart_check():
        async def offline_page(route):
            if route.request.url == "https://example.com/putmeto-profile-test":
                await route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Synthetic profile persistence test</title>")
            else:
                await route.abort()

        manager, _, context = await linkedin._launch(headless=True)
        try:
            await context.route("**/*", offline_page)
            await context.add_cookies([{
                "name": "putmeto_test_cookie", "value": "synthetic-test-value", "domain": "example.com",
                "path": "/", "expires": int(time.time()) + 3600, "secure": True, "httpOnly": True,
            }])
            page = await context.new_page()
            await page.goto("https://example.com/putmeto-profile-test")
            await page.evaluate("localStorage.setItem('putmeto_test_storage', 'synthetic-local-value')")
        finally:
            await manager.__aexit__(None, None, None)
        manager, _, context = await linkedin._launch(headless=True)
        try:
            await context.route("**/*", offline_page)
            cookies = await context.cookies("https://example.com")
            assert any(cookie["name"] == "putmeto_test_cookie" and cookie["value"] == "synthetic-test-value" for cookie in cookies)
            page = await context.new_page()
            await page.goto("https://example.com/putmeto-profile-test")
            assert await page.evaluate("localStorage.getItem('putmeto_test_storage')") == "synthetic-local-value"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(restart_check())


@pytest.mark.skipif(os.environ.get("PUTMETO_RUN_DOM_TESTS") != "1", reason="Optional offline Chrome DOM fixtures; set PUTMETO_RUN_DOM_TESTS=1")
@pytest.mark.parametrize("fixture,expected_title,expected_company,expected_description", [
    ("""<article class="base-search-card"><a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/guest-python-engineer-9000000001/?trk=guest"></a><h3 class="base-search-card__title">Guest Python Engineer</h3><h4 class="base-search-card__subtitle">Guest Company</h4><span class="job-search-card__location">Boston (Remote)</span><time datetime="2026-09-27">Today</time></article><h2>Sign in to view more jobs</h2><div class="show-more-less-html__markup">Build Python APIs with SQL.</div>""", "Guest Python Engineer", "Guest Company", "Build Python APIs with SQL."),
    ("""<li class="jobs-search-results__list-item" data-job-id="9000000002"><div class="job-card-container"><a class="job-card-list__title--link" href="https://www.linkedin.com/jobs/view/9000000002?trackingId=member">Member Research Engineer</a><div class="artdeco-entity-lockup__subtitle">Member Company</div><div class="job-card-container__metadata-item">New York, NY</div></div></li><div class="jobs-description__content">Develop reproducible research software.</div>""", "Member Research Engineer", "Member Company", "Develop reproducible research software."),
])
def test_dom_readers_parse_guest_and_member_cards_offline(fixture, expected_title, expected_company, expected_description):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    options = {"headless": True, **({"executable_path": str(chrome)} if chrome.exists() else {"channel": "chrome"})}
    with playwright.sync_playwright() as runner:
        browser = runner.chromium.launch(**options)
        try:
            context = browser.new_context()
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(fixture)
            raw_cards = page.evaluate(linkedin.EXTRACT_CARDS_JS)
            unique = {entry["url"]: entry for entry in (linkedin.normalize_job(raw) for raw in raw_cards)}
            assert len(unique) == 1
            card = next(iter(unique.values()))
            assert card["title"] == expected_title
            assert card["company"] == expected_company
            assert page.evaluate(linkedin.EXTRACT_DETAIL_JS)["description"] == expected_description
        finally:
            browser.close()


def test_linkedin_search_can_preview_without_persisting(workspace, monkeypatch):
    search = AsyncMock(return_value=search_result(RAW_JOB))
    monkeypatch.setattr(jobs, "search_linkedin", search)
    before = db.get_state()
    response = workspace.post("/api/linkedin/search", json={"keywords": "Python engineer", "location": "Boston", "remote_only": True, "limit": 5, "save": False})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["found"] == 1
    assert result["count"] == 0
    assert result["jobs"][0]["title"] == RAW_JOB["title"]
    assert result["requires_action"] is False
    assert db.get_state() == before
    search.assert_awaited_once_with("Python engineer", location="Boston", remote_only=True, limit=5)


def test_search_saves_deduplicated_numeric_ids_and_preserves_application_state(workspace, monkeypatch):
    db.mutate_state(lambda state: state.update(
        positions=[{"id": "position", "name": "Software Engineer", "confirmed": True}],
        skills=[{"id": "skill", "name": "Python", "confirmed": True}],
    ))
    duplicate = {**RAW_JOB, "url": "https://www.linkedin.com/jobs/view/9000000001?refId=other"}
    search = AsyncMock(return_value=search_result(RAW_JOB, duplicate))
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"keywords": "Software engineer", "location": "United States"})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    saved = db.get_state()["jobs"]
    assert len(saved) == 1
    assert saved[0]["source"] == "LinkedIn"
    assert saved[0]["matched_skills"] == ["Python"]
    job_id = saved[0]["id"]
    assert workspace.patch(f"/api/jobs/{job_id}", json={"status": "applied"}).status_code == 200
    repeat = workspace.post("/api/linkedin/search", json={"keywords": "Software engineer", "location": "United States"})
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["count"] == 0
    assert db.get_state()["jobs"][0]["id"] == job_id
    assert db.get_state()["jobs"][0]["status"] == "applied"


@pytest.mark.parametrize("initial_status,expected_status", [("saved", "saved"), ("prepared", "saved"), ("applied", "applied")])
def test_completed_search_enriches_partial_card_preserving_identity_and_history(workspace, monkeypatch, initial_status, expected_status):
    db.mutate_state(lambda state: state.update(
        positions=[{"id": "role", "name": "Software Engineer", "confirmed": True}],
        skills=[{"id": "python", "name": "Python", "confirmed": True}, {"id": "sql", "name": "SQL", "confirmed": True}],
    ))
    partial = {**RAW_JOB, "description": "", "company": "Observed Company", "location": "", "remote": False}
    complete = {**RAW_JOB, "url": "https://www.linkedin.com/jobs/view/9000000001/", "salary": "USD 100,000–130,000"}
    search = AsyncMock(side_effect=[search_result(partial, requires_action=True), search_result(complete)])
    monkeypatch.setattr(jobs, "search_linkedin", search)
    first = workspace.post("/api/linkedin/search", json={"keywords": "Software engineer", "location": "United States"})
    assert first.status_code == 200, first.text
    assert first.json()["requires_action"] is True
    assert first.json()["count"] == 1
    original = db.get_state()["jobs"][0]
    assert original["description"] == ""
    assert original["matched_skills"] == ["Python"]
    historical_resume = {"profile": {"name": "Ada Applicant"}, "items": [], "skills": ["Python"], "created_at": "2026-09-27T00:00:00Z"}
    db.mutate_state(lambda state: state["jobs"][0].update(status=initial_status, resume=historical_resume))

    retry = workspace.post("/api/linkedin/search", json={"keywords": "Software engineer", "location": "United States"})
    assert retry.status_code == 200, retry.text
    assert retry.json()["requires_action"] is False
    assert retry.json()["count"] == 0
    assert retry.json()["found"] == 1
    stored_jobs = db.get_state()["jobs"]
    assert len(stored_jobs) == 1
    enriched = stored_jobs[0]
    assert enriched["id"] == original["id"]
    assert enriched["created_at"] == original["created_at"]
    assert enriched["description"] == complete["description"]
    assert enriched["company"] == "Observed Company"
    assert enriched["location"] == complete["location"]
    assert enriched["salary"] == complete["salary"]
    assert enriched["remote"] is True
    assert enriched["matched_skills"] == ["Python", "SQL"]
    assert enriched["match_score"] > original["match_score"]
    assert enriched["status"] == expected_status
    if initial_status == "applied":
        assert enriched["resume"] == historical_resume
    else:
        assert "resume" not in enriched
    assert retry.json()["jobs"][0] == enriched
    assert search.await_count == 2


def test_signin_required_response_never_invents_or_saves_jobs(workspace, monkeypatch):
    search = AsyncMock(return_value=search_result(requires_action=True))
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"keywords": "Software engineer"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["requires_action"] is True
    assert result["jobs"] == []
    assert result["count"] == result["found"] == 0
    assert "sign-in" in result["message"]
    assert not db.get_state()["jobs"]


def test_browser_failure_returns_actionable_error_without_persisting(workspace, monkeypatch):
    monkeypatch.setattr(jobs, "search_linkedin", AsyncMock(side_effect=SourceError("Camoufox could not start. Install browser support first.")))
    response = workspace.post("/api/linkedin/search", json={"keywords": "Python"})
    assert response.status_code == 503
    assert "Install browser support" in response.json()["detail"]
    assert not db.get_state()["jobs"]


def test_incomplete_linkedin_result_is_skipped_without_losing_valid_results(workspace, monkeypatch):
    invalid = {"title": "Injected listing", "url": "https://attacker.example/jobs/123"}
    monkeypatch.setattr(jobs, "search_linkedin", AsyncMock(return_value=search_result(invalid, RAW_JOB)))
    response = workspace.post("/api/linkedin/search", json={"keywords": "Python"})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == response.json()["found"] == 1
    assert response.json()["warnings"]
    assert len(db.get_state()["jobs"]) == 1
    assert db.get_state()["jobs"][0]["title"] == RAW_JOB["title"]


@pytest.mark.parametrize("changes", [
    {"limit": 0}, {"limit": 26}, {"regions": []}, {"regions": [""]},
    {"regions": ["   "]}, {"regions": ["United States"] * 6},
])
def test_search_validates_input_before_opening_browser(workspace, monkeypatch, changes):
    search = AsyncMock()
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"keywords": "Python", **changes})
    assert response.status_code == 422, response.text
    search.assert_not_awaited()


def test_linkedin_source_can_be_configured_without_arbitrary_url(workspace):
    response = workspace.put("/api/sources", json={"items": [{"id": "linkedin", "name": "LinkedIn", "kind": "linkedin", "url": "", "enabled": True}]})
    assert response.status_code == 200, response.text
    source = db.get_state()["sources"][0]
    assert source["kind"] == "linkedin"
    assert urlsplit(source["url"]).hostname == "www.linkedin.com"


def test_legacy_workspace_gets_one_disabled_linkedin_source(workspace):
    previous = {"id": "custom", "name": "My company", "kind": "lever", "url": "https://jobs.lever.co/example", "enabled": True}
    db.mutate_state(lambda state: state.update(schema_version=1, sources=[previous]))
    state = workspace.get("/api/state").json()
    assert next(source for source in state["sources"] if source["id"] == "custom") == previous
    linkedin_sources = [source for source in state["sources"] if source["kind"] == "linkedin"]
    assert len(linkedin_sources) == 1
    assert linkedin_sources[0]["enabled"] is False
    again = workspace.get("/api/state").json()
    assert len([source for source in again["sources"] if source["kind"] == "linkedin"]) == 1


def test_removed_linkedin_source_stays_removed_after_initial_upgrade(workspace):
    db.mutate_state(lambda state: state.update(sources=[source for source in state["sources"] if source["kind"] != "linkedin"]))
    db.initialize()
    assert all(source["kind"] != "linkedin" for source in workspace.get("/api/state").json()["sources"])


@pytest.mark.parametrize("positions", [[], [{"id": "reviewed", "name": "Software Engineer", "confirmed": True}]])
def test_linkedin_discovery_ignores_role_and_location_preferences(workspace, monkeypatch, positions):
    db.mutate_state(lambda state: state.update(
        positions=positions,
        skills=[{"id": "python", "name": "Python", "confirmed": True}],
        preferences={"track": "industry", "location": "Boston", "remote_only": True},
        sources=[{"id": "linkedin", "name": "LinkedIn", "kind": "linkedin", "url": "https://www.linkedin.com/jobs/search/", "enabled": True}],
    ))
    job = {**RAW_JOB, "title": "Office administrator", "location": "Paris, France", "description": "Manage office supplies and visitor reception.", "remote": False}
    search = AsyncMock(return_value=search_result(job))
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/jobs/discover")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert search.await_args_list == [
        call("", location="United States", remote_only=False, limit=10),
        call("", location="Europe", remote_only=False, limit=10),
    ]
    stored = db.get_state()["jobs"][0]
    assert stored["source"] == "LinkedIn"
    assert stored["title"] == "Office administrator"
    assert stored["matched_skills"] == []


@pytest.mark.parametrize("payload", [{}, {"keywords": ""}, {"keywords": "   "}])
def test_default_search_covers_us_and_europe_with_all_titles(workspace, monkeypatch, payload):
    db.mutate_state(lambda state: state.update(
        positions=[{"id": "p", "name": "Software engineer", "confirmed": True}],
        preferences={"track": "industry", "location": "Boston", "remote_only": True},
    ))

    async def empty_result(keywords, *, location, remote_only, limit):
        return {**search_result(), "search_url": linkedin.build_search_url(keywords, location, remote_only)}

    search = AsyncMock(side_effect=empty_result)
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json=payload)
    assert response.status_code == 200, response.text
    assert search.await_args_list == [
        call("", location="United States", remote_only=False, limit=10),
        call("", location="Europe", remote_only=False, limit=10),
    ]
    result = response.json()
    assert result["found"] == result["count"] == 0
    assert [parse_qs(urlsplit(url).query)["location"][0] for url in result["search_urls"]] == ["United States", "Europe"]


def test_regions_override_single_location_and_deduplicate_across_regions(workspace, monkeypatch):
    common = {**RAW_JOB, "url": "https://www.linkedin.com/jobs/view/9000000001/"}

    async def regional_result(keywords, *, location, remote_only, limit):
        suffix = "9000000002" if location == "Canada" else "9000000003"
        job = {**RAW_JOB, "title": "Regional position", "url": f"https://www.linkedin.com/jobs/view/{suffix}/", "location": location}
        return {**search_result(common, job), "search_url": linkedin.build_search_url(keywords, location, remote_only)}

    search = AsyncMock(side_effect=regional_result)
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"regions": ["Canada", "Germany"], "location": "Boston", "limit": 2, "save": False})
    assert response.status_code == 200, response.text
    assert search.await_args_list == [call("", location=region, remote_only=False, limit=2) for region in ["Canada", "Germany"]]
    result = response.json()
    assert result["found"] == 3
    assert result["count"] == 0
    assert len({job["url"] for job in result["jobs"]}) == 3
    assert len(result["search_urls"]) == 2
    assert db.get_state()["jobs"] == []


def test_gate_stops_remaining_regions_and_keeps_observed_cards(workspace, monkeypatch):
    first = {**RAW_JOB, "url": "https://www.linkedin.com/jobs/view/9000000001/"}
    gated = {**RAW_JOB, "url": "https://www.linkedin.com/jobs/view/9000000002/", "description": ""}
    search = AsyncMock(side_effect=[
        {**search_result(first), "search_url": "https://www.linkedin.com/jobs/search/?location=United+States"},
        {**search_result(gated, requires_action=True), "search_url": "https://www.linkedin.com/jobs/search/?location=Europe"},
    ])
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"regions": ["United States", "Europe", "Canada"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["requires_action"] is True
    assert result["found"] == result["count"] == 2
    assert "sign-in" in result["message"]
    assert len(result["search_urls"]) == 2
    assert search.await_count == 2
    assert db.get_state()["jobs"][1]["description"] == ""


def test_region_limits_bound_total_jobs_to_125(workspace, monkeypatch):
    regions = ["United States", "Europe", "Canada", "Australia", "Japan"]

    async def overfull_result(keywords, *, location, remote_only, limit):
        offset = regions.index(location) * 100
        entries = [{**RAW_JOB, "url": f"https://www.linkedin.com/jobs/view/{8999999944 + offset + index}/"} for index in range(26)]
        return {**search_result(*entries), "search_url": linkedin.build_search_url(keywords, location, remote_only)}

    search = AsyncMock(side_effect=overfull_result)
    monkeypatch.setattr(jobs, "search_linkedin", search)
    response = workspace.post("/api/linkedin/search", json={"regions": regions, "limit": 25})
    assert response.status_code == 200, response.text
    assert response.json()["found"] == response.json()["count"] == 125
    assert len(db.get_state()["jobs"]) == 125
    assert search.await_count == 5
    assert all(arguments.kwargs["limit"] == 25 for arguments in search.await_args_list)
