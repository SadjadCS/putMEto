"""Goldmove: the keywords your jobs still miss, for you to confirm.

Every job gets its ATS keywords listed and its resume checked in the
background (see ats), so each shows its ATS match before and after. For jobs
that started above a 30% match, keywords still missing after your CV's own
keywords and similar terms are used are gathered for you to confirm. Keywords
still missing are gathered across jobs and counted. You tick the ones you
really have: they join your master CV's skills and every tailored resume not
yet sent. Nothing is added unless you confirm it, and keywords you dismiss
aren't offered again. Job titles, degrees, and certifications are never
offered, since a skills line can't claim them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from . import ats, db, skills, term_bags


MIN_INITIAL = 30                     # Missing keywords are gathered from jobs whose first match was above this.
CONCURRENCY = 3                      # Postings whose keywords are listed at once.
SKIP_KINDS = {"title", "degree", "certification"}
RETRY_AFTER = 30 * 60
ORIGIN = "goldmove"

router = APIRouter(prefix="/api/goldmove")
log = logging.getLogger("putmeto.goldmove")
_task: asyncio.Task | None = None
_status: dict = {"state": "idle", "message": "", "done": 0, "total": 0}

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class TermsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    terms: list[Term] = Field(min_length=1, max_length=100)


def initial_score(job: dict) -> int:
    """The match a job started with: its master-CV match, or else its keyword match."""
    return (job.get("ai_match") or {}).get("score", job.get("match_score") or 0)


def checkable(job: dict) -> bool:
    """Jobs that get an ATS match: everything not archived or applied for, with a posting to read."""
    return job.get("status") not in {"archived", "applied"} and bool(str(job.get("description") or "").strip())


def eligible(job: dict) -> bool:
    """Jobs whose missing keywords are offered."""
    return checkable(job) and initial_score(job) > MIN_INITIAL


def pending(state: dict) -> list[str]:
    """Jobs whose resume hasn't been checked in its current form."""
    now, master = time.time(), term_bags.master_key(state)
    return [job["id"] for job in state["jobs"] if checkable(job) and ats.current_score(job, state, master) is None
            and now - (job.get("ats") or {}).get("failed_at", 0) >= RETRY_AFTER]


def dismissed(state: dict) -> set[str]:
    return set((state.get("goldmove") or {}).get("dismissed") or [])


def candidates(state: dict) -> list[dict]:
    """Keywords still missing from the checked jobs, most asked for first."""
    have = {skills.normalized_name(skill["name"]) for skill in state["skills"] if skill.get("confirmed")}
    gone, found, master = dismissed(state), {}, term_bags.master_key(state)
    for job in state["jobs"]:
        if not eligible(job) or ats.current_score(job, state, master) is None:
            continue
        for term in (job.get("ats") or {}).get("missing", []):
            key = skills.normalized_name(term["term"])
            if term.get("kind") in SKIP_KINDS or key in have or key in gone:
                continue
            entry = found.setdefault(key, {"term": term["term"], "kind": term.get("kind", ""), "jobs": [], "required": 0})
            if all(item["id"] != job["id"] for item in entry["jobs"]):
                entry["jobs"].append({"id": job["id"], "title": job.get("title", ""), "company": job.get("company", "")})
                entry["required"] += term.get("importance") == "required"
    return sorted(found.values(), key=lambda entry: (-len(entry["jobs"]), -entry["required"], entry["term"].casefold()))


def status() -> dict:
    state = db.get_state()
    master = term_bags.master_key(state)
    jobs = [job for job in state["jobs"] if eligible(job)]
    return {**_status, "running": bool(_task and not _task.done()), "waiting": len(pending(state)), "eligible": len(jobs),
            "checked": sum(1 for job in jobs if ats.current_score(job, state, master) is not None),
            "scored": sum(1 for job in state["jobs"] if checkable(job) and ats.current_score(job, state, master) is not None),
            "dismissed": len(dismissed(state)), "candidates": candidates(state)[:200]}


def kick() -> None:
    """Check jobs whose keywords or resume changed, unless that is already under way."""
    global _task
    if (_task is None or _task.done()) and pending(db.get_state()):
        _task = asyncio.create_task(_work())


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _failed(job_id: str) -> None:
    def remember(state):
        job = next((item for item in state["jobs"] if item["id"] == job_id), None)
        if job is not None:
            job.setdefault("ats", {})["failed_at"] = time.time()
    db.mutate_state(remember)


async def _work() -> None:
    done, gate = 0, asyncio.Semaphore(CONCURRENCY)

    async def one(job_id: str, total: int) -> None:
        nonlocal done
        async with gate:
            try:
                job = next((item for item in db.get_state()["jobs"] if item["id"] == job_id), None)
                if job is not None and checkable(job):
                    if ats.has_terms(job):
                        await run_in_threadpool(ats.report, job_id)
                    else:
                        await ats.check(job_id)  # Lists the posting's keywords with the AI first.
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Goldmove couldn't check job %s; it is tried again later.", job_id, exc_info=True)
                _failed(job_id)
            done += 1
            _status.update(done=done, message=f"Checked {done} of {total} jobs for their ATS match and missing keywords.")

    try:
        for _ in range(3):  # Jobs added or changed meanwhile are picked up too.
            queue = pending(db.get_state())
            if not queue:
                break
            total = done + len(queue)
            _status.update(state="working", done=done, total=total, message=f"Checking {total} jobs for their ATS match and missing keywords.")
            await asyncio.gather(*(one(job_id, total) for job_id in queue))
        _status.update(state="done", done=0, total=0, message=f"Checked {done} {'job' if done == 1 else 'jobs'}." if done else "")
    except asyncio.CancelledError:
        _status.update(state="idle", done=0, total=0, message="")
        raise


def confirm(names: list[str]) -> list[str]:
    """Add skills you confirmed to your master CV and every tailored resume not yet sent."""
    def save(state):
        listed = {skills.normalized_name(skill["name"]): skill for skill in state["skills"]}
        added = []
        for name in dict.fromkeys(" ".join(name.split()) for name in names):
            key = skills.normalized_name(name)
            current = listed.get(key)
            if current is not None:
                if not current.get("confirmed"):
                    current["confirmed"] = True
                    added.append(current["name"])
                continue
            if len(state["skills"]) >= skills.MAX_SKILLS:
                raise HTTPException(422, f"Your skill list is full ({skills.MAX_SKILLS} skills). Remove skills you don't need on My profile first.")
            listed[key] = {"id": uuid4().hex, "name": name, "confirmed": True, "origin": ORIGIN, "support": "supported",
                           "kind": "other", "evidence": "You confirmed this skill in Goldmove.", "rationale": "", "aliases": []}
            state["skills"].append(listed[key])
            added.append(name)
        for job in state["jobs"]:
            resume = job.get("resume")
            if resume and job.get("status") != "applied":
                shown = {skills.normalized_name(name) for name in resume["skills"]}
                resume["skills"] += [name for name in added if skills.normalized_name(name) not in shown]
        keys = {skills.normalized_name(name) for name in names}
        state.setdefault("goldmove", {})["dismissed"] = [key for key in dismissed(state) if key not in keys]
        skills.refresh_job_matches(state)
        return added
    return db.mutate_state(save)


@router.get("")
def goldmove_status():
    return status()


@router.post("/scan")
async def scan():
    kick()
    return status()


@router.post("/confirm")
async def confirm_terms(payload: TermsInput):
    from . import skill_grouping, term_bags
    added = confirm(payload.terms)
    kick()  # The changed resumes are checked again.
    skill_grouping.kick()
    term_bags.kick()
    count = len(added)
    return {**status(), "message": f"Added {count} {'skill' if count == 1 else 'skills'} you have to your profile and to every tailored resume not yet sent."
            if count else "Those skills are already in your profile."}


@router.post("/dismiss")
async def dismiss(payload: TermsInput):
    keys = [skills.normalized_name(name) for name in payload.terms]
    db.mutate_state(lambda state: state.setdefault("goldmove", {}).update(
        dismissed=list(dict.fromkeys([*dismissed(state), *keys]))[-1000:]))
    return {**status(), "message": "Those keywords won't be offered again."}


@router.post("/dismissed/clear")
async def clear_dismissed():
    db.mutate_state(lambda state: state.setdefault("goldmove", {}).update(dismissed=[]))
    return {**status(), "message": "Dismissed keywords will be offered again."}
