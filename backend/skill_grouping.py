"""Group the technical skills on My profile by context, such as Programming Languages or Large Language Models.

The AI places each skill in one group; new skills join an existing group when
one fits. Groups are kept by skill name in state["skill_grouping"], apart from
the skills themselves, so grouping never changes the skills that resumes and
job matching use. Regrouping starts over with all skills.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter

from . import ai, db, skills


LEVEL = "medium"       # Grouping is light work, even on the strongest model.
TIMEOUT = 10 * 60
RETRY_AFTER = 10 * 60  # After a failure, new skills wait this long before another try.
OTHER = "Other"

router = APIRouter(prefix="/api/skill-groups")
log = logging.getLogger("putmeto.skills")
_task: asyncio.Task | None = None
_failed_at = 0.0
_status: dict = {"state": "idle", "message": "", "model": "", "effort": ""}


def grouping(state: dict) -> dict:
    value = state.get("skill_grouping") or {}
    return {"order": list(value.get("order") or []), "by_name": dict(value.get("by_name") or {})}


def group_of(state: dict) -> dict[str, str]:
    """Each skill's group, by skill id."""
    by_name = grouping(state)["by_name"]
    return {skill["id"]: by_name[key] for skill in state["skills"] if (key := skills.normalized_name(skill["name"])) in by_name}


def order(state: dict) -> list[str]:
    """The groups that still have skills, in display order."""
    used = set(group_of(state).values())
    return [name for name in grouping(state)["order"] if name in used]


def pending(state: dict) -> list[dict]:
    by_name = grouping(state)["by_name"]
    return [skill for skill in state["skills"] if skills.normalized_name(skill["name"]) not in by_name]


def status() -> dict:
    return {**_status, "running": bool(_task and not _task.done()), "waiting": len(pending(db.get_state()))}


def kick(force: bool = False) -> None:
    """Group skills that have no group yet, unless that is already under way."""
    global _task
    if _task and not _task.done():
        return
    if not force and time.time() - _failed_at < RETRY_AFTER:
        return
    if pending(db.get_state()):
        _task = asyncio.create_task(_work())


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def save(state: dict, asked: list[dict], result: ai.SkillGroups, replace: bool = False) -> None:
    """Store the groups of the skills that were asked about; a regroup replaces all groups."""
    current = {"order": [], "by_name": {}} if replace else grouping(state)
    order, by_name = current["order"], current["by_name"]
    left = dict(enumerate(asked, 1))
    for group in result.groups:
        name = " ".join(group.name.split())[:60] or OTHER
        name = next((known for known in order if known.casefold() == name.casefold()), name)  # Reuse an existing group.
        if name not in order:
            order.append(name)
        for number in group.skills:
            if (skill := left.pop(number, None)) is not None:
                by_name[skills.normalized_name(skill["name"])] = name
    for skill in left.values():  # Anything the model left out still gets a place.
        by_name[skills.normalized_name(skill["name"])] = OTHER
    names = {skills.normalized_name(skill["name"]) for skill in state["skills"]}
    by_name = {key: name for key, name in by_name.items() if key in names}  # Skills removed meanwhile.
    used = set(by_name.values())
    order = [name for name in order if name in used and name != OTHER]
    order += sorted(used - set(order) - {OTHER}) + ([OTHER] if OTHER in used else [])
    state["skill_grouping"] = {"order": order, "by_name": by_name}


async def _work(regroup: bool = False) -> None:
    global _failed_at
    try:
        for _ in range(3):  # Skills added while grouping are picked up too.
            state = db.get_state()
            asked = list(state["skills"]) if regroup else pending(state)
            if not asked:
                break
            settings, effort = await ai.strongest(state["settings"], LEVEL)
            label = (settings.get("model") or "your AI model") + (f" at {effort} reasoning" if effort else "")
            _status.update(state="working", model=settings.get("model", ""), effort=effort or "",
                           message=f"Grouping {len(asked)} {'skill' if len(asked) == 1 else 'skills'} by context with {label}.")
            existing = [] if regroup else order(state)
            result = await asyncio.wait_for(ai.group_skills(settings, asked, existing, effort=effort, timeout=TIMEOUT), TIMEOUT + 60)
            db.mutate_state(lambda latest: save(latest, asked, result, replace=regroup))
            regroup = False
        _status.update(state="done", message="")
    except asyncio.CancelledError:
        _status.update(state="idle", message="")
        raise
    except Exception as exc:
        _failed_at = time.time()
        log.warning("Grouping skills failed.", exc_info=True)
        detail = getattr(exc, "detail", None) or ("it took too long" if isinstance(exc, asyncio.TimeoutError) else str(exc))
        _status.update(state="failed", message=f"Your skills could not be grouped: {detail}. Select Group by context to try again.")


@router.get("")
def grouping_status():
    return status()


@router.post("/regroup")
async def regroup():
    """Group every skill again from scratch; the current groups stay until the new ones are ready."""
    global _task
    await stop()
    if db.get_state()["skills"]:
        _task = asyncio.create_task(_work(regroup=True))
    return status()
