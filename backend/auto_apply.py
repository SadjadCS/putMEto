"""Apply automatically, one job at a time, to jobs that match the master resume well.

When auto-apply is on, each job whose master-CV match is 70% or more and that
has a tailored resume is handed to the browser agent (apply_agent), with a
pause between applications. The agent may use only the profile, the tailored
resume, and the answers saved on the Applications page. When it stops for a
question those don't answer, the application waits until the answers change;
when a site needs a sign-in, it waits until the user has opened the application
browser to sign in. Other failures are tried again later, a few times. An
application interrupted part-way is never retried by itself, because it may
already have been sent. Before applying, the ATS check adds the job's keywords
that the master CV already has to that job's resume, and a job whose keyword
coverage stays below the minimum you set is skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import random
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import ai, apply_agent, ats, db, job_matching, resumes


MIN_SCORE = 70                 # Master-CV match needed before applying.
GAP = (60.0, 120.0)            # Seconds between applications.
IDLE_CHECK = 5 * 60            # How often an idle runner looks for new jobs.
RETRY_AFTER = 60 * 60          # A failed application waits this long before another try.
MAX_ATTEMPTS = 3
APPLICATION_TIMEOUT = 40 * 60
ATS_RETRY = 30 * 60            # A job whose ATS check failed waits this long before another try.
SIGN_IN_URL = "https://www.linkedin.com/login"

# The standard answers, with the wording the agent sees.
ANSWERS = {
    "work_authorization": "Work authorization (where the applicant may legally work)",
    "sponsorship": "Needs visa sponsorship now or in the future",
    "salary": "Salary expectation",
    "start_date": "Earliest start date or notice period",
    "relocation": "Willing to relocate",
    "work_arrangement": "Preferred work arrangement (remote, hybrid, on-site)",
    "years_experience": "Total years of professional experience",
    "linkedin_url": "LinkedIn profile URL",
    "gender": "Gender (voluntary self-identification)",
    "race": "Race or ethnicity (voluntary self-identification)",
    "veteran": "Veteran status (voluntary self-identification)",
    "disability": "Disability status (voluntary self-identification)",
}

router = APIRouter(prefix="/api/auto-apply")
log = logging.getLogger("putmeto.apply")
_task: asyncio.Task | None = None
_wake: asyncio.Event | None = None
_applying = False
_stopping = False
_browser: subprocess.Popen | None = None
_status: dict = {"state": "idle", "message": "", "job_id": "", "step": 0, "applied": 0, "next_at": None}


class OtherAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_authorization: str = Field("", max_length=300)
    sponsorship: str = Field("", max_length=300)
    salary: str = Field("", max_length=300)
    start_date: str = Field("", max_length=300)
    relocation: str = Field("", max_length=300)
    work_arrangement: str = Field("", max_length=300)
    years_experience: str = Field("", max_length=300)
    linkedin_url: str = Field("", max_length=300)
    gender: str = Field("", max_length=300)
    race: str = Field("", max_length=300)
    veteran: str = Field("", max_length=300)
    disability: str = Field("", max_length=300)
    other: list[OtherAnswer] = Field(default_factory=list, max_length=200)


class AnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=2000)


class RulesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_ats: int = Field(0, ge=0, le=100)


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str = ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def settings_of(state: dict) -> dict:
    return state.get("auto_apply") or {}


def enabled(state: dict) -> bool:
    return bool(settings_of(state).get("enabled"))


def min_ats(state: dict) -> int:
    """The lowest ATS keyword coverage to apply with; 0 applies regardless."""
    return int(settings_of(state).get("min_ats") or 0)


def passes_ats(job: dict, state: dict) -> bool:
    minimum = min_ats(state)
    if not minimum:
        return True
    score = ats.current_score(job, state)
    if score is not None:
        return score >= minimum
    return time.time() - (job.get("ats") or {}).get("failed_at", 0) >= ATS_RETRY  # Not checked yet: it is checked first.


def answers_of(state: dict) -> dict:
    return Answers.model_validate(state.get("application_answers") or {}).model_dump()


def answers_key(state: dict) -> str:
    """Changes whenever the facts the agent may use do."""
    value = json.dumps([state["profile"], answers_of(state)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def applicant(state: dict, job: dict) -> dict:
    """Everything the agent may put into an application: profile, answers, and the tailored resume."""
    profile = {key: value for key, value in state["profile"].items() if value}
    answers = answers_of(state)
    from . import term_bags
    resume = term_bags.job_resume(state, job) if job.get("resume") else {}
    return {
        "profile": profile,
        "answers": {label: answers[key] for key, label in ANSWERS.items() if answers[key].strip()},
        "other_answers": [{"question": item["question"], "answer": item["answer"]} for item in answers["other"]],
        "resume": {
            "entries": [{key: item.get(key, "") for key in ("kind", "title", "organization", "start", "end")} | {"text": resumes.resume_text(item)}
                        for item in resume.get("items", [])],
            "skills": list(resume.get("skills", [])),
        },
    }


def score(job: dict, state: dict) -> int | None:
    """The job's current master-CV match, or None when it has none or the job or resume changed since."""
    match = job.get("ai_match") or {}
    if match.get("basis") != job_matching.basis(job_matching.resume_key(state), job):
        return None
    return match.get("score")


def eligible(job: dict, state: dict) -> bool:
    return (job.get("status") not in {"archived", "applied"} and bool(job.get("url")) and bool(job.get("resume"))
            and (score(job, state) or 0) >= MIN_SCORE)


def _latest(state: dict) -> dict[str, dict]:
    latest = {}
    for application in state["applications"]:
        latest[application["job_id"]] = application
    return latest


def ready(application: dict | None, key: str, signed_in_at: float, at: float) -> bool:
    """Whether a job may be applied to (again), given its latest application."""
    if application is None:
        return True
    status, attempted = application.get("status"), application.get("attempted_at", 0)
    if not application.get("automated") or status in {"applied", "applying"}:
        return False  # Applications you started yourself stay yours.
    if application.get("retry"):
        return True
    if status == "needs_input":
        return application.get("answers_key") != key
    if status == "needs_sign_in":
        return signed_in_at > attempted
    if status == "failed":
        return (not application.get("manual_retry") and application.get("attempts", 0) < MAX_ATTEMPTS
                and at - attempted >= RETRY_AFTER)
    return False


def pending(state: dict) -> list[str]:
    """Jobs to apply to now, best match first."""
    key, latest, at = answers_key(state), _latest(state), time.time()
    signed_in_at = settings_of(state).get("signed_in_at", 0)
    jobs = [job for job in state["jobs"] if eligible(job, state) and passes_ats(job, state)
            and ready(latest.get(job["id"]), key, signed_in_at, at)]
    jobs.sort(key=lambda job: -(score(job, state) or 0))
    return [job["id"] for job in jobs]


def browser_open() -> bool:
    return _browser is not None and _browser.poll() is None


def blocker(state: dict) -> str:
    """Why applications can't run right now, or an empty string."""
    if state["settings"].get("provider") != "codex":
        return "Auto-apply uses Codex through your ChatGPT sign-in. Choose it in Settings."
    if importlib.util.find_spec("browser_use") is None:
        return "Install the application agent first: pip install -r requirements-apply.txt"
    if not (state["profile"].get("name") and state["profile"].get("email")):
        return "Add your name and email to your profile first."
    return ""


def status() -> dict:
    state = db.get_state()
    latest = _latest(state)
    job = next((item for item in state["jobs"] if item["id"] == _status["job_id"]), None) if _status["job_id"] else None
    return {
        **_status,
        "enabled": enabled(state),
        "running": bool(_task and not _task.done()),
        "stopping": _stopping,
        "waiting": len(pending(state)),
        "eligible": sum(1 for item in state["jobs"] if eligible(item, state)),
        "needs_you": sum(1 for item in latest.values() if item.get("automated") and item.get("status") in {"needs_input", "needs_sign_in", "failed"}),
        "browser_open": browser_open(),
        "min_ats": min_ats(state),
        "below_ats": sum(1 for item in state["jobs"] if eligible(item, state) and not passes_ats(item, state)),
        "job": {"id": job["id"], "title": job.get("title", ""), "company": job.get("company", "")} if job else None,
        "blocker": blocker(state),
    }


def kick() -> None:
    """Start applying if auto-apply is on; a running applier looks again right away."""
    global _task
    if _task is None or _task.done():
        if enabled(db.get_state()):
            _task = asyncio.create_task(_work())
    elif _wake is not None:
        _wake.set()


async def stop() -> None:
    global _task, _stopping
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _stopping = False
    await asyncio.to_thread(close_browser)


def _find(items: list[dict], item_id: str) -> dict | None:
    return next((item for item in items if item["id"] == item_id), None)


async def _nap(seconds: float) -> None:
    """Wait, waking early when there may be something new to do."""
    _wake.clear()
    try:
        await asyncio.wait_for(_wake.wait(), seconds)
    except asyncio.TimeoutError:
        pass


def _settle_interrupted() -> None:
    """Applications left mid-way by a restart may have been sent, so they wait for you."""
    def settle(state):
        for application in state["applications"]:
            if application.get("status") == "applying":
                application.update(status="failed", manual_retry=True, updated_at=now(),
                                   note="PutMeTo stopped while applying. Check the site; if it didn't receive your application, select Try again.")
    db.mutate_state(settle)


def _begin(job_id: str, key: str) -> str:
    def begin(state):
        latest = _latest(state).get(job_id)
        application = latest if latest is not None and latest.get("automated") else None
        if application is None:
            application = {"id": str(uuid4()), "job_id": job_id, "created_at": now(), "automated": True, "attempts": 0}
            state["applications"].append(application)
        application.update(status="applying", attempts=application.get("attempts", 0) + 1, attempted_at=time.time(),
                           answers_key=key, retry=False, manual_retry=False, question="", updated_at=now(),
                           note="Auto-apply is filling in this application.")
        return application["id"]
    return db.mutate_state(begin)


def _finish(application_id: str, outcome) -> None:
    def finish(state):
        application = _find(state["applications"], application_id)
        if application is None:
            return
        job = _find(state["jobs"], application["job_id"])
        application.update(status=outcome.status, url=outcome.url, updated_at=now())
        if outcome.status == "submitted":
            application.update(status="applied", note=f"Submitted by auto-apply. {outcome.detail}".strip())
            if job is not None:
                job["status"] = "applied"
        elif outcome.status == "needs_input":
            application.update(question=outcome.detail, note=f"Waiting for your answer: {outcome.detail}")
        elif outcome.status == "needs_sign_in":
            application.update(note=f"Needs you to sign in ({outcome.detail}). Open the application browser, sign in, then select Done signing in; auto-apply tries again.")
        else:
            interrupted = outcome.status in {"stopped", "interrupted"}
            application.update(status="failed", manual_retry=interrupted or application.get("manual_retry", False),
                               note=outcome.detail + (" If the site didn't receive your application, select Try again." if interrupted else ""))
    db.mutate_state(finish)


async def _prepare(job_id: str, settings: dict) -> str:
    """Run the ATS check and add the keywords the master CV has; returns why to skip the job, or an empty string."""
    title = next((job.get("title") or "this job" for job in db.get_state()["jobs"] if job["id"] == job_id), "this job")
    _status.update(state="checking", job_id=job_id, step=0, next_at=None, message=f"Checking {title} the way an ATS reads it.")
    try:
        result = await ats.check(job_id, settings=settings)
        if any(term["status"] == "addable" for term in result["terms"]):
            result = await asyncio.to_thread(ats.add_terms, job_id)  # Keywords from your own CV, before submitting.
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("The ATS check for job %s failed.", job_id, exc_info=True)

        def remember(state):
            job = _find(state["jobs"], job_id)
            if job is not None:
                job.setdefault("ats", {})["failed_at"] = time.time()
        db.mutate_state(remember)
        return "" if not min_ats(db.get_state()) else f"The ATS check for {title} didn't work; it is tried again later."
    minimum = min_ats(db.get_state())
    if minimum and result["score"] < minimum:
        return f"Skipped {title}: its ATS keyword coverage is {result['score']}%, below your minimum of {minimum}%."
    return ""


async def _should_stop() -> bool:
    return _stopping


def _on_step(browser_state, output, step: int) -> None:
    _status["step"] = step


async def _apply_one(job_id: str, state: dict, model: str | None) -> str:
    """Apply to one job and return how it ended."""
    global _applying
    job = _find(state["jobs"], job_id)
    application_id = _begin(job_id, answers_key(state))
    label = f"{job.get('title') or 'the job'}" + (f" at {job['company']}" if job.get("company") else "")
    _status.update(state="applying", job_id=job_id, step=0, next_at=None, message=f"Applying to {label}.")
    folder = Path(tempfile.mkdtemp(prefix="putmeto-apply-"))
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", state["profile"].get("name", "")).strip("-") or "Resume"
    resume = folder / f"{name}-Resume.pdf"
    _applying = True
    try:
        from . import term_bags
        resume.write_bytes(resumes.render_pdf(term_bags.job_resume(state, job)))  # The posting's names for your terms.
        resume.chmod(0o600)
        outcome = await asyncio.wait_for(apply_agent.apply(job, applicant(state, job), resume, model,
                                                           should_stop=_should_stop, on_step=_on_step), APPLICATION_TIMEOUT)
    except asyncio.CancelledError:
        _finish(application_id, apply_agent.Outcome("interrupted", "PutMeTo stopped while applying."))
        raise
    except asyncio.TimeoutError:
        outcome = apply_agent.Outcome("interrupted", "The application took too long and was stopped.")
    except Exception as exc:
        log.warning("Applying to job %s failed.", job_id, exc_info=True)
        outcome = apply_agent.Outcome("failed", f"The application agent stopped: {getattr(exc, 'detail', None) or exc}"[:500])
    finally:
        _applying = False
        shutil.rmtree(folder, ignore_errors=True)
    _finish(application_id, outcome)
    log.info("Auto-apply for job %s ended: %s.", job_id, outcome.status)
    return outcome.status


async def _work() -> None:
    global _wake, _stopping
    _wake = asyncio.Event()
    _settle_interrupted()
    try:
        while True:
            state = db.get_state()
            if not enabled(state) or _stopping:
                _status.update(state="idle", job_id="", step=0, next_at=None, message="Auto-apply is off.")
                return
            problem = blocker(state)
            if problem:
                _status.update(state="paused", job_id="", next_at=None, message=problem)
                await _nap(IDLE_CHECK)
                continue
            if browser_open():
                _status.update(state="waiting", job_id="", next_at=None,
                               message="Waiting for you to close the application browser.")
                await _nap(10)
                continue
            queue = pending(state)
            if not queue:
                _status.update(state="idle", job_id="", step=0, next_at=None,
                               message=f"Waiting for jobs with a master-CV match of {MIN_SCORE}% or more and a tailored resume.")
                await _nap(IDLE_CHECK)
                continue
            try:
                settings, _ = await ai.strongest(state["settings"], apply_agent.STEP_EFFORT)
            except ai.AIError as exc:
                _status.update(state="paused", job_id="", next_at=None, message=f"Codex isn't available ({exc}). Trying again in a few minutes.")
                await _nap(IDLE_CHECK)
                continue
            skip = await _prepare(queue[0], settings)
            if skip:
                _status.update(state="idle", job_id="", step=0, next_at=None, message=skip)
                continue
            state = db.get_state()  # The resume may now have more of the job's keywords.
            if browser_open() or not enabled(state) or queue[0] not in pending(state):
                continue  # You opened the application browser, stopped, or the job changed meanwhile.
            ended = await _apply_one(queue[0], state, settings.get("model") or None)
            if ended == "submitted":
                _status["applied"] += 1
            if _stopping or not enabled(db.get_state()):
                continue  # Turned off: finish now instead of resting first.
            pause = random.uniform(*GAP)
            _status.update(state="resting", job_id="", step=0, next_at=time.time() + pause,
                           message=f"Last application: {ended.replace('_', ' ')}. The next one starts in about a minute or two.")
            await asyncio.sleep(pause)
    except asyncio.CancelledError:
        _status.update(state="idle", job_id="", step=0, next_at=None, message="Stopped.")
        raise
    except Exception as exc:
        log.warning("Auto-apply stopped early.", exc_info=True)
        _status.update(state="paused", job_id="", next_at=None,
                       message=f"Auto-apply paused: {getattr(exc, 'detail', None) or exc}. Select Start to continue.")
    finally:
        _stopping = False


def close_browser() -> None:
    global _browser
    process, _browser = _browser, None
    if process is not None and process.poll() is None:
        process.terminate()  # Chrome saves its cookies on a normal quit.
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def _set_enabled(value: bool) -> None:
    db.mutate_state(lambda state: state.setdefault("auto_apply", {}).update(enabled=value))


@router.get("")
def auto_apply_status():
    return status()


@router.post("/start")
async def start():
    global _stopping
    _stopping = False
    _set_enabled(True)
    kick()
    job_matching.kick()  # Keyword-perfect jobs need a master-CV match before auto-apply can use them.
    return status()


@router.post("/stop")
async def stop_applying():
    global _stopping
    _set_enabled(False)
    if _applying:
        _stopping = True  # The agent stops after its current step; the browser closes.
        _status["message"] = "Stopping after the current step."
    elif _task and not _task.done():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    return status()


@router.put("/rules")
async def save_rules(payload: RulesInput):
    db.mutate_state(lambda state: state.setdefault("auto_apply", {}).update(min_ats=payload.min_ats))
    kick()
    return {**status(), "message": "Auto-apply applies to every job at 70% or more." if not payload.min_ats else
            f"Auto-apply skips jobs whose ATS keyword coverage is below {payload.min_ats}%."}


@router.put("/answers")
async def save_answers(payload: Answers):
    def save(state):
        state["application_answers"] = payload.model_dump()
        return state["application_answers"]
    saved = db.mutate_state(save)
    kick()
    return {**status(), "answers": saved}


@router.post("/applications/{application_id}/answer")
async def answer_question(application_id: str, payload: AnswerInput):
    """Save the answer to the question an application stopped on; it is used for every application."""
    def save(state):
        application = _find(state["applications"], application_id)
        if application is None or application.get("status") != "needs_input" or not application.get("question"):
            raise HTTPException(404, "That question is no longer waiting for an answer.")
        answers = answers_of(state)
        question = application["question"].strip()[:500]
        answers["other"] = [item for item in answers["other"] if item["question"].strip().lower() != question.lower()]
        answers["other"].append({"question": question, "answer": payload.answer.strip()})
        state["application_answers"] = Answers.model_validate(answers).model_dump()
    db.mutate_state(save)
    kick()
    return {**status(), "message": "Answer saved. Auto-apply will use it for this and later applications."}


@router.post("/applications/{application_id}/retry")
async def retry(application_id: str):
    def mark(state):
        application = _find(state["applications"], application_id)
        if application is None or not application.get("automated") or application.get("status") in {"applied", "applying"}:
            raise HTTPException(404, "That application can't be tried again.")
        application.update(retry=True, updated_at=now(), note="Waiting to be tried again.")
    db.mutate_state(mark)
    kick()
    return {**status(), "message": "Auto-apply will try this application again." if enabled(db.get_state()) else
            "Queued. Start auto-apply to try it again."}


@router.post("/browser")
async def open_browser(payload: BrowserInput):
    """Open the application browser so you can sign in; auto-apply waits until you close it."""
    global _browser
    from .apply_agent import CHROME
    if _applying:
        raise HTTPException(409, "Auto-apply is using the application browser. Stop auto-apply or wait for this application to finish.")
    if browser_open():
        return {**status(), "message": "The application browser is already open."}
    if not CHROME.exists():
        raise HTTPException(400, "Install Google Chrome to use the application browser.")
    url = SIGN_IN_URL
    state = db.get_state()
    application = _find(state["applications"], payload.application_id) if payload.application_id else None
    if application is not None:
        job = _find(state["jobs"], application["job_id"]) or {}
        url = application.get("url") or job.get("url") or url
    if not url.startswith(("https://", "http://")):
        url = SIGN_IN_URL
    profile = db.DATA_DIR / "apply-browser-profile"
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    _browser = subprocess.Popen([str(CHROME), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    db.mutate_state(lambda current: current.setdefault("auto_apply", {}).update(signed_in_at=time.time()))
    return {**status(), "message": "Application browser opened. Sign in where needed, then select Done signing in."}


@router.post("/browser/close")
async def done_signing_in():
    await asyncio.to_thread(close_browser)
    kick()
    return {**status(), "message": "Application browser closed. Auto-apply continues with your sign-ins."}
