"""Explicit application tools available to the local Codex conversation.

No arbitrary HTTP, shell, files, JavaScript, or application submission is exposed.
Resume proposals remain drafts until the user reviews the exact content.
"""

from __future__ import annotations

import inspect
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import ai, db


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Empty(Args):
    pass


class ById(Args):
    id: str = Field(min_length=1, max_length=100)


class Query(Args):
    query: str = Field(default="", max_length=300)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100000)


class Entries(Args):
    limit: int = Field(default=10, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10000)


class ItemContent(ai.ItemFields):
    original: str = Field(min_length=1, max_length=12000)
    enhanced: str = Field(min_length=1, max_length=12000)


class Draft(ItemContent):
    """All content is unconfirmed, even when supplied by a model."""


class ReviewedItem(ItemContent):
    id: str = Field(min_length=1, max_length=100)
    source: Literal["cv", "manual"] | None = None  # Accepted when an entry is sent back; never compared or changed.


class ConfirmItems(Args):
    items: list[ReviewedItem] = Field(min_length=1, max_length=20)


class ReviewedBagItem(Args):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)


class ConfirmBag(Args):
    kind: Literal["skills", "positions"]
    items: list[ReviewedBagItem] = Field(min_length=1, max_length=80)


class ProfilePatch(Args):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=300)
    headline: str | None = Field(default=None, max_length=500)
    website: str | None = Field(default=None, max_length=1000)
    summary: str | None = Field(default=None, max_length=5000)


def _workspace(_):
    state = db.get_state()
    return {
        "profile": state["profile"], "preferences": state["preferences"],
        "skills": state["skills"], "positions": state["positions"],
        "resume_entries": [{key: item.get(key) for key in ("id", "kind", "title", "organization", "confirmed")} for item in state["items"][:100]],
        "entry_count": len(state["items"]), "job_count": len(state["jobs"]),
        "application_count": len(state["applications"]), "sources": state["sources"],
        "resume_url": "/api/resume", "resume_pdf_url": "/api/resume.pdf",
    }


def _entries(args):
    values = db.get_state()["items"]
    return {"items": values[args.offset:args.offset + args.limit], "total": len(values)}


def _saved_jobs(args):
    terms = args.query.casefold().split()
    values = [job for job in db.get_state()["jobs"] if all(
        term in " ".join(str(job.get(key, "")) for key in ("title", "company", "location", "description")).casefold()
        for term in terms
    )]
    return {"items": [
        {key: job.get(key) for key in ("id", "title", "company", "location", "url", "source", "status", "match_score")}
        for job in values[args.offset:args.offset + args.limit]
    ], "total": len(values), "offset": args.offset, "limit": args.limit}


def _get_job(args):
    from .main import find_item
    job = find_item(db.get_state()["jobs"], args.id, "Job")
    return {key: value for key, value in job.items() if key != "resume"}


def _draft(args):
    entry = {"id": uuid4().hex, **args.model_dump(), "confirmed": False, "source": "manual"}
    db.mutate_state(lambda state: state["items"].append(entry))
    return {"item": entry, "message": "Draft saved. The user must review its wording before confirmation."}


def _confirm_items(args):
    from .main import find_item, invalidate_resumes
    def save(state):
        for proposed in args.items:
            current = find_item(state["items"], proposed.id)
            expected = proposed.model_dump(exclude={"source"})
            if any(current.get(key, "") != value for key, value in expected.items()):
                raise HTTPException(409, "This entry changed after it was proposed. Request a fresh review.")
            current["confirmed"] = True
        invalidate_resumes(state)
        return {"message": f"Confirmed {len(args.items)} reviewed resume entries."}
    return db.mutate_state(save)


def _confirm_bag(args):
    from .main import find_item, invalidate_resumes
    def save(state):
        for proposed in args.items:
            current = find_item(state[args.kind], proposed.id)
            if current["name"] != proposed.name:
                raise HTTPException(409, "This suggestion changed. Request a fresh review.")
            current["confirmed"] = True
        if args.kind == "skills":
            invalidate_resumes(state)
            from .skills import refresh_job_matches
            refresh_job_matches(state)
        return {"message": f"Confirmed {len(args.items)} reviewed {args.kind}."}
    return db.mutate_state(save)


def _profile(args):
    from .main import invalidate_resumes
    updates = args.model_dump(exclude_none=True, exclude_unset=True)
    def save(state):
        if any(state["profile"].get(key) != value for key, value in updates.items()):
            invalidate_resumes(state)
        state["profile"].update(updates)
        return state["profile"]
    return db.mutate_state(save)


def _catalog():
    # Import route implementations lazily to avoid circular imports during app startup.
    from . import jobs, linkedin_routes as linkedin, main
    return {
        "putmeto_workspace": ("Read the applicant's profile, confirmed skills, positions, preferences, sources and local counts. No AI settings or credentials.", Empty, _workspace),
        "putmeto_resume_entries": ("Read a page of resume entries with original and proposed wording and confirmation state.", Entries, _entries),
        "putmeto_saved_jobs": ("Find saved jobs from all sources locally. Search title, company, location and description; no website request.", Query, _saved_jobs),
        "putmeto_get_job": ("Read one saved job's full description by local ID.", ById, _get_job),
        "putmeto_draft_item": ("Save a NEW unconfirmed resume entry. Use only facts supplied by the user, with faithful broader wording in enhanced. Read existing entries first to avoid duplicates.", Draft, _draft),
        "putmeto_rephrase_item": ("Rephrase an existing resume entry and make it an unconfirmed draft for review.", ById, lambda args: main.rephrase_item(args.id)),
        "putmeto_confirm_items": ("Ask the user to review and confirm these EXACT existing resume entries. Requires a visible approval; only unchanged entries can be confirmed.", ConfirmItems, _confirm_items),
        "putmeto_confirm_suggestions": ("Ask the user to confirm exact existing skill or position suggestions. Requires a visible approval.", ConfirmBag, _confirm_bag),
        "putmeto_update_profile": ("Update only profile fields explicitly supplied by the user. Omitted fields are preserved. Never invent contact information or claims.", ProfilePatch, _profile),
        "putmeto_suggest_skills": ("Suggest broad, meaningful professional skills aligned first with the CV's dedicated skills section, then supported and refined by confirmed experience. Use imported and confirmed skills when section metadata is unavailable. Consolidate implementation details, avoid redundant subskills and product lists, and clearly label any unverified related options. Optionally prioritize a saved job_id. Save drafts with evidence and rationale; the user confirms which they know.", main.SkillSuggestionsInput, main.suggest_skills),
        "putmeto_suggest_positions": ("Suggest relevant positions from confirmed resume entries; save unconfirmed suggestions for review.", Empty, lambda _: main.suggest_positions()),
        "putmeto_preferences": ("Save job preferences explicitly requested by the user: industry/academia, location and remote-only.", main.PreferencesInput, main.save_preferences),
        "putmeto_discover_jobs": ("Search the user's enabled job sources and save matching jobs. LinkedIn defaults to all roles in the United States and Europe.", Empty, lambda _: jobs.discover_jobs()),
        "putmeto_tailor_resume": ("Prepare a resume PDF for a saved job ID, using only confirmed facts and wording. Return its review/download links; does not apply.", ById, _tailor),
        "linkedin_inspect": ("Inspect the current LinkedIn page, including job details and supported visible application field references. Stop when requires_action is true.", Empty, lambda _: linkedin.inspect_page()),
        "linkedin_study": ("Study bounded rendered LinkedIn headings and layout to understand the current job page. No hidden values or navigation.", Empty, lambda _: linkedin.study_page()),
        "linkedin_read_job": ("Open a LinkedIn job URL, read visible details and optionally save them locally. Stop on login, checkpoint or rate limits.", linkedin.ReadJob, linkedin.read_job),
        "linkedin_fill": ("Request review of exact user-supplied answers, then fill supported application fields from a fresh snapshot. Never submits or advances forms. Never guess answers.", linkedin.FillFields, linkedin.fill_fields),
        "linkedin_search": ("Search and save a bounded batch of LinkedIn jobs. Default: United States and Europe. Results matching neither the keywords nor a confirmed target position are skipped. Stop on requires_action; never promise every listing.", jobs.LinkedInSearchBody, jobs.search_linkedin_jobs),
        "linkedin_save_search": ("Save a named LinkedIn search to repeat later; does not visit LinkedIn.", linkedin.SavedSearch, linkedin.save_search),
        "linkedin_saved_searches": ("List local named LinkedIn searches and last-run status.", Empty, lambda _: linkedin.saved_searches()),
        "linkedin_run_search": ("Run an existing named LinkedIn search once by local ID and save its results.", ById, lambda args: linkedin.run_search(args.id)),
    }


async def _tailor(args):
    from .main import tailor_resume
    result = await tailor_resume(args.id)
    return {"message": result["message"], "resume_url": f"/api/jobs/{args.id}/resume", "pdf_url": f"/api/jobs/{args.id}/resume.pdf"}


REVIEWED_TOOLS = {"linkedin_fill", "putmeto_confirm_items", "putmeto_confirm_suggestions"}


def tool_specs() -> list[dict]:
    return [{
        "type": "function", "name": name, "description": description,
        "inputSchema": model.model_json_schema(), "requires_approval": name in REVIEWED_TOOLS,
    } for name, (description, model, _) in _catalog().items()]


def approval_description(name: str, args: dict) -> str | None:
    if name not in REVIEWED_TOOLS:
        return None
    # Validate before asking the user to review; no side effects here.
    value = _catalog()[name][1].model_validate(args).model_dump()
    if name == "putmeto_confirm_suggestions" and value["kind"] == "skills":
        current = {skill["id"]: skill for skill in db.get_state()["skills"]}
        # The approval must explain adjacent options instead of silently
        # presenting model-suggested products as the applicant's qualifications.
        value["items"] = [{**item, **{
            field: current[item["id"]].get(field, "")
            for field in ("support", "evidence", "rationale")
        }} if item["id"] in current else item for item in value["items"]]
    title = {
        "linkedin_fill": "Fill these exact answers on the current LinkedIn application form? This will not submit the application.",
        "putmeto_confirm_items": "Confirm these exact resume entries for use in tailored resumes?",
        "putmeto_confirm_suggestions": "Confirm these suggested skills or positions?",
    }[name]
    return title + "\n\n" + json.dumps(value, ensure_ascii=False, indent=2)


async def execute_tool(name: str, args: dict) -> dict:
    """Dispatch only validated, allowlisted operations; the bridge owns approvals."""
    catalog = _catalog()
    if name not in catalog:
        raise HTTPException(400, "Unknown application tool.")
    _, model, handler = catalog[name]
    try:
        parsed = model.model_validate(args)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid application tool arguments: " + str(exc.errors(include_input=False))) from exc
    result = handler(parsed)
    if inspect.isawaitable(result):
        result = await result
    return result
