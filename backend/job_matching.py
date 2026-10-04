"""Match jobs to the master resume and tailor a resume for each, in the background.

Jobs scoring above 30% and below 100% on the keyword match (and not archived or
applied for) get a real match from the strongest model at high reasoning: a
score against the master resume, with strengths and gaps, and a tailored resume
in the applicant's own wording. A job is redone only when it or the master
resume changes; one that failed waits before another try. While auto-apply is
on, jobs at 100% are matched too, since it applies only on a master-CV match.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from fastapi import APIRouter

from . import ai, db


LEVEL = "high"            # Reasoning level for each job.
RANGE = (30, 100)         # Keyword scores strictly between these are matched.
RETRY_AFTER = 30 * 60     # A job that failed waits this long before another try.
TIMEOUT = 15 * 60

router = APIRouter(prefix="/api/job-matching")
log = logging.getLogger("putmeto.matching")
_task: asyncio.Task | None = None
_status: dict = {"state": "idle", "message": "", "done": 0, "total": 0, "model": "", "effort": ""}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:16]


def resume_key(state: dict) -> str:
    """Changes whenever the confirmed master resume does."""
    entries = [[item.get(key, "") for key in ("kind", "title", "organization", "start", "end", "original", "enhanced")]
               for item in state["items"] if item.get("confirmed")]
    # Skills you confirm in Goldmove only add search keywords, so they don't redo every match.
    names = [skill["name"] for skill in state["skills"] if skill.get("confirmed") and skill.get("origin") != "goldmove"]
    return _hash([entries, names])


def basis(key: str, job: dict) -> str:
    """What a job's match was made from: the master resume and the job's text."""
    return _hash([key, job.get("title", ""), job.get("description", "")])


def eligible(job: dict, state: dict) -> bool:
    score = job.get("match_score") or 0
    in_range = RANGE[0] < score < RANGE[1] or (score >= RANGE[1] and bool((state.get("auto_apply") or {}).get("enabled")))
    return job.get("status") not in {"archived", "applied"} and in_range and bool(str(job.get("description") or "").strip())


def pending(state: dict) -> list[str]:
    """Jobs in range whose match or tailored resume is missing or out of date."""
    key, now, ids = resume_key(state), time.time(), []
    for job in state["jobs"]:
        if not eligible(job, state):
            continue
        current = basis(key, job)
        failed = job.get("match_error") or {}
        if failed.get("basis") == current and now - failed.get("at", 0) < RETRY_AFTER:
            continue
        if (job.get("ai_match") or {}).get("basis") != current or not job.get("resume"):
            ids.append(job["id"])
    return ids


def status() -> dict:
    state = db.get_state()
    has_master = any(item.get("confirmed") for item in state["items"])
    return {**_status, "running": bool(_task and not _task.done()), "waiting": len(pending(state)) if has_master else 0}


def kick() -> None:
    """Start matching if it isn't running; a running matcher picks up new jobs by itself."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_work())


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _find(state: dict, job_id: str) -> dict | None:
    return next((job for job in state["jobs"] if job["id"] == job_id), None)


async def _one(job_id: str, settings: dict, effort: str | None) -> None:
    from .main import tailor
    state = db.get_state()
    job = _find(state, job_id)
    if job is None or not eligible(job, state):
        return
    current = basis(resume_key(state), job)
    try:
        if (job.get("ai_match") or {}).get("basis") != current:
            result = await ai.match_job(settings, state, job, effort=effort, timeout=TIMEOUT)

            def save(latest):
                target = _find(latest, job_id)
                # Skip it if the job or the master resume changed meanwhile; it will be matched again.
                if target is not None and basis(resume_key(latest), target) == current:
                    target["ai_match"] = {**result.model_dump(), "basis": current, "model": settings.get("model", ""),
                                          "effort": effort or "", "checked_at": time.time()}
                    target.pop("match_error", None)
            db.mutate_state(save)
        if not job.get("resume"):
            await tailor(job_id, settings, effort, mark_prepared=False)
        from . import auto_apply, goldmove
        auto_apply.kick()  # A well-matched job can be applied to right away.
        goldmove.kick()  # And its resume is checked for keywords it still misses.
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Matching job %s to the master resume failed; it is tried again later.", job_id, exc_info=True)
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__

        def remember(latest):
            target = _find(latest, job_id)
            if target is not None:
                target["match_error"] = {"message": str(detail)[:300], "basis": current, "at": time.time()}
        db.mutate_state(remember)


async def _work() -> None:
    done = 0
    try:
        while True:
            state = db.get_state()
            if not any(item.get("confirmed") for item in state["items"]):
                _status.update(state="idle", message="Matching starts once your master resume is ready.", done=0, total=0)
                return
            queue = pending(state)
            if not queue:
                _status.update(state="idle", done=0, total=0,
                               message=f"Matched {done} {'job' if done == 1 else 'jobs'} to your master resume." if done else "")
                return
            settings, effort = await ai.strongest(state["settings"], LEVEL)
            label = (settings.get("model") or "your AI model") + (f" at {effort} reasoning" if effort else "")
            _status.update(state="working", model=settings.get("model", ""), effort=effort or "")
            total = done + len(queue)
            for job_id in queue:
                _status.update(done=done, total=total,
                               message=f"Matching job {done + 1} of {total} to your master resume with {label}, then tailoring its resume.")
                await _one(job_id, settings, effort)
                done += 1
    except asyncio.CancelledError:
        _status.update(state="idle", message="Stopped.", done=0, total=0)
        raise
    except Exception as exc:
        log.warning("Job matching stopped early.", exc_info=True)
        _status.update(state="idle", message=f"Matching paused: {getattr(exc, 'detail', None) or exc}. Select Match now to continue.",
                       done=0, total=0)


@router.get("")
def matching_status():
    return status()


@router.post("/start")
async def start():
    kick()
    return status()
