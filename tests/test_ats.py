"""The ATS check reads the resume PDF as an ATS parser does and looks up the job's keywords literally."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend import ai, ats, db, resumes


def term(name, importance="required", kind="skill", variants=()):
    return ai.ATSTerm(term=name, kind=kind, importance=importance, variants=list(variants))


def test_keywords_match_whole_words_with_common_variations():
    def hit(name, text, *variants):
        return ats.find({"term": name, "variants": list(variants)}, text)

    assert hit("Multi-agent systems", "Built multi agent system demos") == "multi agent system"
    assert hit("REST API", "Designed REST APIs.") == "REST APIs"
    assert hit("Databases", "Wrote SQL database queries.") == "database"
    assert hit("Processes", "Improved the review process.") == "process"
    assert hit("master's degree", "Two master’s degrees.") == "master’s degrees"
    assert hit("Kubernetes", "Ran on Kubernetes.") == "Kubernetes" and hit("Express", "Built with Express.") == "Express"
    assert hit("Large Language Models", "Fine-tuned LLMs.", "LLMs") == "LLMs"
    assert hit("C", "Wrote C++ and C# services.") is None, "C is not C++ or C#."
    assert hit("C++", "Wrote C++ services.") == "C++"
    assert hit("Go", "Ready to go further.") is None and hit("Go", "Services in Go and Python.") == "Go", \
        "Short terms keep their capitalization."
    assert hit("Java", "Built JavaScript apps.") is None


RESUME_STATE = {
    "profile": {"name": "Alex Morgan", "email": "alex@example.com", "phone": "+1 (212) 555-0142", "location": "New York, NY",
                "summary": "AI engineer building agentic systems."},
    "items": [{"id": "exp", "kind": "experience", "title": "AI Engineer", "organization": "Example Corp", "start": "Jan. 2021",
               "end": "Present", "original": "• Built RAG pipelines on Kubernetes.", "enhanced": "", "confirmed": True,
               # The XYZ wording left Kubernetes out, so the resume doesn't show it.
               "xyz": "• Cut answer latency by 40% by building RAG pipelines.", "xyz_basis": resumes.text_hash("• Built RAG pipelines on Kubernetes.")}],
    "skills": [{"id": "py", "name": "Python", "confirmed": True}],
}


@pytest.fixture
def workspace(monkeypatch):
    db.initialize()

    def seed(state):
        state.update({key: value for key, value in RESUME_STATE.items() if key != "profile"})
        state["profile"].update(RESUME_STATE["profile"])
        job = {"id": "job", "title": "AI Engineer", "company": "Example Labs", "description": "Python, Retrieval-Augmented Generation, Kubernetes, Rust.",
               "url": "https://example.com/job", "status": "discovered", "match_score": 70}
        job["resume"] = resumes.build_resume(state, job)
        state["jobs"] = [job]
    db.mutate_state(seed)
    calls = []

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def ats_terms(settings, job, effort=None, timeout=None):
        calls.append((settings["model"], effort, job["description"]))
        return [term("Python"), term("Retrieval-Augmented Generation", variants=["RAG"]), term("Kubernetes"),
                term("Rust", "preferred"), term("AI Engineer", kind="title")]

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "ats_terms", ats_terms)
    return calls


def client():
    from backend.main import app
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_the_resume_is_read_back_and_each_keyword_is_present_in_your_cv_or_missing(workspace):
    with client() as http:
        assert http.get("/api/jobs/job/ats").json() == {"ready": False, "message": ""}, "Nothing is listed before you ask."
        result = http.post("/api/jobs/job/ats").json()
    status = {item["term"]: item["status"] for item in result["terms"]}
    assert status == {"Python": "present", "Retrieval-Augmented Generation": "variant", "Kubernetes": "addable",
                      "Rust": "missing", "AI Engineer": "present"}, "The resume says RAG, not the posting's full name."
    kubernetes = next(item for item in result["terms"] if item["term"] == "Kubernetes")
    assert kubernetes["wording"] == "Kubernetes" and kubernetes["where"] == "AI Engineer | Example Corp"
    assert result["score"] == round(100 * 5 / 9), "Required keywords count double; another name counts half."
    checks = {item["label"]: item["level"] for item in result["checks"]}
    assert checks == {"Name": "ok", "Email": "ok", "Phone": "ok", "Section titles": "ok", "Dates": "ok", "Characters": "ok", "Length": "ok"}
    assert workspace == [("gpt-6-astra", "medium", "Python, Retrieval-Augmented Generation, Kubernetes, Rust.")]
    db.mutate_state(lambda state: state["jobs"][0].update(description="Python, RAG, Kubernetes, Rust."))
    state = db.get_state()
    posting = state["jobs"][0]
    posting["ats"] = {"terms": [term("Retrieval-Augmented Generation", variants=["RAG"]).model_dump()], "terms_basis": ats.terms_basis(posting)}
    assert ats.evaluate(state, posting)["terms"][0]["status"] == "present", "The posting says RAG, as the resume does."


def test_keywords_are_listed_once_per_posting_and_again_when_it_changes(workspace):
    with client() as http:
        http.post("/api/jobs/job/ats")
        http.post("/api/jobs/job/ats")
        assert len(workspace) == 1
        assert http.get("/api/jobs/job/ats").json()["ready"] is True
        assert http.get("/api/state").json()["jobs"][0]["ats_score"] == 56
        db.mutate_state(lambda state: state["jobs"][0].update(description="Now Go, too."))
        assert http.get("/api/jobs/job/ats").json()["ready"] is False
        assert http.get("/api/state").json()["jobs"][0]["ats_score"] is None, "An old score isn't shown for a changed posting."
        http.post("/api/jobs/job/ats", json={"refresh": True})
        assert len(workspace) == 2


def test_only_keywords_written_in_your_cv_are_added_in_your_wording(workspace):
    with client() as http:
        http.post("/api/jobs/job/ats")
        added = http.post("/api/jobs/job/ats/add").json()
    status = {item["term"]: item["status"] for item in added["terms"]}
    assert status["Kubernetes"] == "present" and status["Rust"] == "missing", "A real gap is never added."
    resume = db.get_state()["jobs"][0]["resume"]
    assert resume["skills"] == ["Python", "Kubernetes"] and resume["added_keywords"] == ["Kubernetes"]
    text, _ = ats.read_pdf(resume)
    assert "Kubernetes" in text and "Rust" not in text
    assert added["score"] == round(100 * 7 / 9)
    assert added["before"] == round(100 * 5 / 9), "Before: the resume as tailored, without the added keyword."
    with client() as http:
        job = http.get("/api/state").json()["jobs"][0]
    assert (job["ats_before"], job["ats_score"]) == (56, 78)


def test_unreadable_or_unusual_resume_parts_are_flagged():
    resume = resumes.build_resume({**RESUME_STATE, "section_titles": {"experience": "My Journey"}})
    text, pages = ats.read_pdf(resume)
    checks = {item["label"]: item for item in ats.parse_checks(resume, text, pages)}
    assert checks["Section titles"]["level"] == "ok" and "Experience" in checks["Section titles"]["detail"], \
        "Resumes use the standard name instead of a title an ATS may not recognize."
    missing_email = {**resume, "profile": {**resume["profile"], "email": ""}}
    assert {item["label"]: item["level"] for item in ats.parse_checks(missing_email, text, pages)}["Email"] == "fail"
    assert {item["label"]: item["level"] for item in ats.parse_checks(resume, text, 3)}["Length"] == "warn"


def test_a_job_without_a_tailored_resume_is_checked_with_your_general_resume(workspace):
    db.mutate_state(lambda state: state["jobs"][0].pop("resume"))
    with client() as http:
        result = http.post("/api/jobs/job/ats").json()
        assert result["ready"] is True and result["tailored"] is False
        assert {item["term"]: item["status"] for item in result["terms"]}["Python"] == "present"
        assert http.get("/api/state").json()["jobs"][0]["ats_score"] == result["score"]
        assert http.post("/api/jobs/job/ats/add").status_code == 400, "Keywords are added to a tailored resume only."
        db.mutate_state(lambda state: state["skills"].append({"id": "rs", "name": "Rust", "confirmed": True}))
        assert http.get("/api/state").json()["jobs"][0]["ats_score"] is None, "A changed master CV means checking again."


def test_keyword_request_uses_only_the_posting(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(instructions=instructions, data=data)
        return output_type(terms=[term("Python"), term("python", "preferred")])

    monkeypatch.setattr(ai, "generate", generate)
    found = asyncio.run(ai.ats_terms({}, {"title": "AI Engineer", "company": "Labs", "description": "Python.", "url": "x"}))
    assert seen["data"] == {"job": {"title": "AI Engineer", "company": "Labs", "description": "Python."}}
    assert [item.term for item in found] == ["Python"], "Each keyword is listed once."
    assert "Leave out soft skills" in seen["instructions"] and "LLMs" in seen["instructions"]
