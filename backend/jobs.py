"""Job discovery, local application tracking, and optional browser assistance."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import db
from backend.browser import browser_status, close_browsers, discover_jsonld, start_application
from backend.network import SourceError, fetch_json, validate_public_url, validate_url_shape
from backend.linkedin import canonical_job_url, linkedin_status, search_linkedin


router = APIRouter(prefix="/api")
_discovery_lock = asyncio.Lock()
_apply_lock = asyncio.Lock()
_linkedin_batch_lock = asyncio.Lock()
_REMOTIVE_TTL = 6 * 60 * 60
_REMOTIVE_URL = "https://remotive.com/api/remote-jobs"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Source(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=100)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["remotive", "greenhouse", "lever", "camoufox", "linkedin"]
    url: str = Field(default="", max_length=2048)
    enabled: bool = True


class SourcesBody(BaseModel):
    items: list[Source] = Field(max_length=20)


class ManualJob(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str = Field(default="", max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    description: str = Field(min_length=1, max_length=60_000)
    salary: str = Field(default="", max_length=300)
    job_type: str = Field(default="", max_length=100)


class JobStatus(BaseModel):
    status: Literal["discovered", "saved", "prepared", "applied", "archived"]


class LinkedInSearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    keywords: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    regions: list[str] | None = Field(default=None, min_length=1, max_length=5)
    remote_only: bool = False
    limit: int = Field(default=10, ge=1, le=25)
    save: bool = True

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, value):
        if value is None:
            return value
        result = []
        for region in value:
            region = region.strip()
            if not region or len(region) > 300:
                raise ValueError("Each region must be between 1 and 300 characters.")
            if region not in result:
                result.append(region)
        return result


async def search_linkedin_regions(keywords: str, regions: list[str], remote_only: bool, limit: int) -> dict:
    """Search each selected geography; callers decide which results are relevant."""
    if _linkedin_batch_lock.locked():
        raise SourceError("A LinkedIn search is already running. Wait for it to finish.")
    async with _linkedin_batch_lock:
        collected, warnings, search_urls = [], [], []
        requires_action, completed = False, 0
        messages = []
        for region in regions:
            try:
                result = await search_linkedin(keywords, location=region, remote_only=remote_only, limit=limit)
            except SourceError as exc:
                warnings.append(f"{region}: {exc}")
                continue
            completed += 1
            collected.extend(result.get("jobs", [])[:limit])
            if result.get("search_url"):
                search_urls.append(result["search_url"])
            warnings.extend(f"{region}: {warning}" for warning in result.get("warnings", []))
            if result.get("message"):
                messages.append(f"{region}: {result['message']}")
            if result.get("requires_action"):
                requires_action = True
                break
        if not completed:
            raise SourceError(" ".join(warnings) or "LinkedIn could not be reached.")
        return {"jobs": collected, "requires_action": requires_action, "message": " ".join(messages), "search_url": search_urls[-1] if search_urls else "", "search_urls": search_urls, "warnings": warnings}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value) -> str:
    parser = _TextParser()
    parser.feed(html.unescape(str(value or ""))[:150_000])
    return re.sub(r"\n\s*\n", "\n\n", "".join(parser.parts)).strip()[:60_000]


def canonical_url(url: str) -> str:
    parts = urlsplit(validate_url_shape(url))
    if parts.hostname and (parts.hostname == "linkedin.com" or parts.hostname.endswith(".linkedin.com")) and parts.path.startswith("/jobs/view/"):
        try:
            return canonical_job_url(url)
        except SourceError:
            pass  # Preserve previously saved manual URLs that have no numeric ID.
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"ref", "referrer", "source"})
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urlencode(query), ""))


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_term(text: str, term: str) -> bool:
    return bool(term.strip()) and re.search(r"(?<!\w)" + re.escape(term.strip().casefold()) + r"(?!\w)", text.casefold()) is not None


TITLE_FILLER = {"a", "and", "the", "of", "in", "for", "senior", "junior", "sr", "jr", "lead", "principal"}


def confirmed_positions(state: dict) -> list[str]:
    return [item["name"] for item in state.get("positions", []) if item.get("confirmed")]


def position_match(title: str, positions: list[str]) -> float:
    """How much of the closest target position a job title contains, from 0 to 1."""
    title_words = _words(title)
    best = 0.0
    for position in positions:
        if _contains_term(title, position):
            return 1.0
        words = _words(position) - TITLE_FILLER
        if words:
            best = max(best, len(words & title_words) / len(words))
    return best


def _matches_search(title: str, keywords: str) -> bool:
    """A result belongs to a keyword search when its title shares a meaningful searched word."""
    return bool((_words(keywords) - TITLE_FILLER - {"or", "not"}) & _words(title))


def positions_query(positions: list[str]) -> str:
    """One LinkedIn keyword search for any target position, within LinkedIn's 300-character limit."""
    terms = [" ".join(re.sub(r"[()\"]", " ", name).split()) for name in positions]
    terms = [term for term in terms if term]
    if len(terms) == 1:
        return terms[0][:300]
    query = ""
    for term in terms:
        candidate = f"{query} OR ({term})" if query else f"({term})"
        if len(candidate) > 300:
            break
        query = candidate
    return query


def score_job(job: dict, state: dict) -> tuple[int, list[str], bool]:
    """Transparent keyword overlap: a relevance aid, not an AI hiring prediction."""
    skills = [item for item in state.get("skills", []) if item.get("confirmed")]
    title = str(job.get("title", ""))
    text = f"{title} {job.get('description', '')}"
    best = position_match(title, confirmed_positions(state))
    from .skills import normalized_name
    skill_text = normalized_name(text)
    matched = [skill["name"] for skill in skills if any(
        _contains_term(skill_text, normalized_name(name))
        for name in [skill["name"], *skill.get("aliases", [])]
    )]
    score = round(70 * best + (30 * min(len(matched) / min(len(skills), 6), 1) if skills else 0))
    relevant = best >= 0.5
    preferences = state.get("preferences", {})
    remote = job.get("remote", False) or _contains_term(str(job.get("location", "")), "remote") or _contains_term(title, "remote")
    if preferences.get("remote_only") and not remote:
        relevant = False
    location = preferences.get("location", "").strip()
    job_location = str(job.get("location", "")).casefold()
    # Keep worldwide remote roles eligible, but respect stated regional limits.
    if location and not any(term in job_location for term in ["worldwide", "anywhere", "global"]):
        if not any(part.strip().casefold() in job_location for part in location.split(",") if part.strip()):
            relevant = False
    return score, matched, relevant


def make_job(fields: dict, source: str, state: dict, status="discovered") -> dict:
    job = {
        "id": str(uuid.uuid4()), "title": plain_text(fields.get("title"))[:300],
        "company": plain_text(fields.get("company"))[:300], "location": plain_text(fields.get("location"))[:300],
        "url": validate_url_shape(str(fields.get("url", ""))), "source": source,
        "description": plain_text(fields.get("description")), "salary": plain_text(fields.get("salary"))[:300],
        "job_type": plain_text(fields.get("job_type"))[:100], "posted_at": str(fields.get("posted_at", ""))[:100],
        "status": status, "created_at": now(), "remote": bool(fields.get("remote", False)),
    }
    if not job["title"]:
        raise SourceError("The source returned a job without a title.")
    job["match_score"], job["matched_skills"], _ = score_job(job, state)
    return job


def enrich_linkedin_job(existing: dict, incoming: dict, state: dict) -> None:
    """A completed login can fill gaps in a previously saved public job card."""
    if incoming.get("source") != "LinkedIn":
        return
    gained_description = not existing.get("description") and bool(incoming.get("description"))
    for field in ("description", "company", "location", "salary", "job_type", "posted_at", "remote"):
        if not existing.get(field) and incoming.get(field):
            existing[field] = incoming[field]
    existing["match_score"], existing["matched_skills"], _ = score_job(existing, state)
    if gained_description and existing.get("status") != "applied":
        existing.pop("resume", None)
        if existing.get("status") == "prepared":
            existing["status"] = "saved"


def _board_token(url: str, kind: str) -> tuple[str, bool]:
    parts = urlsplit(validate_url_shape(url))
    path = [part for part in parts.path.split("/") if part]
    if kind == "greenhouse":
        if parts.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
            token = path[0] if path else ""
        elif parts.hostname in {"boards-api.greenhouse.io", "boards-api.eu.greenhouse.io"} and len(path) >= 3 and path[:2] == ["v1", "boards"]:
            token = path[2]
        else:
            raise SourceError("Use a Greenhouse board URL such as https://job-boards.greenhouse.io/company.")
    else:
        if parts.hostname in {"jobs.lever.co", "jobs.eu.lever.co"}:
            token = path[0] if path else ""
        elif parts.hostname in {"api.lever.co", "api.eu.lever.co"} and len(path) >= 3 and path[:2] == ["v0", "postings"]:
            token = path[2]
        else:
            raise SourceError("Use a Lever board URL such as https://jobs.lever.co/company.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", token):
        raise SourceError("The board URL must include its company identifier.")
    return token, ".eu." in (parts.hostname or "")


async def _remotive() -> list[dict]:
    cache = db.DATA_DIR / "remotive-cache.json"
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if 0 <= time.time() - float(cached["fetched_at"]) < _REMOTIVE_TTL and isinstance(cached["jobs"], list):
            return cached["jobs"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data = await fetch_json(_REMOTIVE_URL)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError("Remotive returned an unexpected response.")
    jobs = data["jobs"][:5000]
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps({"fetched_at": time.time(), "jobs": jobs}), encoding="utf-8")
    os.replace(temporary, cache)
    return jobs


def _jsonld_fields(posting: dict) -> dict:
    organization = posting.get("hiringOrganization", {})
    locations = posting.get("jobLocation", [])
    if isinstance(locations, dict):
        locations = [locations]
    location_names = []
    for location in locations if isinstance(locations, list) else []:
        if isinstance(location, dict):
            address = location.get("address", {})
            if isinstance(address, dict):
                location_names.append(", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)))
            elif isinstance(address, str):
                location_names.append(address)
    remote = str(posting.get("jobLocationType", "")).casefold() == "telecommute"
    if remote:
        location_names.insert(0, "Remote")
    salary = posting.get("baseSalary", {})
    salary_text = ""
    if isinstance(salary, dict):
        value = salary.get("value", {})
        if isinstance(value, dict):
            salary_text = " ".join(str(part) for part in (salary.get("currency", ""), value.get("value") or value.get("minValue", ""), "–" if value.get("maxValue") else "", value.get("maxValue", ""), value.get("unitText", "")) if part)
    return {
        "title": posting.get("title", ""), "company": organization.get("name", "") if isinstance(organization, dict) else organization,
        "location": ", ".join(filter(None, location_names)), "remote": remote,
        "url": posting.get("url") or posting.get("_page_url", ""), "description": posting.get("description", ""),
        "salary": salary_text, "job_type": posting.get("employmentType", ""), "posted_at": posting.get("datePosted", ""),
    }


async def _fetch_source(source: dict) -> list[dict]:
    kind, url = source["kind"], source.get("url", "")
    if kind == "linkedin":
        state = db.get_state()
        preferences = state.get("preferences", {})
        location = str(preferences.get("location", "")).strip()
        result = await search_linkedin_regions(positions_query(confirmed_positions(state)),
                                               [location] if location else ["United States", "Europe"],
                                               bool(preferences.get("remote_only")), 10)
        if result.get("requires_action") or result.get("warnings"):
            # Discovery accepts lists; preserve partial results while surfacing feedback.
            source["_warnings"] = [result["message"], *result.get("warnings", [])]
        return result["jobs"]
    if kind == "remotive":
        return [{"title": item.get("title"), "company": item.get("company_name"), "url": item.get("url"), "description": item.get("description"), "location": item.get("candidate_required_location"), "salary": item.get("salary"), "job_type": item.get("job_type"), "posted_at": item.get("publication_date"), "remote": True} for item in await _remotive() if isinstance(item, dict)]
    if kind == "greenhouse":
        token, eu = _board_token(url, kind)
        data = await fetch_json(f"https://boards-api{'.eu' if eu else ''}.greenhouse.io/v1/boards/{token}/jobs?content=true")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError("Greenhouse returned an unexpected response.")
        return [{"title": item.get("title"), "company": source["name"], "url": item.get("absolute_url"), "description": item.get("content"), "location": (item.get("location") or {}).get("name", ""), "posted_at": item.get("updated_at")} for item in data["jobs"][:2000] if isinstance(item, dict)]
    if kind == "lever":
        token, eu = _board_token(url, kind)
        data = await fetch_json(f"https://api{'.eu' if eu else ''}.lever.co/v0/postings/{token}?mode=json&limit=500")
        if not isinstance(data, list):
            raise SourceError("Lever returned an unexpected response.")
        results = []
        for item in data[:500]:
            if not isinstance(item, dict):
                continue
            categories = item.get("categories") or {}
            description = "\n\n".join(str(part) for part in [item.get("descriptionPlain") or item.get("description", ""), *[str(entry.get("text", "")) + "\n" + str(entry.get("content", "")) for entry in item.get("lists", []) if isinstance(entry, dict)], item.get("additionalPlain", "")])
            results.append({"title": item.get("text"), "company": source["name"], "url": item.get("hostedUrl") or item.get("applyUrl"), "description": description, "location": categories.get("location", ""), "remote": item.get("workplaceType") == "remote", "job_type": categories.get("commitment", ""), "salary": item.get("salaryDescriptionPlain", ""), "posted_at": ""})
        return results
    if kind == "camoufox":
        return [_jsonld_fields(posting) for posting in await discover_jsonld(url)]
    raise SourceError("Unknown job source type.")


@router.put("/sources")
async def update_sources(body: SourcesBody):
    sources = []
    seen = set()
    try:
        for item in body.items:
            source = item.model_dump()
            if source["id"] in seen:
                raise SourceError("Each source must have a unique identifier.")
            seen.add(source["id"])
            source["name"] = source["name"].strip()
            if not source["name"]:
                raise SourceError("Give each source a name.")
            if source["kind"] == "remotive":
                source["name"], source["url"] = "Remotive", "https://remotive.com"
            elif source["kind"] == "linkedin":
                source["name"], source["url"] = "LinkedIn", "https://www.linkedin.com/jobs/search/"
            elif source["enabled"] or source["url"]:
                source["url"] = validate_url_shape(source["url"])
                if source["kind"] in {"greenhouse", "lever"}:
                    _board_token(source["url"], source["kind"])
            sources.append(source)
    except SourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.mutate_state(lambda state: state.update(sources=sources))
    return {"items": sources}


@router.post("/jobs/discover")
async def discover_jobs():
    if _discovery_lock.locked():
        raise HTTPException(409, "A discovery is already running. Wait for it to finish.")
    async with _discovery_lock:
        state = db.get_state()
        sources = [source for source in state.get("sources", []) if source.get("enabled")]
        if not sources:
            raise HTTPException(422, "Enable at least one job source first.")
        if not confirmed_positions(state):
            raise HTTPException(422, "Confirm at least one target position before discovering jobs. To browse LinkedIn without one, use Search LinkedIn.")
        warnings, candidates, unrelated = [], [], 0
        for source in sources:
            try:
                fields = await _fetch_source(source)
                warnings.extend(f"{source['name']}: {warning}" for warning in source.pop("_warnings", []))
                source_name = {"remotive": "Remotive", "linkedin": "LinkedIn"}.get(source["kind"], source["name"])
                invalid = 0
                for value in fields:
                    try:
                        job = make_job(value, source_name, state)
                        if score_job(job, state)[2]:
                            candidates.append(job)
                        else:
                            unrelated += 1
                    except (SourceError, ValueError, TypeError):
                        invalid += 1
                if not fields and source["kind"] == "camoufox":
                    warnings.append(f"{source['name']}: no JobPosting structured data found in the board or its first eight job links. Try a direct job URL or add a job manually.")
                if invalid:
                    warnings.append(f"{source['name']}: skipped {invalid} incomplete job listings.")
            except Exception as exc:
                message = str(exc) if isinstance(exc, SourceError) else "The source could not be read. Check the board URL and try again."
                warnings.append(f"{source['name']}: {message}")

        def persist(current):
            existing = {canonical_url(job["url"]): job for job in current.get("jobs", [])}
            count = 0
            for job in sorted(candidates, key=lambda value: value["match_score"], reverse=True)[:500]:
                key = canonical_url(job["url"])
                if key not in existing:
                    current["jobs"].append(job)
                    existing[key] = job
                    count += 1
                else:
                    enrich_linkedin_job(existing[key], job, current)
            return count

        count = db.mutate_state(persist)
        from . import job_matching
        job_matching.kick()
        message = f"Found {count} new matching {'job' if count == 1 else 'jobs'}."
        if unrelated:
            message += f" Skipped {unrelated} that {'does' if unrelated == 1 else 'do'} not match your target positions or location."
        if not candidates and not warnings:
            message += " Try broader target positions, a different location, or more sources."
        return {"count": count, "message": message, "warnings": warnings}


@router.get("/linkedin/status")
def linkedin_browser_status():
    return linkedin_status()


@router.post("/linkedin/search")
async def search_linkedin_jobs(body: LinkedInSearchBody):
    """Run an explicit search without requiring a completed resume or saved roles."""
    try:
        regions = body.regions if body.regions is not None else ([body.location] if body.location else ["United States", "Europe"])
        result = await search_linkedin_regions(body.keywords, regions, body.remote_only, body.limit)
    except SourceError as exc:
        raise HTTPException(503, str(exc)) from exc
    state = db.get_state()
    positions = confirmed_positions(state)
    candidates, seen, unrelated = [], set(), 0
    warnings = list(result.get("warnings", []))
    for fields in result.get("jobs", [])[:body.limit * len(regions)]:
        try:
            fields = {**fields, "url": canonical_job_url(fields["url"])}
            job = make_job(fields, "LinkedIn", state)
            # LinkedIn pads results with unrelated jobs. Keep a job whose title
            # matches the searched words or a target position; with neither to
            # compare against, the search is open browsing and keeps everything.
            if (body.keywords or positions) and not (
                _matches_search(job["title"], body.keywords) or position_match(job["title"], positions) >= 0.5
            ):
                unrelated += 1
            elif job["url"] not in seen:
                candidates.append(job)
                seen.add(job["url"])
        except (SourceError, ValueError, KeyError, TypeError):
            warnings.append("Skipped an incomplete LinkedIn result.")

    def persist(current):
        existing = {canonical_url(job["url"]): job for job in current["jobs"]}
        saved, count = [], 0
        for job in candidates:
            key = canonical_url(job["url"])
            if key in existing:
                enrich_linkedin_job(existing[key], job, current)
                saved.append(existing[key])
            else:
                current["jobs"].append(job)
                existing[key] = job
                saved.append(job)
                count += 1
        return count, saved

    count, jobs = db.mutate_state(persist) if body.save else (0, candidates)
    if body.save:
        from . import job_matching
        job_matching.kick()
    message = result.get("message", "LinkedIn search complete.")
    if body.save and candidates:
        message += f" Added {count} new jobs to your workspace; existing jobs were kept."
    if unrelated:
        message += f" Skipped {unrelated} {'result' if unrelated == 1 else 'results'} matching neither your keywords nor your target positions."
    return {"count": count, "found": len(candidates), "skipped": unrelated, "jobs": jobs, "requires_action": result.get("requires_action", False), "message": message, "search_url": result.get("search_url", ""), "search_urls": result.get("search_urls", []), "regions": regions, "warnings": warnings}


@router.post("/jobs")
async def create_job(body: ManualJob):
    try:
        fields = body.model_dump()
        fields["url"] = await validate_public_url(fields["url"])
        job = make_job(fields, "Added manually", db.get_state(), status="saved")
    except SourceError as exc:
        raise HTTPException(422, str(exc)) from exc

    def persist(state):
        key = canonical_url(job["url"])
        if any(canonical_url(existing["url"]) == key for existing in state["jobs"]):
            raise HTTPException(409, "This job URL is already in your workspace.")
        state["jobs"].append(job)
        return job

    saved = db.mutate_state(persist)
    from . import job_matching
    job_matching.kick()
    return saved


@router.patch("/jobs/{job_id}")
def update_job_status(job_id: str, body: JobStatus):
    def update(state):
        job = next((value for value in state["jobs"] if value["id"] == job_id), None)
        if not job:
            raise HTTPException(404, "Job not found.")
        if body.status == "prepared" and not job.get("resume"):
            raise HTTPException(422, "Tailor a resume before marking this job prepared.")
        if body.status == "applied":
            # This PATCH is also an explicit user confirmation for manually submitted jobs.
            application = next((value for value in reversed(state["applications"]) if value["job_id"] == job_id), None)
            if not application:
                application = {"id": str(uuid.uuid4()), "job_id": job_id, "created_at": now(), "note": "Submission confirmed by the user."}
                state["applications"].append(application)
            application.update(status="applied", updated_at=now())
        job["status"] = body.status
        return job

    return db.mutate_state(update)


@router.post("/jobs/{job_id}/apply")
async def apply_job(job_id: str):
    async with _apply_lock:
        state = db.get_state()
        job = next((value for value in state["jobs"] if value["id"] == job_id), None)
        if not job:
            raise HTTPException(404, "Job not found.")
        if not job.get("resume"):
            raise HTTPException(422, "Tailor and review a resume for this job before opening its application.")
        if job.get("status") == "applied":
            raise HTTPException(409, "This job is already marked applied.")
        from backend.resumes import render_pdf

        application = {"id": str(uuid.uuid4()), "job_id": job_id, "status": "in_progress", "created_at": now(), "updated_at": now(), "note": "Browser opened. Submission still needs your confirmation."}
        try:
            result = await start_application(application["id"], job["url"], state["profile"], render_pdf(job["resume"]))
        except SourceError as exc:
            raise HTTPException(503, str(exc)) from exc
        db.mutate_state(lambda current: current["applications"].append(application))
        attachment = "Your tailored resume was attached." if result["resume_attached"] else "Download your tailored resume here and attach it on the site."
        return {"application": application, "message": f"Application browser opened; filled {result['filled_fields']} common fields. {attachment} Review the form, submit on the site, then mark the application submitted in PutMeTo."}


@router.post("/applications/{application_id}/confirm")
def confirm_application(application_id: str):
    def update(state):
        application = next((item for item in state["applications"] if item["id"] == application_id), None)
        if not application:
            raise HTTPException(404, "Application not found.")
        job = next((item for item in state["jobs"] if item["id"] == application["job_id"]), None)
        if not job:
            raise HTTPException(404, "The application job no longer exists.")
        application.update(status="applied", updated_at=now(), note="Submission confirmed by the user.")
        job["status"] = "applied"
        return application

    return {"application": db.mutate_state(update), "message": "Application marked submitted based on your confirmation."}


@router.get("/browser/status")
def get_browser_status():
    return browser_status()
