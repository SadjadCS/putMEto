"""Build the applicant's master resume, and every job title it supports, with the deepest reasoning available.

Uploading a CV fills in entries right away with a quick reading. This reads the
same CV text again with the strongest model at its deepest reasoning level (for
example gpt-6-astra at ultra), organizes it the way the CV does, with projects
nested under their jobs and the CV's own section titles, and replaces the quick
entries. Wording is always copied from the CV. Entries the user added are kept,
and nothing is replaced if CV entries were edited while the build ran. Then the
same model writes each bullet in Google's XYZ format for resumes (the master
keeps the CV's wording) and lists every job title the master resume supports.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from . import ai, db, roles, skills, xyz


TIMEOUT = 45 * 60  # Deep reasoning over a whole CV can take a long time.
ROLES_TIMEOUT = 20 * 60
XYZ_TIMEOUT = 30 * 60
router = APIRouter(prefix="/api/master-resume")
_task: asyncio.Task | None = None
_status: dict = {"state": "idle", "phase": "", "message": "", "model": "", "effort": "", "started_at": ""}


class EntriesChanged(Exception):
    """CV entries were edited while the master resume was being built."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cv_entries(state: dict) -> list[dict]:
    return [item for item in state["items"] if item.get("source") != "manual"]


def status() -> dict:
    state = db.get_state()
    return {**_status, "running": bool(_task and not _task.done()), "xyz_pending": len(xyz.pending(state)),
            "has_cv": bool((state.get("cv_transcript") or {}).get("lines")), "built": state.get("master_resume") or None}


def _launch(work, phase: str, message: str) -> None:
    """Run one background job, replacing any job still in progress."""
    global _task
    if _task and not _task.done():
        _task.cancel()
    _status.update(state="working", phase=phase, message=message, model="", effort="", started_at=_now())
    _task = asyncio.create_task(work)


def start(lines: list[str]) -> None:
    """Build the master resume from these CV lines, then list the job titles it supports."""
    _launch(_build(lines), "resume", "Starting your master resume.")


def start_xyz() -> None:
    """Write Google XYZ wording for entries that have none or whose wording changed."""
    _launch(_xyz_only(), "xyz", "Writing your bullets in Google's XYZ format. This can take several minutes; you can keep working.")


def start_roles() -> None:
    """List every job title the current master resume supports."""
    _launch(_roles_only(), "roles", "Listing every job title your master resume supports. This can take several minutes; you can keep working.")


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _strongest() -> tuple[dict, str | None, str]:
    settings, effort = await ai.strongest(db.get_state()["settings"])
    model = settings.get("model", "")
    _status.update(model=model, effort=effort or "")
    return settings, effort, (model or "your AI model") + (f" at {effort} reasoning" if effort else "")


def _apply(state: dict, result: ai.ImportedResume, before: list[dict], model: str, effort: str | None) -> int:
    from .main import invalidate_resumes
    if _cv_entries(state) != before:
        raise EntriesChanged
    state["items"] = [
        *({"id": uuid4().hex, **item.model_dump(), "enhanced": "", "confirmed": True, "source": "cv"} for item in result.items),
        *(item for item in state["items"] if item.get("source") == "manual"),
    ]
    for key, value in result.profile.model_dump().items():
        if value and not state["profile"].get(key):
            state["profile"][key] = value
    skills.merge_candidates(state, result.skills, origin="resume")
    state["section_titles"] = dict(result.section_titles)
    state["master_resume"] = {"built_at": _now(), "model": model, "effort": effort or ""}
    invalidate_resumes(state)
    skills.refresh_job_matches(state)
    return len(result.items)


async def _write_xyz(settings: dict, effort: str | None, label: str) -> str:
    from .main import invalidate_resumes
    items = xyz.pending(db.get_state())
    if not items:
        return ""
    _status.update(phase="xyz", message=f"Writing your bullets in Google's XYZ format with {label}. This can take several minutes.")
    written = await asyncio.wait_for(xyz.write(settings, items, effort=effort, timeout=XYZ_TIMEOUT), XYZ_TIMEOUT + 60)

    def save(state):
        count, unmeasured = xyz.apply(state, written)
        if count:
            invalidate_resumes(state)  # Tailored resumes are rebuilt with the new wording.
        return count, unmeasured
    count, unmeasured = db.mutate_state(save)
    missing = f" {unmeasured} {'bullet has' if unmeasured == 1 else 'bullets have'} no measurable result in your CV yet; add a real number on My profile if you have one." if unmeasured else ""
    return f"Resumes now use Google XYZ wording for {count} {'entry' if count == 1 else 'entries'}; your master CV keeps your own words.{missing}"


async def _suggest_roles(settings: dict, effort: str | None, label: str) -> str:
    _status.update(phase="roles", message=f"Listing every job title your master resume supports, with {label}. This can take several minutes.")
    found = await asyncio.wait_for(ai.suggest_roles(settings, db.get_state(), effort=effort, timeout=ROLES_TIMEOUT), ROLES_TIMEOUT + 60)
    added = db.mutate_state(lambda state: roles.merge_roles(state, found))
    return f"Found {len(found)} job titles you qualify for ({added} new); check the ones you want in Job preferences."


async def _build(lines: list[str]) -> None:
    try:
        settings, effort, label = await _strongest()
        _status.update(message=f"Building your master resume with {label}. This can take several minutes; you can keep working.")
        before = _cv_entries(db.get_state())
        result = await asyncio.wait_for(ai.build_master_resume(settings, lines, effort=effort, timeout=TIMEOUT), TIMEOUT + 60)
        if not result.items:
            raise ai.AIError("The model found no entries, so your current entries were kept.")
        count = db.mutate_state(lambda state: _apply(state, result, before, settings.get("model", ""), effort))
        from . import skill_grouping, term_bags
        skill_grouping.kick()  # Skills from the master resume join their context groups.
        term_bags.kick()  # And each technical term gets its other names.
        done = f"Your master resume is ready: {count} entries organized from your CV, in your own words."
        try:
            done += " " + await _write_xyz(settings, effort, label)
        except (ai.AIError, asyncio.TimeoutError):
            done += " The XYZ wording could not be written this time; select Write XYZ on My profile to try again."
        try:
            done += " " + await _suggest_roles(settings, effort, label)
        except (ai.AIError, asyncio.TimeoutError):
            done += " Job titles could not be listed this time; select Suggest roles in Job preferences to try again."
        _status.update(state="done", phase="", message=done)
        from . import job_matching
        job_matching.kick()  # Your jobs are matched to the new master resume.
    except asyncio.CancelledError:
        _status.update(state="idle", phase="", message="Stopped.")
        raise
    except EntriesChanged:
        _status.update(state="failed", phase="", message="Your CV entries changed while the master resume was being built, so nothing was replaced. Select Rebuild to use your latest edits as the starting point.")
    except asyncio.TimeoutError:
        _status.update(state="failed", phase="", message="Building the master resume took too long. Your entries were kept; select Rebuild to try again.")
    except ai.AIError as exc:
        _status.update(state="failed", phase="", message=f"Your master resume could not be built: {exc}")
    except Exception:
        _status.update(state="failed", phase="", message="Your master resume could not be built. Your entries were kept; select Rebuild to try again.")


async def _xyz_only() -> None:
    try:
        settings, effort, label = await _strongest()
        message = await _write_xyz(settings, effort, label) or "Every entry already has current XYZ wording."
        _status.update(state="done", phase="", message=message)
        from . import job_matching
        job_matching.kick()  # Tailored resumes are rebuilt with the new wording.
    except asyncio.CancelledError:
        _status.update(state="idle", phase="", message="Stopped.")
        raise
    except asyncio.TimeoutError:
        _status.update(state="failed", phase="", message="Writing the XYZ wording took too long. Select Write XYZ to try again.")
    except ai.AIError as exc:
        _status.update(state="failed", phase="", message=f"The XYZ wording could not be written: {exc}")
    except Exception:
        _status.update(state="failed", phase="", message="The XYZ wording could not be written. Select Write XYZ to try again.")


async def _roles_only() -> None:
    try:
        settings, effort, label = await _strongest()
        _status.update(state="done", phase="", message=await _suggest_roles(settings, effort, label))
    except asyncio.CancelledError:
        _status.update(state="idle", phase="", message="Stopped.")
        raise
    except asyncio.TimeoutError:
        _status.update(state="failed", phase="", message="Listing job titles took too long. Select Suggest roles to try again.")
    except ai.AIError as exc:
        _status.update(state="failed", phase="", message=f"Job titles could not be listed: {exc}")
    except Exception:
        _status.update(state="failed", phase="", message="Job titles could not be listed. Select Suggest roles to try again.")


@router.get("")
def master_status():
    return status()


@router.post("/rebuild")
async def rebuild():
    lines = (db.get_state().get("cv_transcript") or {}).get("lines")
    if not lines:
        raise HTTPException(422, "Upload your CV first; the master resume is built from it.")
    start(lines)
    return status()


@router.post("/xyz")
async def write_xyz():
    if not any(item.get("confirmed") for item in db.get_state()["items"]):
        raise HTTPException(400, "Import your CV or confirm at least one entry first.")
    start_xyz()
    return status()


@router.post("/roles")
async def suggest_roles():
    if not any(item.get("confirmed") for item in db.get_state()["items"]):
        raise HTTPException(400, "Import your CV or confirm at least one entry first; job titles are suggested from your master resume.")
    start_roles()
    return status()
