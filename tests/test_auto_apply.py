"""Auto-apply sends applications for jobs at 70%+ with only the user's data, and stops to ask instead of guessing."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import ai, apply_agent, auto_apply, codex_bridge, db, job_matching


def job(job_id, ai_score=None, status="discovered", resume=True, **extra):
    value = {"id": job_id, "title": f"Role {job_id}", "company": "Example", "url": f"https://example.com/{job_id}", "source": "LinkedIn",
             "description": "Build agentic AI systems.", "status": status, "match_score": 60, **extra}
    if resume:
        value["resume"] = {"profile": {"name": "Sam Lee"}, "items": [
            {"id": "exp", "kind": "experience", "title": "AI Specialist", "organization": "Clemson University",
             "original": "• Built agents.", "enhanced": "", "confirmed": True}], "skills": ["Python"]}
    return value


def matched(state, value, ai_score):
    """Give a job a current master-CV match."""
    value["ai_match"] = {"score": ai_score, "basis": job_matching.basis(job_matching.resume_key(state), value)}
    return value


@pytest.fixture
def workspace():
    db.initialize()

    def seed(state):
        state["profile"].update(name="Sam Lee", email="sam@example.com", phone="555-0100")
        state["items"] = [{"id": "exp", "kind": "experience", "title": "AI Specialist", "original": "• Built agents on Kubernetes.", "enhanced": "", "confirmed": True}]
        state["skills"] = [{"id": "py", "name": "Python", "confirmed": True}]
        state["jobs"] = [matched(state, job("good"), 82), matched(state, job("best"), 95), matched(state, job("low"), 69),
                         matched(state, job("no-resume", resume=False), 90), matched(state, job("done", status="applied"), 90),
                         matched(state, job("archived", status="archived"), 90), job("unmatched")]
        state["auto_apply"] = {"enabled": True}
    db.mutate_state(seed)


def applications():
    return {item["job_id"]: item for item in db.get_state()["applications"]}


def test_only_current_matches_of_70_or_more_with_a_tailored_resume_are_applied_to_best_first(workspace):
    assert auto_apply.pending(db.get_state()) == ["best", "good"]
    db.mutate_state(lambda state: state["items"][0].update(original="• Built agents and led a team on Kubernetes."))
    assert auto_apply.pending(db.get_state()) == [], "A match made from an older master resume is not used."


def test_applications_you_started_yourself_are_left_to_you(workspace):
    db.mutate_state(lambda state: state["applications"].append({"id": "mine", "job_id": "best", "status": "in_progress"}))
    assert auto_apply.pending(db.get_state()) == ["good"]


def test_the_agent_gets_only_your_profile_answers_and_tailored_resume(workspace):
    db.mutate_state(lambda state: state.update(application_answers={"sponsorship": "No", "salary": "", "other": [
        {"question": "Years of Rust?", "answer": "2"}]}))
    state = db.get_state()
    data = auto_apply.applicant(state, next(item for item in state["jobs"] if item["id"] == "best"))
    assert data["profile"] == {"name": "Sam Lee", "email": "sam@example.com", "phone": "555-0100"}
    assert data["answers"] == {auto_apply.ANSWERS["sponsorship"]: "No"}, "Empty answers are left out, so the agent asks."
    assert data["other_answers"] == [{"question": "Years of Rust?", "answer": "2"}]
    assert data["resume"]["entries"][0]["text"] == "• Built agents." and data["resume"]["skills"] == ["Python"]


@pytest.fixture
def agent(workspace, monkeypatch):
    """A stand-in browser agent that ends each job the way the test says."""
    calls, outcomes = [], {}
    terms = [ai.ATSTerm(term="Python", kind="skill", importance="required", variants=[]),
             ai.ATSTerm(term="Kubernetes", kind="tool", importance="required", variants=[])]

    async def ats_terms(settings, job, effort=None, timeout=None):
        if job["id"] in outcomes.get("ats_fails", ()):
            raise ai.AIError("The model was busy.")
        return terms + outcomes.get("ats_extra", [])

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def apply(job, applicant, resume_pdf, model, should_stop=None, on_step=None):
        assert resume_pdf.exists() and resume_pdf.read_bytes().startswith(b"%PDF"), "The tailored resume is uploaded as a PDF."
        assert resume_pdf.name == "Sam-Lee-Resume.pdf"
        calls.append({"job": job["id"], "model": model, "applicant": applicant, "pdf": resume_pdf, "skills": list(job["resume"]["skills"])})
        result = outcomes.get(job["id"], ("submitted", "Reference APP-1"))
        if isinstance(result, Exception):
            raise result
        return apply_agent.Outcome(*result, url=job["url"])

    async def nap(seconds):
        db.mutate_state(lambda state: state["auto_apply"].update(enabled=False))  # Nothing left: end the run.

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "ats_terms", ats_terms)
    monkeypatch.setattr(apply_agent, "apply", apply)
    monkeypatch.setattr(auto_apply, "blocker", lambda state: "")
    monkeypatch.setattr(auto_apply, "_nap", nap)
    monkeypatch.setattr(auto_apply, "GAP", (0, 0))
    return calls, outcomes


def run():
    asyncio.run(auto_apply._work())


def test_submitted_applications_are_recorded_and_the_resume_file_is_removed(agent):
    calls, _ = agent
    run()
    assert [call["job"] for call in calls] == ["best", "good"] and calls[0]["model"] == "gpt-6-astra"
    assert all(not call["pdf"].exists() for call in calls), "The resume file is deleted after each application."
    best = applications()["best"]
    assert best["status"] == "applied" and best["automated"] and "Reference APP-1" in best["note"]
    assert {item["id"]: item["status"] for item in db.get_state()["jobs"]}["best"] == "applied"
    assert auto_apply.pending(db.get_state()) == []


def test_keywords_your_cv_has_are_added_to_the_resume_before_applying(agent):
    calls, _ = agent
    run()
    assert calls[0]["skills"] == ["Python", "Kubernetes"], "Kubernetes is in your CV but wasn't in the tailored resume."
    assert db.get_state()["jobs"][1]["resume"]["added_keywords"] == ["Kubernetes"]
    assert auto_apply.status()["below_ats"] == 0


def test_jobs_below_your_ats_minimum_are_skipped_without_applying(agent):
    calls, outcomes = agent
    outcomes["ats_extra"] = [ai.ATSTerm(term="Rust", kind="skill", importance="required", variants=[])]
    db.mutate_state(lambda state: state["auto_apply"].update(min_ats=90))
    run()
    assert calls == [] and applications() == {}, "Python and Kubernetes but no Rust: 67% is below 90%."
    assert auto_apply.pending(db.get_state()) == [] and auto_apply.status()["below_ats"] == 2
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        saved = client.put("/api/auto-apply/rules", json={"min_ats": 60})
        assert saved.status_code == 200 and saved.json()["min_ats"] == 60
        assert client.put("/api/auto-apply/rules", json={"min_ats": 101}).status_code == 422
    assert auto_apply.pending(db.get_state()) == ["best", "good"]


def test_a_failed_ats_check_does_not_hold_up_applying_without_a_minimum(agent):
    calls, outcomes = agent
    outcomes["ats_fails"] = ("best",)
    run()
    assert [call["job"] for call in calls] == ["best", "good"]
    assert db.get_state()["jobs"][1]["ats"]["failed_at"] > 0


def test_a_question_it_cannot_answer_waits_for_your_answer_then_is_tried_again(agent):
    calls, outcomes = agent
    outcomes["best"] = ("needs_input", "Years of professional Rust experience (required)")
    run()
    waiting = applications()["best"]
    assert waiting["status"] == "needs_input" and waiting["question"] == "Years of professional Rust experience (required)"
    assert applications()["good"]["status"] == "applied", "One question doesn't hold up the other jobs."
    assert auto_apply.pending(db.get_state()) == []
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(f"/api/auto-apply/applications/{waiting['id']}/answer", json={"answer": "2 years"})
        assert response.status_code == 200
    assert db.get_state()["application_answers"]["other"] == [{"question": "Years of professional Rust experience (required)", "answer": "2 years"}]
    assert auto_apply.pending(db.get_state()) == ["best"]
    outcomes["best"] = ("submitted", "Done.")
    db.mutate_state(lambda state: state["auto_apply"].update(enabled=True))
    run()
    assert calls[-1]["applicant"]["other_answers"] == [{"question": "Years of professional Rust experience (required)", "answer": "2 years"}]
    assert applications()["best"]["status"] == "applied" and applications()["best"]["attempts"] == 2


def test_a_site_that_needs_a_sign_in_is_tried_again_after_you_open_the_application_browser(agent):
    _, outcomes = agent
    outcomes["best"] = ("needs_sign_in", "linkedin.com: Sign in to use Easy Apply")
    run()
    assert applications()["best"]["status"] == "needs_sign_in" and auto_apply.pending(db.get_state()) == []
    db.mutate_state(lambda state: state["auto_apply"].update(signed_in_at=time.time() + 1))
    assert auto_apply.pending(db.get_state()) == ["best"]


def test_failures_are_retried_later_a_few_times_but_interrupted_ones_wait_for_you(agent, monkeypatch):
    _, outcomes = agent
    outcomes["best"] = RuntimeError("Chrome could not start.")
    outcomes["good"] = ("stopped", "Stopped before finishing.")
    run()
    failed, stopped = applications()["best"], applications()["good"]
    assert failed["status"] == "failed" and "Chrome could not start." in failed["note"] and not failed.get("manual_retry")
    assert stopped["status"] == "failed" and stopped["manual_retry"] and "Try again" in stopped["note"]
    later = time.time() + auto_apply.RETRY_AFTER + 1
    monkeypatch.setattr(auto_apply.time, "time", lambda: later)
    assert auto_apply.pending(db.get_state()) == ["best"], "Only the failure is retried by itself; a stopped one may have been sent."
    db.mutate_state(lambda state: next(item for item in state["applications"] if item["job_id"] == "best").update(attempts=auto_apply.MAX_ATTEMPTS))
    assert auto_apply.pending(db.get_state()) == []


def test_an_application_left_mid_way_by_a_restart_is_not_sent_again_by_itself(agent):
    db.mutate_state(lambda state: state["applications"].append(
        {"id": "a1", "job_id": "best", "status": "applying", "automated": True, "attempts": 1, "attempted_at": 1}))
    calls, _ = agent
    run()
    assert applications()["best"]["status"] == "failed" and applications()["best"]["manual_retry"]
    assert [call["job"] for call in calls] == ["good"]


def test_nothing_is_applied_to_while_auto_apply_is_off(agent):
    calls, _ = agent
    db.mutate_state(lambda state: state["auto_apply"].update(enabled=False))
    run()
    assert calls == [] and applications() == {}


def test_keyword_perfect_jobs_are_matched_to_the_master_cv_only_while_auto_apply_is_on(workspace):
    db.mutate_state(lambda state: state["jobs"].append(job("perfect", match_score=100)))
    assert "perfect" in job_matching.pending(db.get_state())
    db.mutate_state(lambda state: state["auto_apply"].update(enabled=False))
    assert "perfect" not in job_matching.pending(db.get_state())


def test_controls_answers_retry_and_the_application_browser(workspace, monkeypatch, tmp_path):
    from backend.main import app
    launched = []

    class Chrome:
        def __init__(self, args, **kwargs):
            launched.append(args)
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout=None):
            return 0

    chrome = tmp_path / "Google Chrome"
    chrome.write_text("")
    monkeypatch.setattr(apply_agent, "CHROME", chrome)
    monkeypatch.setattr(auto_apply.subprocess, "Popen", Chrome)
    db.mutate_state(lambda state: state["applications"].append(
        {"id": "a1", "job_id": "best", "status": "failed", "automated": True, "attempts": 3, "attempted_at": time.time(), "manual_retry": True}))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/auto-apply/stop").json()["enabled"] is False
        assert client.post("/api/auto-apply/start").json()["enabled"] is True
        answers = {"sponsorship": "No", "other": [{"question": "Notice period?", "answer": "Two weeks"}]}
        saved = client.put("/api/auto-apply/answers", json=answers)
        assert saved.status_code == 200 and saved.json()["answers"]["other"][0]["answer"] == "Two weeks"
        assert client.put("/api/auto-apply/answers", json={"nickname": "Sam"}).status_code == 422
        assert client.get("/api/state").json()["application_answers"]["sponsorship"] == "No"

        assert "best" not in auto_apply.pending(db.get_state())
        assert client.post("/api/auto-apply/applications/a1/retry").status_code == 200
        assert "best" in auto_apply.pending(db.get_state()), "Try again queues it even after the automatic tries ran out."

        opened = client.post("/api/auto-apply/browser", json={"application_id": "a1"})
        assert opened.status_code == 200 and opened.json()["browser_open"] is True
        assert launched[0][0] == str(chrome) and launched[0][-1] == "https://example.com/best"
        assert any(arg.startswith("--user-data-dir=") and arg.endswith("apply-browser-profile") for arg in launched[0])
        assert client.get("/api/auto-apply").json()["browser_open"] is True
        assert client.post("/api/auto-apply/browser/close").json()["browser_open"] is False

        monkeypatch.setattr(auto_apply, "_applying", True)
        assert client.post("/api/auto-apply/browser", json={}).status_code == 409, "The agent's browser profile is in use."


class FakeBridge:
    def __init__(self, reply="", error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def generate_json(self, instructions, data, schema, **kwargs):
        self.calls.append({"instructions": instructions, "schema": schema, **kwargs})
        if self.error:
            raise self.error
        return self.reply

    async def close(self):
        pass


def test_the_browser_agent_thinks_with_codex_and_sees_the_latest_screenshots(monkeypatch):
    pytest.importorskip("browser_use")
    from browser_use.llm.exceptions import ModelProviderError
    from browser_use.llm.messages import ContentPartImageParam, ContentPartTextParam, ImageURL, SystemMessage, UserMessage

    bridge = FakeBridge(json.dumps({"submitted": True, "confirmation": "APP-1", "summary": "Sent."}))
    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: bridge)
    shots = [ContentPartImageParam(image_url=ImageURL(url=f"data:image/png;base64,{n}")) for n in "ABC"]
    messages = [SystemMessage(content="You fill forms."), UserMessage(content=[ContentPartTextParam(text="Page state"), *shots])]
    text, images = apply_agent.ChatCodex.render(messages)
    assert "<system>\nYou fill forms.\n</system>" in text and text.count("[screenshot attached]") == 3

    llm = apply_agent.ChatCodex("gpt-6-astra")
    result = asyncio.run(llm.ainvoke(messages, apply_agent.ApplicationResult))
    assert result.completion.submitted and result.completion.confirmation == "APP-1"
    call = bridge.calls[0]
    assert call["model"] == "gpt-6-astra" and call["effort"] == apply_agent.STEP_EFFORT
    assert call["image_urls"] == images[-2:] == ["data:image/png;base64,B", "data:image/png;base64,C"]
    assert "never invent" in call["base_instructions"] and "untrusted" in call["base_instructions"]

    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: FakeBridge(error=codex_bridge.CodexError("Signed out.")))
    with pytest.raises(ModelProviderError):
        asyncio.run(llm.ainvoke(messages, apply_agent.ApplicationResult))


def test_the_agent_is_told_to_ask_instead_of_guessing_and_to_stop_for_sign_ins():
    assert "never guess" in apply_agent.RULES and "ask_user" in apply_agent.RULES and "needs_sign_in" in apply_agent.RULES
    assert "Never pay" in apply_agent.RULES and "decline" in apply_agent.RULES
    task = apply_agent.task_for(job("best"), {"profile": {"name": "Sam Lee"}}, "Sam-Lee-Resume.pdf")
    assert "Easy Apply" in task and "Sam-Lee-Resume.pdf" in task and "https://example.com/best" in task
