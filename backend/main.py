"""Local web API and static application host."""

import asyncio
import copy
import io
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from . import ai, db, resumes, skills


ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 10 * 1024 * 1024
IMPORT_AI_TIMEOUT = 300
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    yield
    from .jobs import close_browsers
    from .linkedin import close_linkedin
    from .codex_bridge import get_bridge
    try:
        await get_bridge().close()
    finally:
        try:
            await close_linkedin()
        finally:
            await close_browsers()


app = FastAPI(title="PutMeTo", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    """Defend loopback services against hostile origins and DNS rebinding."""
    try:
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
    except ValueError:
        host = None
    if host not in LOCAL_HOSTS:
        return JSONResponse({"detail": "This application is available only through localhost or 127.0.0.1."}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-origin requests are not allowed. Open this application directly on localhost."}, status_code=403)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if request.method in ("POST", "PUT", "PATCH") and request.headers.get("content-length", "0") not in ("", "0"):
            if content_type not in ("application/json", "multipart/form-data"):
                return JSONResponse({"detail": "Use JSON or a multipart file upload."}, status_code=415)
        try:
            if int(request.headers.get("content-length", "0")) > MAX_UPLOAD + 1024 * 1024:
                return JSONResponse({"detail": "Request is too large. Maximum CV file size is 10 MB."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length header."}, status_code=400)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ai.AIError)
async def ai_error_handler(request: Request, error: ai.AIError):
    return JSONResponse(status_code=503, content={"detail": str(error)})


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileInput(Input):
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=300)
    headline: str = Field(default="", max_length=500)
    website: str = Field(default="", max_length=1000)
    summary: str = Field(default="", max_length=5000)


class ItemInput(Input):
    kind: Literal["experience", "project", "education", "publication"]
    title: str = Field(min_length=1, max_length=300)
    organization: str = Field(default="", max_length=300)
    start: str = Field(default="", max_length=80)
    end: str = Field(default="", max_length=80)
    original: str = Field(min_length=1, max_length=12000)


class ItemUpdate(ItemInput):
    enhanced: str = Field(default="", max_length=12000)
    confirmed: bool = False


class BagItem(Input):
    id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    confirmed: bool = False


class BagInput(Input):
    items: list[BagItem] = Field(max_length=150)


class SkillItem(BagItem):
    # Accept round-tripped display metadata. Existing server provenance is kept
    # on confirmation; newly entered skill names are marked as manual input.
    kind: Literal["technique", "language", "framework", "database", "platform", "tool", "other"] | None = None
    support: Literal["supported", "related"] | None = None
    evidence: str | None = Field(default=None, max_length=2000)
    rationale: str | None = Field(default=None, max_length=2000)
    aliases: list[str] | None = Field(default=None, max_length=8)
    origin: Literal["resume", "suggestion", "manual"] | None = None


class SkillsInput(Input):
    items: list[SkillItem] = Field(max_length=150)


class SkillSuggestionsInput(Input):
    job_id: str | None = Field(default=None, min_length=1, max_length=100)


class PreferencesInput(Input):
    track: Literal["industry", "academia"] = "industry"
    location: str = Field(default="", max_length=300)
    remote_only: bool = False


class SettingsInput(Input):
    provider: Literal["codex", "ollama", "compatible"]
    base_url: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_service(self):
        if self.provider == "codex":
            self.base_url = ""
            self.api_key = None
        else:
            self.base_url = ai.validate_base_url(self.base_url)
            if not self.model:
                raise ValueError("Choose an AI model.")
        return self


def sanitized_state(state: dict) -> dict:
    state = copy.deepcopy(state)
    state.pop("codex_chat", None)  # Chat has its own bounded endpoint.
    settings = state["settings"]
    settings["api_key_set"] = bool(settings.pop("api_key", ""))
    return state


def find_item(items: list, item_id: str, label: str = "Item") -> dict:
    item = next((item for item in items if item["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, f"{label} not found.")
    return item


def invalidate_resumes(state: dict) -> None:
    """A resume must never continue using facts the user changed or unconfirmed."""
    for job in state["jobs"]:
        job.pop("resume", None)
        if job.get("status") == "prepared":
            job["status"] = "saved"


@app.get("/api/state")
def state_endpoint():
    return sanitized_state(db.get_state())


@app.get("/api/health")
def health_endpoint():
    state = db.get_state()
    return {"status": "ok", "local": True, "ai_configured": state["settings"].get("provider") == "codex" or bool(state["settings"].get("model")), "version": "0.1.0"}


@app.put("/api/profile")
def save_profile(payload: ProfileInput):
    def update(state):
        profile = payload.model_dump()
        if state["profile"] != profile:
            invalidate_resumes(state)
        state["profile"] = profile
        return profile
    return db.mutate_state(update)


def _extract_text(content: bytes, filename: str, content_type: str = "") -> str:
    # Exported PDFs can have no extension or a browser-supplied filename. Detect
    # their actual header instead of rejecting a valid PDF based on its name.
    filename = filename.strip().lower()
    content_type = content_type.split(";", 1)[0].strip().lower()
    is_pdf = b"%PDF-" in content[:1024] or filename.endswith(".pdf") or content_type == "application/pdf"
    if is_pdf:
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted and not reader.decrypt(""):
                raise HTTPException(422, "This PDF is password protected. Upload an unlocked copy or a text file.")
            if len(reader.pages) > 100:
                raise HTTPException(422, "Upload a CV with at most 100 pages.")
            parts, total = [], 0
            for page in reader.pages:
                text = page.extract_text() or ""
                parts.append(text)
                total += len(text)
                if total > 70000:
                    raise HTTPException(422, "This CV is too long. Upload at most 70,000 characters of text.")
            text = "\n".join(parts)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "This PDF could not be read. Export a fresh PDF or upload a UTF-8 .txt file.") from exc
    elif (filename.endswith(".docx") or
          content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or
          content.startswith(b"PK\x03\x04")):
        from .documents import extract_docx
        text = extract_docx(content)
    elif filename.endswith(".doc") or content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise HTTPException(415, "This is an older Word .doc file. Save it as Word .docx or PDF, then upload it again.")
    elif filename.endswith(".txt") or content_type == "text/plain":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Save the text file with UTF-8 encoding and try again.") from exc
        if len(text) > 70000:
            raise HTTPException(422, "This CV is too long. Upload at most 70,000 characters of text.")
    else:
        raise HTTPException(415, "This file could not be recognized as PDF, Word .docx, or a UTF-8 text CV. Export it as .docx or PDF and try again.")
    if len(text.strip()) < 30:
        raise HTTPException(422, "No readable CV text was found. Scanned PDFs need OCR first; you can also paste entries manually or upload a text file.")
    return text.strip()


async def read_cv(file: UploadFile) -> str:
    try:
        content = await file.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "The file is too large. Maximum file size is 10 MB.")
        text = await run_in_threadpool(_extract_text, content, file.filename or "", file.content_type or "")
    finally:
        await file.close()
    return text


@app.post("/api/import")
async def import_resume(file: UploadFile = File(...)):
    text = await read_cv(file)
    try:
        extracted = await asyncio.wait_for(ai.extract_resume(db.get_state()["settings"], text), IMPORT_AI_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise ai.AIError("Resume extraction timed out. No entries were imported. Try a shorter CV or another model in Settings.") from exc
    if not extracted.items and not extracted.skills:
        raise HTTPException(422, "The AI found no resume entries or technical skills. Try another model or add your experience manually.")
    def save(state):
        for key, value in extracted.profile.model_dump().items():
            if value and not state["profile"].get(key):
                state["profile"][key] = value
        for entry in extracted.items:
            state["items"].append({"id": uuid4().hex, **entry.model_dump(), "confirmed": False})
        skill_count, _ = skills.merge_candidates(state, extracted.skills, origin="resume")
        invalidate_resumes(state)
        return skill_count
    skill_count = db.mutate_state(save)
    return {"count": len(extracted.items), "skill_count": skill_count, "message": f"Imported {len(extracted.items)} draft entries and {skill_count} technical skills. Review and confirm the wording and skills before using them in applications."}


@app.post("/api/import/skills")
async def import_cv_skills(file: UploadFile = File(...)):
    """Recover the skills section without appending duplicate experience."""
    text = await read_cv(file)
    try:
        extracted = await asyncio.wait_for(ai.extract_skills(db.get_state()["settings"], text), IMPORT_AI_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise ai.AIError("Reading CV skills timed out. No skills were imported. Try again or use a shorter CV.") from exc
    if not extracted:
        raise HTTPException(422, "No technical skills were found. Check that the CV's skills section contains readable text, or add skills manually.")
    count, updated = db.mutate_state(lambda state: skills.merge_candidates(state, extracted, origin="resume"))
    return {"count": count, "skill_count": count, "updated": updated, "message": f"Imported {count} new skills and updated {updated} draft suggestions from your CV. Your saved experience was kept. Review the skills before confirming them."}


@app.post("/api/items", status_code=201)
async def create_item(payload: ItemInput):
    entry = {"id": uuid4().hex, **payload.model_dump(), "confirmed": False}
    warning = None
    try:
        entry["enhanced"] = await ai.enhance_item(db.get_state()["settings"], entry)
    except ai.AIError as error:
        entry["enhanced"] = entry["original"]
        warning = f"Saved your original wording as a draft. {error}"
    db.mutate_state(lambda state: state["items"].append(entry))
    return {**entry, **({"warning": warning} if warning else {})}


@app.put("/api/items/{item_id}")
async def update_item(item_id: str, payload: ItemUpdate):
    snapshot = db.get_state()
    previous = find_item(snapshot["items"], item_id)
    entry = {"id": item_id, **payload.model_dump()}
    source_changed = any(entry[key] != previous.get(key, "") for key in ItemInput.model_fields)
    warning = None
    if source_changed:
        entry["confirmed"] = False
        try:
            entry["enhanced"] = await ai.enhance_item(snapshot["settings"], entry)
        except ai.AIError as error:
            entry["enhanced"] = entry["original"]
            warning = f"Saved your updated original as a draft. {error}"
    elif not entry["enhanced"]:
        entry["enhanced"] = previous.get("enhanced") or entry["original"]
    def save(state):
        current = find_item(state["items"], item_id)
        if current != previous:
            raise HTTPException(409, "This entry changed while the AI was working. Refresh and try again.")
        if current != entry and (current.get("confirmed") or entry["confirmed"]):
            invalidate_resumes(state)
        current.clear()
        current.update(entry)
    db.mutate_state(save)
    return {**entry, **({"warning": warning} if warning else {})}


@app.post("/api/items/{item_id}/enhance")
async def rephrase_item(item_id: str):
    snapshot = db.get_state()
    previous = find_item(snapshot["items"], item_id)
    enhanced = await ai.enhance_item(snapshot["settings"], previous)
    def save(state):
        current = find_item(state["items"], item_id)
        if current != previous:
            raise HTTPException(409, "This entry changed while the AI was working. Refresh and try again.")
        if current.get("confirmed"):
            invalidate_resumes(state)
        current.update(enhanced=enhanced, confirmed=False)
        return current
    return db.mutate_state(save)


@app.delete("/api/items/{item_id}")
def delete_item(item_id: str):
    def remove(state):
        item = find_item(state["items"], item_id)
        state["items"].remove(item)
        if item.get("confirmed"):
            invalidate_resumes(state)
    db.mutate_state(remove)
    return {"message": "Entry deleted."}


async def _suggest(kind: str):
    snapshot = db.get_state()
    if not any(item.get("confirmed") for item in snapshot["items"]):
        raise HTTPException(400, "Confirm at least one resume entry before generating suggestions.")
    names = await ai.suggest(snapshot["settings"], snapshot, kind)
    def save(state):
        seen = {item["name"].casefold() for item in state[kind]}
        added = []
        for name in names:
            if name.casefold() not in seen:
                item = {"id": uuid4().hex, "name": name, "confirmed": False}
                state[kind].append(item)
                added.append(item)
                seen.add(name.casefold())
        return {"items": state[kind], "count": len(added), "message": f"Added {len(added)} suggestions to review."}
    return db.mutate_state(save)


@app.post("/api/suggestions/skills")
async def suggest_skills(payload: SkillSuggestionsInput | None = None):
    snapshot = db.get_state()
    has_background = any(item.get("confirmed") for item in snapshot["items"]) or any(
        item.get("confirmed") or item.get("origin") == "resume" for item in snapshot["skills"]
    )
    if not has_background:
        raise HTTPException(400, "Import skills from your CV, add a skill you know, or confirm an experience entry before requesting suggestions.")
    job = find_item(snapshot["jobs"], payload.job_id, "Job") if payload and payload.job_id else None
    candidates = await ai.suggest_skills(snapshot["settings"], snapshot, job)
    def save(state):
        if state["items"] != snapshot["items"] or state["skills"] != snapshot["skills"]:
            raise HTTPException(409, "Your background changed while skills were being suggested. Try again to use the latest information.")
        count, updated = skills.merge_candidates(state, candidates, origin="suggestion")
        return {"items": state["skills"], "count": count, "updated": updated,
                "message": f"Added {count} skill suggestions and updated {updated} drafts. Review techniques supported by your background and confirm only the related tools you know."}
    return db.mutate_state(save)


@app.post("/api/suggestions/positions")
async def suggest_positions():
    return await _suggest("positions")


def _save_bag(kind: str, payload: BagInput | SkillsInput):
    items, names, ids = [], set(), set()
    for entry in payload.items:
        name_key = skills.normalized_name(entry.name) if kind == "skills" else entry.name.casefold()
        if name_key in names:
            continue
        item_id = entry.id or uuid4().hex
        if item_id in ids:
            raise HTTPException(422, "Each entry must have a unique ID.")
        items.append({"id": item_id, "name": entry.name, "confirmed": entry.confirmed})
        names.add(name_key)
        ids.add(item_id)
    def save(state):
        if kind == "skills":
            previous = {item["id"]: item for item in state[kind]}
            for item in items:
                old = previous.get(item["id"])
                if old and old["name"] == item["name"]:
                    item.update({field: old[field] for field in skills.METADATA if field in old})
                else:
                    item.update(origin="manual", support="supported", kind="other", evidence="Added by you.", rationale="", aliases=[])
            removed = {skills.normalized_name(item["name"]) for item in state[kind]
                       if item["id"] not in ids and not item.get("confirmed")}
            retained = {skills.normalized_name(item["name"]) for item in items}
            dismissed = state.setdefault("dismissed_skills", [])
            state["dismissed_skills"] = list(dict.fromkeys([*dismissed, *sorted(removed)]))[-1000:]
            state["dismissed_skills"] = [name for name in state["dismissed_skills"] if name not in retained]
        if kind == "skills" and [item for item in state[kind] if item.get("confirmed")] != [item for item in items if item["confirmed"]]:
            invalidate_resumes(state)
        state[kind] = items
        if kind == "skills":
            skills.refresh_job_matches(state)
        return {"items": items}
    return db.mutate_state(save)


@app.put("/api/skills")
def save_skills(payload: SkillsInput):
    return _save_bag("skills", payload)


@app.put("/api/positions")
def save_positions(payload: BagInput):
    return _save_bag("positions", payload)


@app.put("/api/preferences")
def save_preferences(payload: PreferencesInput):
    def save(state):
        state["preferences"] = payload.model_dump()
        return state["preferences"]
    return db.mutate_state(save)


@app.put("/api/settings")
def save_settings(payload: SettingsInput):
    def save(state):
        old = state["settings"]
        # A saved credential belongs to its configured server, not a newly typed URL.
        changed_service = old.get("base_url") != payload.base_url or old.get("provider") != payload.provider
        key = "" if payload.clear_api_key or changed_service else old.get("api_key", "")
        if payload.api_key and not payload.clear_api_key:
            key = payload.api_key
        if payload.provider == "codex":
            key = ""
        state["settings"] = {"provider": payload.provider, "base_url": payload.base_url, "model": payload.model, "api_key": key}
        return sanitized_state(state)["settings"]
    return db.mutate_state(save)


@app.post("/api/settings/test")
async def test_settings():
    settings = db.get_state()["settings"]
    await ai.test_connection(settings)
    return {"message": f"Connected to {settings['model'] or 'Codex'}. Structured responses are working."}


@app.post("/api/jobs/{job_id}/tailor")
async def tailor_resume(job_id: str):
    snapshot = db.get_state()
    job = find_item(snapshot["jobs"], job_id, "Job")
    if not any(item.get("confirmed") for item in snapshot["items"]):
        raise HTTPException(400, "Confirm at least one resume entry before tailoring a resume.")
    order = await ai.order_resume(snapshot["settings"], snapshot, job)
    resume = resumes.build_resume(snapshot, job)
    item_rank = {item_id: rank for rank, item_id in enumerate(dict.fromkeys(order.item_ids))}
    skill_rank = {skill_id: rank for rank, skill_id in enumerate(dict.fromkeys(order.skill_ids))}
    resume["items"].sort(key=lambda item: item_rank.get(item["id"], len(item_rank)))
    skills = [skill for skill in snapshot["skills"] if skill.get("confirmed")]
    skills.sort(key=lambda skill: skill_rank.get(skill["id"], len(skill_rank)))
    resume["skills"] = [skill["name"] for skill in skills]
    def save(state):
        current_job = find_item(state["jobs"], job_id, "Job")
        if any(state[key] != snapshot[key] for key in ("profile", "items", "skills")):
            raise HTTPException(409, "Your profile changed while tailoring. Try again to use the latest confirmed content.")
        current_job["resume"] = resume
        if current_job.get("status") != "applied":
            current_job["status"] = "prepared"
    db.mutate_state(save)
    return {"message": "Resume prepared with your confirmed wording, with the most relevant entries and skills first. Review it before applying.", "resume": resume}


def _resume_for_job(job_id: str) -> dict:
    job = find_item(db.get_state()["jobs"], job_id, "Job")
    if not job.get("resume"):
        raise HTTPException(400, "Tailor a resume for this job first.")
    return job["resume"]


def _pdf_response(resume: dict) -> Response:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", resume["profile"].get("name", "")).strip("-") or "resume"
    return Response(resumes.render_pdf(resume), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}-resume.pdf"'})


@app.get("/api/resume", response_class=HTMLResponse)
def general_resume():
    return resumes.render_html(resumes.build_resume(db.get_state()))


@app.get("/api/resume.pdf")
def general_pdf():
    return _pdf_response(resumes.build_resume(db.get_state()))


@app.get("/api/jobs/{job_id}/resume", response_class=HTMLResponse)
def job_resume(job_id: str):
    return resumes.render_html(_resume_for_job(job_id))


@app.get("/api/jobs/{job_id}/resume.pdf")
def job_pdf(job_id: str):
    return _pdf_response(_resume_for_job(job_id))


from .jobs import router as jobs_router  # noqa: E402
from .linkedin_routes import router as linkedin_agent_router  # noqa: E402
from .codex_routes import router as codex_router  # noqa: E402

app.include_router(jobs_router)
app.include_router(linkedin_agent_router)
app.include_router(codex_router)
app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "static" / "index.html")
