"""Each technical term in the master CV gets a bag of similar terms, and a job's resume uses the posting's choice."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend import ai, ats, db, resumes, term_bags
from backend.term_bags import kick as real_kick  # Tests otherwise replace kick with a no-op.


STATE = {
    "profile": {"name": "Alex Morgan", "email": "alex@example.com", "summary": "AI engineer working on RAG and LLM fine-tuning."},
    "items": [{"id": "exp", "kind": "experience", "title": "AI Engineer", "organization": "Example Corp", "start": "2021",
               "original": "• Fine-tuned LLMs for contract drafting.\n• Ran Postgres and k8s clusters.\n• Built LLM evaluation tools.\n"
                           "• Designed agentic AI systems over 10 years of software development.",
               "enhanced": "", "confirmed": True}],
    "skills": [{"id": "llm", "name": "LLM", "confirmed": True}, {"id": "pg", "name": "Postgres", "confirmed": True}],
}
BAGS = [{"names": ["LLM", "Large Language Model"], "phrasings": []},
        {"names": ["RAG", "Retrieval-Augmented Generation"], "phrasings": []},
        {"names": ["Postgres", "PostgreSQL"], "phrasings": []}, {"names": ["k8s", "Kubernetes"], "phrasings": []},
        {"names": ["PyTorch", "Torch"], "phrasings": []},
        {"names": ["agentic AI"], "phrasings": ["AI agents", "agent-based systems"]},
        {"names": ["software development"], "phrasings": ["software engineering"]}]


def job(description):
    return {"id": "job", "title": "AI Engineer", "description": description}


def wording(description, state=STATE):
    resume = resumes.build_resume(state)
    shown = term_bags.apply(resume, term_bags.choices(BAGS, job(description), resume))
    return shown, resumes.render_html(shown)


def test_the_postings_full_name_is_paired_once_with_your_abbreviation():
    shown, html = wording("Experience with Large Language Models and Retrieval-Augmented Generation is required.")
    assert shown["profile"]["summary"] == "AI engineer working on Retrieval-Augmented Generation (RAG) and Large Language Model (LLM) fine-tuning."
    assert resumes.resume_text(shown["items"][0]).splitlines()[0] == "• Fine-tuned LLMs for contract drafting.", "Elsewhere your own name stays."
    assert shown["skills"][0] == "LLM"
    assert "Large Language Model (LLM) fine-tuning" in html
    assert {"from": "LLM", "to": "Large Language Model (LLM)"} in shown["wording"]


def test_the_postings_abbreviation_is_added_to_your_full_name_and_plurals_follow_your_wording():
    state = {**STATE, "profile": {**STATE["profile"], "summary": "Builds large language models."},
             "items": [{**STATE["items"][0], "original": "• Ran Postgres clusters."}], "skills": [STATE["skills"][1]]}
    shown, _ = wording("Strong LLM background.", state)
    assert shown["profile"]["summary"] == "Builds large language models (LLMs)."
    shown, _ = wording("Strong LLM background.")
    assert shown["profile"]["summary"] == STATE["profile"]["summary"], "The resume already says LLM, so nothing changes."


def test_other_spellings_take_the_postings_form_everywhere():
    shown, html = wording("We run PostgreSQL on Kubernetes.")
    assert "• Ran PostgreSQL and Kubernetes clusters." in shown["items"][0]["shown"]
    assert "PostgreSQL" in shown["skills"] and "Postgres" not in shown["skills"]
    assert "Postgres " not in html


def test_the_postings_phrasing_is_added_to_the_skills_and_your_sentences_stay_as_written():
    shown, html = wording("You will build AI agents; your AI agents use tools. Strong software engineering skills.")
    text = resumes.resume_text(shown["items"][0])
    assert "• Designed agentic AI systems over 10 years of software development." in text, "No broken sentences."
    assert shown["skills"][-2:] == ["AI agents", "Software engineering"]
    assert "AI agents" in html and "Software engineering" in html
    assert {"from": "agentic AI", "to": "AI agents (added to skills)"} in shown["wording"]
    shown, _ = wording("You have built agentic AI and AI agents.")
    assert "AI agents" not in shown["skills"], "Your resume already uses one of the posting's terms."


def test_nothing_changes_when_the_resume_already_uses_the_postings_name_or_the_posting_lacks_the_term():
    shown, _ = wording("You know LLMs, Postgres, and PyTorch.")
    assert "wording" not in shown or shown["wording"] == []
    assert "shown" not in shown["items"][0]
    assert term_bags.choices(BAGS, job("You know Torch."), resumes.build_resume(STATE)) == [], "PyTorch isn't in this CV."


def test_bags_keep_only_terms_your_cv_uses_and_each_name_once():
    found = [ai.TermBag(term="LLM", names=["Large Language Model", "large-language model", "LLM"], phrasings=[]),
             ai.TermBag(term="Rust", names=["Rust lang"], phrasings=[]), ai.TermBag(term="k8s", names=[], phrasings=[]),
             ai.TermBag(term="software development", names=[], phrasings=["software engineering", "Software Development"])]
    text = term_bags.master_text({**STATE, "profile": STATE["profile"]})
    assert term_bags.clean(found, text) == [{"names": ["LLM", "Large Language Model"], "phrasings": []},
                                            {"names": ["software development"], "phrasings": ["software engineering"]}]


@pytest.fixture
def workspace(monkeypatch):
    db.initialize()

    def seed(state):
        state["profile"].update(STATE["profile"])
        state.update(items=[dict(item) for item in STATE["items"]], skills=[dict(skill) for skill in STATE["skills"]])
        posting = job("Large Language Models, PostgreSQL, and Rust.") | {"url": "https://example.com/job", "status": "discovered", "match_score": 70}
        posting["resume"] = resumes.build_resume(state, posting)
        state["jobs"] = [posting]
    db.mutate_state(seed)
    calls = []

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def term_bags_ai(settings, state, effort=None, timeout=None):
        calls.append(effort)
        return [ai.TermBag(term=bag["names"][0], names=bag["names"][1:], phrasings=bag["phrasings"]) for bag in BAGS]

    async def ats_terms(settings, posting, effort=None, timeout=None):
        return [ai.ATSTerm(term="Large Language Models", kind="skill", importance="required", variants=[]),
                ai.ATSTerm(term="PostgreSQL", kind="tool", importance="required", variants=[]),
                ai.ATSTerm(term="Rust", kind="skill", importance="required", variants=[])]

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "term_bags", term_bags_ai)
    monkeypatch.setattr(ai, "ats_terms", ats_terms)
    return calls


def test_bags_are_listed_once_per_master_cv(workspace):
    assert term_bags.stale(db.get_state())
    asyncio.run(term_bags._work())
    saved = db.get_state()["term_bags"]
    assert workspace == ["medium"] and saved["model"] == "gpt-6-astra"
    assert all(bag["names"][0] != "PyTorch" for bag in saved["bags"])
    assert {"names": ["LLM", "Large Language Model"], "phrasings": []} in saved["bags"]
    assert {"names": ["agentic AI"], "phrasings": ["AI agents", "agent-based systems"]} in saved["bags"]
    assert not term_bags.stale(db.get_state())
    db.mutate_state(lambda state: state["items"][0].update(original="• Built Kubernetes operators."))
    assert term_bags.stale(db.get_state()), "A changed master CV gets its bags listed again."


def test_job_resumes_the_ats_check_and_downloads_use_the_postings_names(workspace):
    asyncio.run(term_bags._work())
    with TestClient(__import__("backend.main", fromlist=["app"]).app, base_url="http://127.0.0.1:8000") as client:
        assert "Large Language Model (LLM) fine-tuning" in client.get("/api/jobs/job/resume").text
        assert client.get("/api/jobs/job/resume.pdf").status_code == 200
        result = client.post("/api/jobs/job/ats").json()
    assert {term["term"]: term["status"] for term in result["terms"]} == {
        "Large Language Models": "present", "PostgreSQL": "present", "Rust": "missing"}
    assert {"from": "Postgres", "to": "PostgreSQL"} in result["wording"]
    assert db.get_state()["jobs"][0]["resume"]["skills"] == ["LLM", "Postgres"], "The stored resume stays as tailored."


def test_the_ats_check_finds_your_cvs_other_name_for_a_missing_keyword(workspace):
    asyncio.run(term_bags._work())
    state = db.get_state()
    posting = state["jobs"][0]
    posting["ats"] = {"terms": [{"term": "Kubernetes", "kind": "tool", "importance": "required", "variants": []}],
                      "terms_basis": ats.terms_basis(posting)}
    posting["resume"]["items"][0]["original"] = "• Fine-tuned LLMs."  # This job's resume left k8s out.
    result = ats.evaluate(state, posting)
    assert result["terms"][0]["status"] == "addable" and result["terms"][0]["wording"] == "k8s"


def test_a_changed_master_cv_starts_listing_again_but_not_right_after_a_failure(workspace, monkeypatch):
    started = []
    monkeypatch.setattr(term_bags.asyncio, "create_task", lambda work: started.append(work.close()))
    real_kick()
    assert len(started) == 1
    monkeypatch.setattr(term_bags, "_failed_at", __import__("time").time())
    real_kick()
    assert len(started) == 1
    real_kick(force=True)
    assert len(started) == 2


def test_the_request_asks_only_for_exact_equivalents(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(instructions=instructions, data=data)
        return output_type(bags=[])

    monkeypatch.setattr(ai, "generate", generate)
    asyncio.run(ai.term_bags({}, {**STATE, "skills": STATE["skills"]}))
    assert seen["data"]["master_resume"]["skills"][0]["name"] == "LLM"
    assert "software development and software engineering" in seen["instructions"] and "agentic AI and AI agents" in seen["instructions"]
    assert "Never include a different product or a broader or narrower category" in seen["instructions"]
