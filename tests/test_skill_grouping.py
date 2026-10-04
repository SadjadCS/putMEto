"""Technical skills on My profile are grouped by context, such as Programming Languages or Large Language Models."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend import ai, db, skill_grouping
from backend.skill_grouping import kick as real_kick  # Tests otherwise replace kick with a no-op.


def skill(skill_id, name, confirmed=True, **extra):
    return {"id": skill_id, "name": name, "confirmed": confirmed, **extra}


def groups(*pairs):
    return ai.SkillGroups(groups=[ai.SkillGroup(name=name, skills=list(numbers)) for name, numbers in pairs])


@pytest.fixture
def workspace(monkeypatch):
    db.initialize()
    db.mutate_state(lambda state: state.update(skills=[
        skill("py", "Python", kind="language"), skill("ts", "TypeScript", kind="language"), skill("lc", "LangChain", kind="framework"),
        skill("cr", "CrewAI", kind="framework"), skill("pt", "PyTorch", kind="framework"), skill("rag", "RAG pipelines", confirmed=False)]))
    calls = []

    async def strongest(settings, level=None):
        return {**settings, "model": "gpt-6-astra"}, level

    async def group_skills(settings, asked, existing, effort=None, timeout=None):
        calls.append({"names": [item["name"] for item in asked], "existing": existing, "effort": effort})
        if len(asked) == 1:
            return groups(("multi-agent frameworks", [1]))  # Same group, different case.
        return groups(("Programming Languages", [1, 2]), ("Multi-Agent Frameworks", [3, 4]), ("Machine Learning", [5]))

    monkeypatch.setattr(ai, "strongest", strongest)
    monkeypatch.setattr(ai, "group_skills", group_skills)
    return calls


def shown():
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        return client.get("/api/state").json()


def test_every_skill_is_grouped_by_context_and_shown_with_its_group(workspace):
    asyncio.run(skill_grouping._work())
    assert workspace[0]["effort"] == "medium" and workspace[0]["existing"] == []
    state = shown()
    assert {item["name"]: item.get("group") for item in state["skills"]} == {
        "Python": "Programming Languages", "TypeScript": "Programming Languages", "LangChain": "Multi-Agent Frameworks",
        "CrewAI": "Multi-Agent Frameworks", "PyTorch": "Machine Learning", "RAG pipelines": "Other"}, \
        "A skill the model left out still gets a place."
    assert state["skill_group_order"] == ["Programming Languages", "Multi-Agent Frameworks", "Machine Learning", "Other"]
    assert "skill_grouping" not in state


def test_a_new_skill_joins_an_existing_group_without_regrouping_the_rest(workspace):
    asyncio.run(skill_grouping._work())
    db.mutate_state(lambda state: state["skills"].append(skill("lg", "LangGraph", kind="framework")))
    assert [item["name"] for item in skill_grouping.pending(db.get_state())] == ["LangGraph"]
    asyncio.run(skill_grouping._work())
    assert workspace[-1]["names"] == ["LangGraph"] and "Multi-Agent Frameworks" in workspace[-1]["existing"]
    assert skill_grouping.group_of(db.get_state())["lg"] == "Multi-Agent Frameworks"


def test_saving_skills_keeps_their_groups_and_regroup_starts_over(workspace, monkeypatch):
    asyncio.run(skill_grouping._work())
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        items = client.get("/api/state").json()["skills"]
        saved = client.put("/api/skills", json={"items": [item for item in items if item["name"] != "PyTorch"]})
        assert saved.status_code == 200, "Round-tripped groups are accepted."
        state = client.get("/api/state").json()
        assert {item["name"]: item["group"] for item in state["skills"]}["LangChain"] == "Multi-Agent Frameworks"
        assert "Machine Learning" not in state["skill_group_order"], "A group with no skills left disappears."
        regrouped = client.post("/api/skill-groups/regroup")
        assert regrouped.status_code == 200
    assert workspace[-1]["existing"] == [] and len(workspace[-1]["names"]) == 5, "Regrouping sends every skill."


def test_grouping_never_changes_the_skills_resumes_use(workspace):
    before = db.get_state()["skills"]
    asyncio.run(skill_grouping._work())
    assert db.get_state()["skills"] == before


def test_a_failure_is_reported_and_not_retried_right_away(workspace, monkeypatch):
    async def broken(*args, **kwargs):
        raise ai.AIError("The model was busy.")

    monkeypatch.setattr(ai, "group_skills", broken)
    asyncio.run(skill_grouping._work())
    status = skill_grouping.status()
    assert status["state"] == "failed" and "The model was busy." in status["message"] and status["waiting"] == 6
    started = []
    monkeypatch.setattr(skill_grouping.asyncio, "create_task", lambda work: started.append(work.close()))
    real_kick()
    assert started == [], "New skills wait a while after a failure."
    real_kick(force=True)
    assert len(started) == 1, "Group by context tries again right away."


def test_grouping_request_lists_the_skills_by_number_with_context_examples(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(instructions=instructions, data=data, output_type=output_type)
        return output_type(groups=[])

    monkeypatch.setattr(ai, "generate", generate)
    asyncio.run(ai.group_skills({}, [{"name": "Python", "kind": "language"}, {"name": "CrewAI"}], ["Programming Languages"]))
    assert seen["data"]["skills"] == [{"number": 1, "name": "Python", "type": "language"}, {"number": 2, "name": "CrewAI", "type": ""}]
    assert seen["data"]["existing_groups"] == ["Programming Languages"] and seen["output_type"] is ai.SkillGroups
    for example in ("Programming Languages", "Multi-Agent Frameworks", "Large Language Models", "Machine Learning", "Web Development"):
        assert example in seen["instructions"]
