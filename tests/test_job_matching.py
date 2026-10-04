"""Jobs between 30% and 100% are matched to the master resume and get a tailored resume."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend import ai, db, job_matching


def job(job_id, score, status="discovered", description="Build agentic AI systems with Python.", **extra):
    return {"id": job_id, "title": f"Role {job_id}", "company": "Example", "location": "Remote", "url": f"https://example.com/{job_id}",
            "source": "LinkedIn", "description": description, "status": status, "match_score": score, "matched_skills": [], **extra}


@pytest.fixture
def workspace(monkeypatch):
    db.initialize()
    db.mutate_state(lambda state: state.update(
        items=[{"id": "exp", "kind": "experience", "title": "AI Specialist", "original": "• Built agents.", "enhanced": "", "confirmed": True}],
        skills=[{"id": "py", "name": "Python", "confirmed": True}],
        jobs=[job("in-range", 67), job("perfect", 100), job("low", 30), job("archived", 70, status="archived"),
              job("applied", 70, status="applied"), job("no-description", 70, description=" "), job("saved", 31, status="saved")],
    ))
    calls = {"match": [], "order": []}

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def match_job(settings, state, job, effort=None, timeout=None):
        calls["match"].append((job["id"], settings["model"], effort))
        if job["id"] == "broken":
            raise ai.AIError("The model was busy.")
        return ai.JobMatch(score=88, summary="Strong fit.", strengths=["Agents (AI Specialist)."], gaps=["No Kubernetes."])

    async def order_resume(settings, state, job, effort=None, timeout=None):
        calls["order"].append((job["id"], effort))
        return ai.ResumeOrder(item_ids=["exp"], skill_ids=["py"])

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "match_job", match_job)
    monkeypatch.setattr(ai, "order_resume", order_resume)
    return calls


def jobs():
    return {item["id"]: item for item in db.get_state()["jobs"]}


def test_only_jobs_above_30_and_below_100_with_a_description_are_matched(workspace):
    assert sorted(job_matching.pending(db.get_state())) == ["in-range", "saved"]


def test_each_job_gets_a_master_cv_match_and_a_tailored_resume_at_high_reasoning(workspace):
    asyncio.run(job_matching._work())
    assert sorted(workspace["match"]) == [("in-range", "gpt-6-astra", "high"), ("saved", "gpt-6-astra", "high")]
    assert sorted(workspace["order"]) == [("in-range", "high"), ("saved", "high")]
    matched = jobs()["in-range"]
    assert matched["ai_match"]["score"] == 88 and matched["ai_match"]["gaps"] == ["No Kubernetes."]
    assert matched["ai_match"]["model"] == "gpt-6-astra" and matched["ai_match"]["effort"] == "high"
    assert matched["resume"]["items"][0]["title"] == "AI Specialist"
    assert matched["status"] == "discovered" and jobs()["saved"]["status"] == "saved", "Matching never changes a job's status."
    assert "ai_match" not in jobs()["perfect"] and "resume" not in jobs()["low"]
    assert job_matching.pending(db.get_state()) == []
    assert "Matched 2 jobs" in job_matching.status()["message"]


def test_a_job_is_matched_again_only_when_it_or_the_master_resume_changes(workspace):
    asyncio.run(job_matching._work())
    assert job_matching.pending(db.get_state()) == []
    db.mutate_state(lambda state: next(item for item in state["jobs"] if item["id"] == "in-range").update(description="Now also Kubernetes."))
    assert job_matching.pending(db.get_state()) == ["in-range"]
    asyncio.run(job_matching._work())
    db.mutate_state(lambda state: state["items"][0].update(original="• Built agents and led a team."))
    assert sorted(job_matching.pending(db.get_state())) == ["in-range", "saved"], "A new master resume means new matches."


def test_one_failure_does_not_stop_the_others_and_is_retried_later(workspace, monkeypatch):
    db.mutate_state(lambda state: state["jobs"].insert(0, job("broken", 50)))
    asyncio.run(job_matching._work())
    current = jobs()
    assert current["broken"]["match_error"]["message"] == "The model was busy."
    assert "ai_match" in current["in-range"] and "ai_match" in current["saved"]
    assert "broken" not in job_matching.pending(db.get_state()), "A failed job waits before another try."
    later = time.time() + job_matching.RETRY_AFTER + 1
    monkeypatch.setattr(job_matching.time, "time", lambda: later)
    assert job_matching.pending(db.get_state()) == ["broken"]


def test_nothing_runs_before_there_is_a_master_resume(workspace):
    db.mutate_state(lambda state: state["items"][0].update(confirmed=False))
    asyncio.run(job_matching._work())
    assert workspace["match"] == []
    assert job_matching.status()["waiting"] == 0


def test_match_now_starts_matching(workspace, monkeypatch):
    from backend.main import app
    started = []
    monkeypatch.setattr(job_matching, "kick", lambda: started.append(True))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/api/job-matching/start")
        assert response.status_code == 200 and response.json()["waiting"] == 2
    assert started == [True]


def test_match_request_uses_the_master_resume_and_the_job(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(instructions=instructions, data=data, output_type=output_type, effort=effort)
        return output_type(score=70, summary="Partial fit.", strengths=["Python."], gaps=["Go."])

    monkeypatch.setattr(ai, "generate", generate)
    state = {"profile": {"headline": "AI engineer"}, "items": [{"kind": "experience", "title": "AI Specialist", "original": "• Built agents.", "confirmed": True}],
             "skills": [{"name": "Python", "confirmed": True}]}
    result = asyncio.run(ai.match_job({}, state, job("x", 50), effort="high"))
    assert result.score == 70 and seen["output_type"] is ai.JobMatch and seen["effort"] == "high"
    assert seen["data"]["job"]["description"] == "Build agentic AI systems with Python."
    assert seen["data"]["master_resume"]["entries"][0]["title"] == "AI Specialist"
    assert "never assume experience" in seen["instructions"]
