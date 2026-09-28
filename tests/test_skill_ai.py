"""Skill extraction preserves CV evidence and keeps related options unverified."""

import asyncio
import copy

import pytest
from pydantic import ValidationError

from backend import ai


def skill(name, *, support="supported", kind="tool", evidence="Listed in the synthetic CV.", aliases=None):
    return {
        "name": name, "kind": kind, "support": support, "evidence": evidence,
        "rationale": "Explicitly stated." if support == "supported" else "A related option; verify your own experience.",
        "aliases": aliases or [],
    }


def test_resume_skills_remain_backward_compatible_but_reject_related_imports():
    assert ai.ExtractedResume.model_validate({"profile": {}, "items": []}).skills == []
    parsed = ai.ExtractedResume.model_validate({"profile": {}, "items": [], "skills": [skill("SQL", kind="language")]})
    assert parsed.skills[0].name == "SQL"
    assert parsed.skills[0].support == "supported"
    with pytest.raises(ValidationError):
        ai.ExtractedResume.model_validate({"profile": {}, "items": [], "skills": [skill("PostgreSQL", support="related")]})


def test_import_and_backfill_preserve_skills_absent_from_experience(monkeypatch):
    calls = []
    text = "Example Applicant\nTechnical Skills: SQL, Python\nExperience: laboratory assistant."

    async def generate(settings, instructions, data, output_type):
        calls.append((instructions, data, output_type))
        payload = {"skills": [skill("SQL", evidence="Technical Skills: SQL, Python", kind="language")]}
        if output_type is ai.ExtractedResume:
            payload.update(profile={}, items=[])
        return output_type.model_validate(payload)

    monkeypatch.setattr(ai, "generate", generate)
    extracted = asyncio.run(ai.extract_resume({}, text))
    recovered = asyncio.run(ai.extract_skills({}, text))
    assert extracted.skills[0].name == recovered[0].name == "SQL"
    assert extracted.items == []
    assert calls[0][1] == calls[1][1] == {"cv_text": text}
    assert calls[1][2] is ai.ExtractedSkills
    assert "Technical Skills" in calls[0][0]
    assert "never appear in the work experience" in calls[1][0]


def test_suggestions_include_resume_skills_without_promoting_old_candidates(monkeypatch):
    state = {
        "items": [
            {"id": "confirmed", "confirmed": True, "original": "Orchestrated scheduled data pipelines using Apache Airflow.", "enhanced": "Orchestrated scheduled data pipelines using Apache Airflow."},
            {"id": "draft", "confirmed": False, "original": "Unverified Kubernetes draft."},
        ],
        "skills": [
            {**skill("SQL", kind="language", evidence="Technical Skills: SQL"), "origin": "resume", "confirmed": False},
            {**skill("Python", kind="language"), "origin": "manual", "confirmed": True},
            {**skill("Qdrant", support="related"), "origin": "suggestion", "confirmed": False},
            {**skill("Unreviewed manual draft"), "origin": "manual", "confirmed": False},
        ],
        "settings": {"api_key": "must-not-send"}, "preferences": {"track": "industry"},
    }
    original = copy.deepcopy(state)
    observed = {}

    async def generate(settings, instructions, data, output_type):
        observed.update(data=data, instructions=instructions, output_type=output_type)
        return output_type.model_validate({"skills": [
            skill("Workflow orchestration", kind="technique", evidence="Orchestrated scheduled data pipelines using Apache Airflow."),
            skill("PostgreSQL", kind="database", support="related", evidence="SQL"),
        ]})

    monkeypatch.setattr(ai, "generate", generate)
    result = asyncio.run(ai.suggest_skills({}, state, {"title": "Example role", "description": "The employer requires Oracle."}))
    data = observed["data"]
    assert [item["name"] for item in data["resume_skills"]] == ["SQL"]
    assert [item["name"] for item in data["confirmed_skills"]] == ["Python"]
    assert len(data["confirmed_items"]) == 1
    assert "Qdrant" in data["existing_names_for_deduplication_only"]
    assert all("Qdrant" not in str(data[key]) for key in ("resume_skills", "confirmed_skills", "confirmed_items"))
    assert "settings" not in data
    assert data["job_context_for_relevance_only"]["description"] == "The employer requires Oracle."
    assert [(item.name, item.support) for item in result] == [("Workflow orchestration", "supported"), ("PostgreSQL", "related")]
    assert all(not hasattr(item, "confirmed") for item in result)
    assert state == original


def test_skill_deduplication_retains_stronger_evidence_and_distinct_languages(monkeypatch):
    candidates = [
        skill("PostgreSQL", support="related", aliases=["Postgres"]),
        skill(" postgresql ", evidence="Database: PostgreSQL", aliases=["Postgres", "postgres", "PostgreSQL"]),
        skill("C", kind="language"), skill("C++", kind="language"), skill("C#", kind="language"),
    ]

    async def generate(*args):
        return ai.SkillSuggestions.model_validate({"skills": candidates})

    monkeypatch.setattr(ai, "generate", generate)
    result = asyncio.run(ai.suggest_skills({}, {"items": [], "skills": []}))
    assert [item.name for item in result] == ["postgresql", "C", "C++", "C#"]
    assert result[0].support == "supported"
    assert result[0].evidence == "Database: PostgreSQL"
    assert result[0].aliases == ["Postgres"]


def test_legacy_skill_suggestions_never_flatten_related_options(monkeypatch):
    async def typed(*args):
        return [ai.SkillCandidate(**skill("SQL")), ai.SkillCandidate(**skill("MySQL", support="related"))]

    monkeypatch.setattr(ai, "suggest_skills", typed)
    assert asyncio.run(ai.suggest({}, {}, "skills")) == ["SQL"]


def test_position_suggestions_keep_the_existing_contract(monkeypatch):
    async def generate(settings, instructions, data, output_type):
        assert output_type is ai.Suggestions
        return ai.Suggestions(names=[" Data Engineer ", "Data Engineer", "Research Engineer"])

    monkeypatch.setattr(ai, "generate", generate)
    state = {"items": [], "skills": [], "preferences": {}}
    assert asyncio.run(ai.suggest({}, state, "positions")) == ["Data Engineer", "Research Engineer"]


@pytest.mark.parametrize("field", ["name", "evidence", "rationale"])
def test_skill_candidates_require_reviewable_nonblank_details(field):
    payload = skill("SQL")
    payload[field] = "   "
    with pytest.raises(ValidationError):
        ai.SkillCandidate.model_validate(payload)
