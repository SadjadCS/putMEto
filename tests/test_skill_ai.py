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
    assert parsed.skills[0].source_section is None
    with pytest.raises(ValidationError):
        ai.ExtractedResume.model_validate({"profile": {}, "items": [], "skills": [skill("PostgreSQL", support="related")]})


@pytest.mark.parametrize("source_section", ["skills", "experience", "other", None])
def test_imported_skill_section_metadata_is_optional_and_validated(source_section):
    parsed = ai.ExtractedSkill.model_validate({**skill("SQL"), "source_section": source_section})
    assert parsed.source_section == source_section
    with pytest.raises(ValidationError):
        ai.ExtractedSkill.model_validate({**skill("SQL"), "source_section": "job_description"})


def test_import_and_backfill_preserve_skills_absent_from_experience(monkeypatch):
    calls = []
    lines = ["Example Applicant", "Technical Skills: SQL, Python", "Experience: laboratory assistant."]

    async def generate(settings, instructions, data, output_type):
        calls.append((instructions, data, output_type))
        payload = {"skills": [{**skill("SQL", evidence="Technical Skills: SQL, Python", kind="language"), "source_section": "skills"}]}
        if output_type is ai.ExtractedResume:
            payload.update(profile={}, items=[])
        return output_type.model_validate(payload)

    monkeypatch.setattr(ai, "generate", generate)
    extracted = asyncio.run(ai.extract_resume({}, lines))
    recovered = asyncio.run(ai.extract_skills({}, lines))
    assert extracted.skills[0].name == recovered[0].name == "SQL"
    assert extracted.skills[0].source_section == recovered[0].source_section == "skills"
    assert extracted.items == []
    assert calls[0][1] == calls[1][1] == {"cv_lines": [{"line": number, "text": text} for number, text in enumerate(lines, 1)]}
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
            {**skill("SQL", kind="language", evidence="Technical Skills: SQL"), "origin": "resume", "confirmed": False, "source_section": "skills"},
            {**skill("Apache Airflow"), "origin": "resume", "confirmed": False, "source_section": "experience"},
            {**skill("Legacy skill"), "origin": "resume", "confirmed": False},
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
    assert [item["name"] for item in data["resume_skills"]] == ["SQL", "Apache Airflow", "Legacy skill"]
    assert [item["name"] for item in data["cv_skills_section"]] == ["SQL"]
    assert data["cv_skills_section"][0]["evidence"] == "Technical Skills: SQL"
    assert data["resume_skills"][0]["source_section"] == "skills"
    assert [item["name"] for item in data["confirmed_skills"]] == ["Python"]
    assert len(data["confirmed_items"]) == 1
    assert "Qdrant" in data["existing_names_for_deduplication_only"]
    assert all("Qdrant" not in str(data[key]) for key in ("cv_skills_section", "resume_skills", "confirmed_skills", "confirmed_items"))
    assert "settings" not in data
    assert data["job_context_for_relevance_only"]["description"] == "The employer requires Oracle."
    assert [(item.name, item.support) for item in result] == [("Workflow orchestration", "supported"), ("PostgreSQL", "related")]
    assert all(not hasattr(item, "confirmed") for item in result)
    assert state == original


def test_legacy_resume_skills_remain_context_without_fabricating_a_section(monkeypatch):
    state = {"skills": [{**skill("SQL"), "origin": "resume", "confirmed": False}], "items": []}

    async def generate(settings, instructions, data, output_type):
        assert data["cv_skills_section"] == []
        assert [item["name"] for item in data["resume_skills"]] == ["SQL"]
        assert data["resume_skills"][0].get("source_section") is None
        return output_type(skills=[])

    monkeypatch.setattr(ai, "generate", generate)
    assert asyncio.run(ai.suggest_skills({}, state)) == []


@pytest.mark.parametrize("skills_section_first", [True, False])
@pytest.mark.parametrize("full_import", [True, False])
def test_duplicate_import_keeps_skills_section_evidence_regardless_of_order(monkeypatch, skills_section_first, full_import):
    explicit = {**skill("SQL", evidence="Technical Skills: SQL"), "source_section": "skills"}
    experience = {**skill("SQL", evidence="Built reports with SQL."), "source_section": "experience"}
    candidates = [explicit, experience] if skills_section_first else [experience, explicit]

    async def generate(settings, instructions, data, output_type):
        payload = {"skills": candidates}
        if full_import:
            payload.update(profile={}, items=[])
        return output_type.model_validate(payload)

    monkeypatch.setattr(ai, "generate", generate)
    lines = ["Technical Skills: SQL", "Experience: Built reports with SQL."]
    if full_import:
        result = asyncio.run(ai.extract_resume({}, lines)).skills
    else:
        result = asyncio.run(ai.extract_skills({}, lines))
    assert len(result) == 1
    assert result[0].source_section == "skills"
    assert result[0].evidence == "Technical Skills: SQL"


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


def role(title, family="Engineering", fit="strong"):
    return {"title": title, "family": family, "fit": fit, "reason": f"The resume shows {title} work."}


def test_role_suggestions_list_every_title_from_the_master_resume(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None, effort=None, timeout=None):
        seen.update(instructions=instructions, data=data, output_type=output_type, effort=effort)
        return output_type.model_validate({"roles": [role(" Data  Engineer "), role("Data Engineer"), role("Research Engineer", "Research", "possible")]})

    monkeypatch.setattr(ai, "generate", generate)
    state = {"profile": {"headline": "AI engineer", "summary": "Builds agents."}, "preferences": {"track": "industry"},
             "items": [{"kind": "experience", "title": "AI Specialist", "original": "• Built agents.", "confirmed": True},
                       {"kind": "experience", "title": "Unreviewed draft", "original": "x", "confirmed": False}],
             "skills": [{"name": "Python", "category": "Languages", "confirmed": True}],
             "positions": [{"name": "LLM Engineer", "confirmed": True}], "dismissed_positions": ["cashier"]}
    found = asyncio.run(ai.suggest_roles({}, state, effort="ultra"))
    assert [(item.title, item.fit) for item in found] == [("Data Engineer", "strong"), ("Research Engineer", "possible")]
    assert seen["output_type"] is ai.RoleSuggestions and seen["effort"] == "ultra"
    assert "every distinct job title" in seen["instructions"] and "exhaustive" in seen["instructions"]
    resume = seen["data"]["master_resume"]
    assert [entry["title"] for entry in resume["entries"]] == ["AI Specialist"], "Only the confirmed master resume is used."
    assert resume["skills"] == [{"name": "Python", "category": "Languages"}]
    assert seen["data"]["already_listed"] == ["LLM Engineer"] and seen["data"]["declined_do_not_suggest"] == ["cashier"]
    assert asyncio.run(ai.suggest({}, state, "positions")) == ["Data Engineer", "Research Engineer"]


@pytest.mark.parametrize("field", ["name", "evidence", "rationale"])
def test_skill_candidates_require_reviewable_nonblank_details(field):
    payload = skill("SQL")
    payload[field] = "   "
    with pytest.raises(ValidationError):
        ai.SkillCandidate.model_validate(payload)


CV = [
    "Sam Example",
    "sam@example.com | (555) 010-0200",
    "Summary",
    "Engineer who  builds data tools.",
    "Keeps them simple.",
    "Experience",
    "Senior Engineer, Northwind Labs — Jan. 2022 – Present",
    "Built the reporting API in Python.",
    "Cut query time by 40%.",
    "Founder, Picsun — 2014",
    "Skills",
    "Languages: Python, SQL",
    "Cloud: AWS",
]


def respond_with(monkeypatch, payload):
    async def generate(settings, instructions, data, output_type, images=None):
        return output_type.model_validate(payload)
    monkeypatch.setattr(ai, "generate", generate)


def test_import_copies_descriptions_and_fields_from_the_cv_never_from_the_ai(monkeypatch):
    respond_with(monkeypatch, {
        "profile": {"name": "sam example", "email": "sam@example.com", "phone": "(555) 010-0200",
                    "headline": "Data engineering leader", "location": "", "website": "", "summary_lines": [5, 4]},
        "items": [
            {"kind": "experience", "title": "Senior  Engineer", "organization": "Northwind Labs", "start": "Jan. 2022",
             "end": "Present", "description_lines": [9, 8, 8, 40]},
            {"kind": "experience", "title": "Founder", "organization": "Picsun Inc.", "start": "2014", "end": "",
             "description_lines": []},
            {"kind": "project", "title": "Reporting platform rebuild", "organization": "", "start": "", "end": "",
             "description_lines": [8]},
        ],
        "skills": [],
    })
    result = asyncio.run(ai.extract_resume({}, CV))
    assert result.profile.name == "Sam Example", "Fields keep the CV's own spelling and capitalization."
    assert result.profile.headline == "", "A headline the CV does not contain is never saved."
    assert result.profile.summary == "Engineer who  builds data tools.\nKeeps them simple."
    senior, founder, project = result.items
    assert senior.title == "Senior Engineer"
    assert senior.original == "Built the reporting API in Python.\nCut query time by 40%."
    assert (senior.organization, senior.start, senior.end) == ("Northwind Labs", "Jan. 2022", "Present")
    assert founder.original == "" and founder.organization == ""
    assert project.title == "Reporting platform rebuild", "A required title is kept even when reworded."


def test_import_keeps_skills_under_the_cv_spelling_and_categories(monkeypatch):
    def entry(name, kind, category="", aliases=()):
        return {**skill(name, kind=kind, evidence="Languages:   Python,\nSQL", aliases=list(aliases)),
                "source_section": "skills", "category": category}
    respond_with(monkeypatch, {"skills": [
        entry("python", "language", "Languages"),
        entry("Amazon Web Services", "platform", "Cloud", aliases=["AWS"]),
        entry("Structured Query Language", "language", "Programming"),
        entry("Kubernetes", "platform", "Cloud"),
    ]})
    result = asyncio.run(ai.extract_skills({}, CV))
    # Renamed skills fall back to an alias the CV uses; names the CV never uses are dropped.
    assert [(item.name, item.category) for item in result] == [("Python", "Languages"), ("AWS", "Cloud")]
    assert result[0].evidence == "Languages: Python, SQL"


def test_pdf_transcription_sends_pages_and_tidies_lines(monkeypatch):
    seen = {}

    async def generate(settings, instructions, data, output_type, images=None):
        seen.update(data=data, images=images, output_type=output_type)
        return output_type.model_validate({"lines": ["  Sam   Example ", "", "Built the API."]})

    monkeypatch.setattr(ai, "generate", generate)
    assert asyncio.run(ai.transcribe_pdf({}, [b"page-1", b"page-2"])) == ["Sam Example", "Built the API."]
    assert seen == {"data": {"page_count": 2}, "images": [b"page-1", b"page-2"], "output_type": ai.Transcript}

    async def empty(settings, instructions, data, output_type, images=None):
        return output_type.model_validate({"lines": [" "]})
    monkeypatch.setattr(ai, "generate", empty)
    with pytest.raises(ai.AIError, match="no text"):
        asyncio.run(ai.transcribe_pdf({}, [b"page"]))
