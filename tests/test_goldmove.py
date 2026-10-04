"""Goldmove gathers the keywords jobs above 30% still miss, and adds only the ones you confirm."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend import ai, ats, auto_apply, db, goldmove, job_matching, resumes


def term(name, kind="skill", importance="required"):
    return ai.ATSTerm(term=name, kind=kind, importance=importance, variants=[])


POSTINGS = {
    "a": [term("Python"), term("System design"), term("Kubernetes", "tool"), term("Staff Engineer", "title")],
    "b": [term("Python"), term("System design", importance="preferred"), term("PhD", "degree")],
    "low": [term("Rust")],
}


@pytest.fixture
def workspace(monkeypatch):
    db.initialize()

    def seed(state):
        state["profile"].update(name="Alex Morgan", email="alex@example.com")
        state["items"] = [{"id": "exp", "kind": "experience", "title": "AI Engineer", "original": "• Built Python services.", "confirmed": True}]
        state["skills"] = [{"id": "py", "name": "Python", "confirmed": True, "origin": "resume"}]
        jobs = []
        for job_id, score, status in (("a", 82, "discovered"), ("b", 45, "saved"), ("low", 25, "discovered"), ("sent", 90, "applied")):
            job = {"id": job_id, "title": f"Role {job_id}", "company": "Example", "url": f"https://example.com/{job_id}",
                   "description": f"Posting {job_id}.", "status": status, "match_score": score}
            job["resume"] = resumes.build_resume(state, job)
            jobs.append(job)
        state["jobs"] = jobs
    db.mutate_state(seed)
    asked = []

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def ats_terms(settings, job, effort=None, timeout=None):
        asked.append(job["id"])
        return POSTINGS.get(job["id"], [])

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "ats_terms", ats_terms)
    return asked


def candidates():
    return {item["term"]: item for item in goldmove.status()["candidates"]}


def test_only_jobs_that_started_above_30_percent_are_checked_and_nothing_is_added_by_itself(workspace):
    before = db.get_state()["jobs"]
    asyncio.run(goldmove._work())
    assert sorted(workspace) == ["a", "b", "low"], "Every job gets an ATS match; the job already applied for is left alone."
    found = candidates()
    assert list(found) == ["System design", "Kubernetes"], "Most asked-for first; titles and degrees are never offered."
    assert len(found["System design"]["jobs"]) == 2 and found["System design"]["required"] == 1
    assert [job["resume"] for job in db.get_state()["jobs"]] == [job["resume"] for job in before]
    assert "Rust" not in found, "Keywords from the 25% job aren't offered."
    assert goldmove.pending(db.get_state()) == [] and goldmove.status()["checked"] == 2 and goldmove.status()["scored"] == 3


def test_confirmed_keywords_join_your_skills_and_every_resume_not_yet_sent(workspace):
    asyncio.run(goldmove._work())
    state = db.get_state()
    key = job_matching.resume_key(state)
    added = goldmove.confirm(["System design"])
    assert added == ["System design"]
    state = db.get_state()
    skill = next(item for item in state["skills"] if item["name"] == "System design")
    assert skill["confirmed"] and skill["origin"] == "goldmove"
    resumes_by_job = {job["id"]: job["resume"]["skills"] for job in state["jobs"]}
    assert resumes_by_job["a"] == resumes_by_job["b"] == ["Python", "System design"]
    assert resumes_by_job["sent"] == ["Python"], "A resume already sent stays as it was."
    assert job_matching.resume_key(state) == key, "Your job matches stay current; nothing is matched again."
    assert "System design" not in candidates()
    asyncio.run(goldmove._work())  # The changed resumes are checked again.
    report = ats.report("a")
    assert {item["term"]: item["status"] for item in report["terms"]}["System design"] == "present"


def test_dismissed_keywords_are_not_offered_again_until_you_ask(workspace):
    asyncio.run(goldmove._work())
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/api/goldmove/dismiss", json={"terms": ["Kubernetes"]})
        assert response.status_code == 200 and [item["term"] for item in response.json()["candidates"]] == ["System design"]
        assert client.post("/api/goldmove/dismiss", json={"terms": []}).status_code == 422
        assert client.post("/api/goldmove/dismissed/clear").json()["dismissed"] == 0
        confirmed = client.post("/api/goldmove/confirm", json={"terms": ["Kubernetes", "System design"]})
        assert confirmed.status_code == 200 and "Added 2 skills" in confirmed.json()["message"]
        assert client.put("/api/skills", json={"items": client.get("/api/state").json()["skills"]}).status_code == 200, \
            "Goldmove skills round-trip through the skills editor."
    assert db.get_state()["goldmove"]["dismissed"] == []


def test_auto_apply_keeps_going_after_you_confirm_keywords(workspace):
    def matched(state):
        for job in state["jobs"]:
            job["ai_match"] = {"score": 82, "basis": job_matching.basis(job_matching.resume_key(state), job)}
    db.mutate_state(matched)
    eligible = [job["id"] for job in db.get_state()["jobs"] if auto_apply.eligible(job, db.get_state())]
    goldmove.confirm(["System design"])
    assert [job["id"] for job in db.get_state()["jobs"] if auto_apply.eligible(job, db.get_state())] == eligible


def test_a_job_that_cannot_be_checked_waits_before_another_try(workspace, monkeypatch):
    async def broken(*args, **kwargs):
        raise ai.AIError("The model was busy.")

    monkeypatch.setattr(ai, "ats_terms", broken)
    asyncio.run(goldmove._work())
    assert goldmove.pending(db.get_state()) == []
    assert db.get_state()["jobs"][0]["ats"]["failed_at"] > 0
