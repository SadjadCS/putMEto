"""Check how a job's resume reads to an applicant tracking system (ATS).

Employers' own ATS rankings can't be seen, so this checks the two things that
decide whether a resume surfaces in them. Parsing: the resume PDF is read back
the way an ATS parser reads it, and the contact details, section titles, and
dates must come through. Keywords: the AI lists the terms an ATS search for the
job would use, in the posting's wording, and each one is looked up literally in
the PDF's text, as ATS searches are. A term missing from the resume but written
in the master CV can be added to that job's resume skills, in the CV's own
wording; nothing else is ever added. A term the resume writes only under
another name (LLM for Large Language Models) counts half, since searches use
the posting's words. The score is the share of terms found, with required terms
counting double. Each job gets two scores: before, for the resume as tailored,
and after, for the resume as sent, with similar terms, added keywords, and
Goldmove skills. A job without a tailored resume is checked with your general
resume.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import ai, db, resumes, skills


LEVEL = "medium"   # Listing a posting's keywords is light work, even on the strongest model.
TIMEOUT = 10 * 60
WEIGHT = {"required": 2, "preferred": 1}
SEPARATOR = r"[\s\-–—/]*"  # "multi-agent", "multi agent", and "multiagent" are the same term.

router = APIRouter(prefix="/api/jobs")


class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: bool = False


class AddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terms: list[str] | None = Field(default=None, max_length=40)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def terms_basis(job: dict) -> str:
    """What the keywords were taken from: the posting."""
    return _hash([job.get("title", ""), job.get("description", "")])


def score_basis(job: dict, state: dict, master: str | None = None) -> str:
    """What a score was computed from: the job's resume (or your master CV), the keywords, and your similar terms."""
    from .term_bags import master_key
    resume = job.get("resume")
    content = {key: value for key, value in resume.items() if key != "created_at"} if resume else ["master", master or master_key(state)]
    return _hash([content, (job.get("ats") or {}).get("terms_basis"), (state.get("term_bags") or {}).get("basis")])


def has_terms(job: dict) -> bool:
    return (job.get("ats") or {}).get("terms_basis") == terms_basis(job)


def current(job: dict, state: dict, master: str | None = None) -> dict | None:
    """The stored scores, {"score": after, "before": before}, while the resume and the posting are unchanged."""
    check = job.get("ats") or {}
    if has_terms(job) and "score" in check and check.get("score_basis") == score_basis(job, state, master):
        return {"score": check["score"], "before": check.get("before", check["score"])}
    return None


def current_score(job: dict, state: dict, master: str | None = None) -> int | None:
    found = current(job, state, master)
    return found["score"] if found else None


def base_resume(state: dict, job: dict) -> dict:
    """The job's tailored resume, or your general resume while it has none."""
    return job["resume"] if job.get("resume") else resumes.build_resume(state, job)


def before_resume(state: dict, job: dict) -> dict:
    """The resume before PutMeTo worked on its keywords: no similar-term choices, added keywords, or Goldmove skills."""
    resume = copy.deepcopy(base_resume(state, job))
    added = {skills.normalized_name(name) for name in resume.get("added_keywords", [])}
    added |= {skills.normalized_name(skill["name"]) for skill in state["skills"] if skill.get("origin") == "goldmove"}
    resume["skills"] = [name for name in resume.get("skills", []) if skills.normalized_name(name) not in added]
    return resume


def pattern(term: str) -> re.Pattern:
    """A literal, whole-word search for a term, allowing singular or plural and hyphen or space variations."""
    words = [word for word in re.split(r"[\s\-–—/]+", term.strip()) if word]
    suffix = "(?:s|es)?"
    if words and len(words[-1]) > 3 and words[-1].isalpha() and words[-1][-1:].lower() == "s" and words[-1][-2:].lower() != "ss":
        # "systems" also finds "system"; "databases" finds "database" and "processes" finds "process".
        last = words[-1]
        words[-1], suffix = (last[:-2], "(?:es|e|s)?") if last[-2:].lower() == "es" else (last[:-1], "(?:s|es)?")
    letters = sum(character.isalpha() for character in term)
    # Short terms such as Go, R, or AI must match their capitalization, or "go" and "ai" in prose would count.
    flags = 0 if letters <= 3 else re.IGNORECASE
    body = SEPARATOR.join(re.sub(r"['’]", "['’]", re.escape(word)) for word in words)  # master's and master’s
    return re.compile(rf"(?<![A-Za-z0-9]){body}{suffix}(?![A-Za-z0-9+#])", flags)


def find(term: dict, text: str) -> str | None:
    """The text that matched the term or one of its variants, if any."""
    for candidate in [term["term"], *term.get("variants", [])]:
        if candidate.strip() and (match := pattern(candidate).search(text)):
            return match.group(0)
    return None


def read_pdf(resume: dict) -> tuple[str, int]:
    """The resume PDF's text and page count, read back the way an ATS parser reads it."""
    import pypdfium2 as pdfium
    from .cv_import import _PDFIUM_LOCK
    content = resumes.render_pdf(resume)
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(content)
        try:
            pages = len(document)
            text = "\n".join(document[index].get_textpage().get_text_range() for index in range(pages))
        finally:
            document.close()
    return text, pages


def master_sources(state: dict) -> list[tuple[str, str]]:
    """The applicant's own confirmed wording, labelled by where it comes from."""
    sources = []
    profile = state["profile"]
    if profile.get("headline") or profile.get("summary"):
        sources.append(("Your profile", f"{profile.get('headline', '')}\n{profile.get('summary', '')}"))
    for item in state["items"]:
        if item.get("confirmed"):
            label = " | ".join(value for value in (item.get("title"), item.get("organization")) if value) or "An entry"
            sources.append((label, "\n".join(str(item.get(key) or "") for key in ("title", "organization", "original", "enhanced"))))
    names = [skill["name"] for skill in state["skills"] if skill.get("confirmed")]
    if names:
        sources.append(("Your skills", "\n".join(names)))
    return sources


def _check(label: str, level: str, detail: str) -> dict:
    return {"label": label, "level": level, "detail": detail}


def parse_checks(resume: dict, text: str, pages: int) -> list[dict]:
    """Whether an ATS parser gets the contact details, sections, and dates out of the PDF."""
    profile, checks = resume.get("profile") or {}, []
    flat = " ".join(text.split()).casefold()
    head = " ".join(text[:800].split()).casefold()
    name = " ".join(str(profile.get("name") or "").split())
    if name:
        found = name.casefold() in head
        checks.append(_check("Name", "ok" if found else "fail", "Read at the top of the resume." if found else "Not found at the top of the resume."))
    email = str(profile.get("email") or "").strip()
    checks.append(_check("Email", "ok" if email and email.casefold() in flat else "fail",
                         "Read as text." if email and email.casefold() in flat else "Add your email to your profile." if not email else "Not readable as text."))
    phone = re.sub(r"\D", "", str(profile.get("phone") or ""))
    if phone:
        found = phone in re.sub(r"\D", "", text)
        checks.append(_check("Phone", "ok" if found else "fail", "Read as text." if found else "Not readable as text."))
    lines = {line.strip().casefold() for line in text.splitlines() if line.strip()}
    kinds = {item.get("kind") for item in resume.get("items", [])}
    present = [(kind, resumes._section_title(resume, kind, default)) for kind, default in resumes.SECTIONS
               if (kind == "summary" and profile.get("summary")) or (kind == "skills" and resume.get("skills")) or kind in kinds]
    missing = [title for _, title in present if title.casefold() not in lines]
    unusual = [title for _, title in present if title.casefold() not in resumes.ATS_SECTION_NAMES]
    if missing:
        checks.append(_check("Section titles", "fail", f"Not read as headings: {', '.join(missing)}."))
    elif unusual:
        checks.append(_check("Section titles", "warn", f"ATS parsers look for standard names such as Experience, Education, and Skills; "
                                                       f"these may not be recognized: {', '.join(unusual)}."))
    else:
        checks.append(_check("Section titles", "ok", "Standard headings: " + ", ".join(title for _, title in present) + "."))
    dates = [str(item.get(key)).strip() for item in resume.get("items", []) for key in ("start", "end") if str(item.get(key) or "").strip()]
    unread = [date for date in dates if " ".join(date.split()).casefold() not in flat]
    checks.append(_check("Dates", "fail" if unread else "ok", f"Not readable: {', '.join(unread[:4])}." if unread else
                         f"All {len(dates)} dates read next to their entries." if dates else "No dates on this resume."))
    odd = sorted({character for character in text if character == "�" or 0xE000 <= ord(character) <= 0xF8FF})
    checks.append(_check("Characters", "fail" if odd else "ok", "Some characters don't come through as text." if odd else
                         "All text comes through, with no images or unreadable characters."))
    checks.append(_check("Length", "ok" if pages <= 2 else "warn", f"{pages} {'page' if pages == 1 else 'pages'}." +
                         ("" if pages <= 2 else " An ATS reads every page, but recruiters skim; two pages is a common limit.")))
    return checks


def evaluate(state: dict, job: dict) -> dict:
    """The parse checks, each keyword's status, and the score, for the job's current resume."""
    from . import term_bags
    check = job["ats"]
    resume = term_bags.job_resume(state, job)  # As sent: using the posting's names for your terms.
    text, pages = read_pdf(resume)
    before_text, _ = read_pdf(before_resume(state, job))
    sources = master_sources(state)
    posting = f"{job.get('title', '')}\n{job.get('description', '')}"
    terms, before = [], []
    for term in check["terms"]:
        result = {**term, "status": "missing", "wording": "", "where": ""}
        status, wording = _status(term, text, posting)
        before.append({**term, "status": _status(term, before_text, posting)[0]})
        if status != "missing":
            result.update(status=status, wording=wording)
        else:
            # Your CV may use another name for it, such as LLM for Large Language Models.
            names = [term["term"], *term.get("variants", [])]
            others = [form for bag in term_bags.bags(state) if any(term_bags._same(form, name) for form in term_bags.forms(bag) for name in names)
                      for form in term_bags.forms(bag)]
            wider = {**term, "variants": [*term.get("variants", []), *others]}
            for label, source in sources:
                if wording := find(wider, source):
                    result.update(status="addable", wording=" ".join(wording.split()), where=label)
                    break
        terms.append(result)
    return {"ready": True, "score": _score(terms), "before": _score(before), "terms": terms, "tailored": bool(job.get("resume")),
            "checks": parse_checks(resume, text, pages), "pages": pages, "wording": resume.get("wording", []),
            "model": check.get("model", ""), "effort": check.get("effort", "")}


def _status(term: dict, text: str, posting: str) -> tuple[str, str]:
    """present, variant (found under another name, with that name), or missing."""
    # The posting's own words for it: the forms it really uses (it may say AI where the keyword says Artificial Intelligence).
    own = [form for form in [term["term"], *term.get("variants", [])] if find({"term": form, "variants": []}, posting)] or [term["term"]]
    if any(find({"term": form, "variants": []}, text) for form in own):
        return "present", ""
    if other := find(term, text):
        return "variant", " ".join(other.split())
    return "missing", ""


def _score(terms: list[dict]) -> int:
    total = sum(WEIGHT[term["importance"]] for term in terms)
    credit = {"present": 1, "variant": 0.5}
    found = sum(WEIGHT[term["importance"]] * credit.get(term["status"], 0) for term in terms)
    return round(100 * found / total) if total else 100


def _find(state: dict, job_id: str) -> dict:
    job = next((item for item in state["jobs"] if item["id"] == job_id), None)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job


def _store_score(job_id: str, resume: dict | None, result: dict) -> None:
    def save(state):
        job = next((item for item in state["jobs"] if item["id"] == job_id), None)
        if job is not None and job.get("resume") == resume and has_terms(job):
            job["ats"].update(score=result["score"], before=result["before"], score_basis=score_basis(job, state), checked_at=time.time(),
                              missing=[{key: term[key] for key in ("term", "kind", "importance")}
                                       for term in result["terms"] if term["status"] == "missing"])
    db.mutate_state(save)


def report(job_id: str) -> dict:
    """The check for a job's resume, if its keywords have been listed."""
    state = db.get_state()
    job = _find(state, job_id)
    if not has_terms(job):
        return {"ready": False, "message": ""}
    result = evaluate(state, job)
    _store_score(job_id, job.get("resume"), result)
    return result


async def check(job_id: str, settings: dict | None = None, refresh: bool = False) -> dict:
    """List the posting's keywords with the AI when needed, then check the resume against them."""
    state = db.get_state()
    job = _find(state, job_id)
    if refresh or not has_terms(job):
        chosen, effort = await ai.strongest(settings or state["settings"], LEVEL)
        terms = await ai.ats_terms(chosen, job, effort=effort, timeout=TIMEOUT)
        basis = terms_basis(job)

        def save(latest):
            target = _find(latest, job_id)
            if terms_basis(target) == basis:  # The posting wasn't edited meanwhile.
                target["ats"] = {"terms": [term.model_dump() for term in terms], "terms_basis": basis,
                                 "model": chosen.get("model", ""), "effort": effort or "", "listed_at": time.time()}
        db.mutate_state(save)
    return await run_in_threadpool(report, job_id)


def add_terms(job_id: str, terms: list[str] | None = None) -> dict:
    """Add the keywords the master CV already has to this job's resume skills, in the CV's wording."""
    state = db.get_state()
    job = _find(state, job_id)
    if not job.get("resume"):
        raise HTTPException(400, "Tailor a resume for this job first; keywords are added to that resume.")
    if not has_terms(job):
        raise HTTPException(400, "Check this job's resume first.")
    wanted = {term.casefold() for term in terms} if terms is not None else None
    adding = [term["wording"] for term in evaluate(state, job)["terms"]
              if term["status"] == "addable" and (wanted is None or term["term"].casefold() in wanted)]
    before = job["resume"]

    def save(latest):
        target = _find(latest, job_id)
        if target.get("resume") != before:
            raise HTTPException(409, "This job's resume changed meanwhile. Check it again.")
        listed = {skills.normalized_name(name) for name in target["resume"]["skills"]}
        for wording in adding:
            if skills.normalized_name(wording) not in listed:
                target["resume"]["skills"].append(wording)
                target["resume"].setdefault("added_keywords", []).append(wording)
                listed.add(skills.normalized_name(wording))
    db.mutate_state(save)
    return report(job_id)


@router.get("/{job_id}/ats")
def get_report(job_id: str):
    return report(job_id)


@router.post("/{job_id}/ats")
async def run_check(job_id: str, payload: CheckInput | None = None):
    return await check(job_id, refresh=bool(payload and payload.refresh))


@router.post("/{job_id}/ats/add")
def add_keywords(job_id: str, payload: AddInput | None = None):
    result = add_terms(job_id, payload.terms if payload else None)
    added = sum(1 for term in result["terms"] if term["status"] == "present")
    return {**result, "message": f"Added the keywords from your CV. {added} of {len(result['terms'])} keywords are now in this resume."}
