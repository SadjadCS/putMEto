"""A bag of similar terms for each technical term in the master CV, and each job's choice from it.

Once per master resume, the AI gives each technical term the CV uses a bag with
two parts: names, the other names for exactly the same thing (LLM and Large
Language Model, PostgreSQL and Postgres), and phrasings, the other ways postings
phrase the same skill (software development and software engineering, agentic
AI and AI agents). A job's resume then uses the posting's choice from the bag.
A name is switched in the text: an abbreviation and its full name are written
together once ("Large Language Models (LLMs)"), so a search for either finds
it, and other spellings take the posting's form. A phrasing is added to that
resume's skills instead, since swapping it inside a sentence could break it
("agentic AI systems"). Only terms from the bag of a term the CV already uses
are ever written, so the resume claims nothing new. The stored resume stays as
tailored: the job's choice is applied whenever it is shown, downloaded,
checked, or sent.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time

from fastapi import APIRouter

from . import ai, ats, db, resumes


LEVEL = "medium"
TIMEOUT = 10 * 60
RETRY_AFTER = 10 * 60
SEPARATORS = re.compile(r"[\s\-–—/]+")

router = APIRouter(prefix="/api/term-bags")
log = logging.getLogger("putmeto.terms")
_task: asyncio.Task | None = None
_failed_at = 0.0
_status: dict = {"state": "idle", "message": ""}


def master_key(state: dict) -> str:
    """Changes whenever the confirmed master CV's wording does."""
    entries = [[item.get(key, "") for key in ("kind", "title", "organization", "original", "enhanced")]
               for item in state["items"] if item.get("confirmed")]
    value = [entries, [skill["name"] for skill in state["skills"] if skill.get("confirmed")],
             state["profile"].get("headline", ""), state["profile"].get("summary", "")]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:16]


def bags(state: dict) -> list[dict]:
    """Each bag: {"names": [the CV's own term, other names...], "phrasings": [...]}."""
    return [bag if isinstance(bag, dict) else {"names": list(bag), "phrasings": []}
            for bag in (state.get("term_bags") or {}).get("bags") or []]


def forms(bag: dict) -> list[str]:
    return [*bag["names"], *bag["phrasings"]]


def stale(state: dict) -> bool:
    has_master = any(item.get("confirmed") for item in state["items"]) or any(skill.get("confirmed") for skill in state["skills"])
    return has_master and (state.get("term_bags") or {}).get("basis") != master_key(state)


def master_text(state: dict) -> str:
    return "\n".join(source for _, source in ats.master_sources(state))


def _same(first: str, second: str) -> bool:
    return SEPARATORS.sub("", first).casefold() == SEPARATORS.sub("", second).casefold()


def clean(found: list[ai.TermBag], text: str) -> list[dict]:
    """Bags for terms the CV really uses, each term listed once."""
    result = []
    for bag in found:
        term = " ".join(bag.term.split())
        if not term or not ats.find({"term": term, "variants": []}, text):
            continue  # Only terms written in the CV get a bag.
        names, phrasings = [term], []
        for target, values in ((names, bag.names), (phrasings, bag.phrasings)):
            for value in values:
                value = " ".join(value.split())
                if value and not any(_same(value, known) for known in [*names, *phrasings]):
                    target.append(value)
        if len(names) > 1 or phrasings:
            result.append({"names": names, "phrasings": phrasings})
    return result


def status() -> dict:
    state = db.get_state()
    return {**_status, "running": bool(_task and not _task.done()), "stale": stale(state), "count": len(bags(state)),
            "bags": bags(state)}


def kick(force: bool = False) -> None:
    """List the bags again when the master CV changed, unless that is already under way."""
    global _task
    if _task and not _task.done():
        return
    if not force and time.time() - _failed_at < RETRY_AFTER:
        return
    if force or stale(db.get_state()):
        _task = asyncio.create_task(_work())


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _work() -> None:
    global _failed_at
    try:
        state = db.get_state()
        key = master_key(state)
        settings, effort = await ai.strongest(state["settings"], LEVEL)
        _status.update(state="working", message="Listing the other names for each technical term in your master CV.")
        found = await asyncio.wait_for(ai.term_bags(settings, state, effort=effort, timeout=TIMEOUT), TIMEOUT + 60)
        listed = clean(found, master_text(state))

        def save(latest):
            if master_key(latest) == key:  # The CV changed meanwhile: the next run lists them again.
                latest["term_bags"] = {"basis": key, "bags": listed, "model": settings.get("model", ""), "built_at": time.time()}
        db.mutate_state(save)
        _status.update(state="done", message=f"Listed similar terms for {len(listed)} technical terms in your master CV.")
        from . import goldmove
        goldmove.kick()  # Resumes now read differently, so they are checked again.
        if stale(db.get_state()):
            kick()
    except asyncio.CancelledError:
        _status.update(state="idle", message="")
        raise
    except Exception as exc:
        _failed_at = time.time()
        log.warning("Listing equivalent term names failed.", exc_info=True)
        detail = getattr(exc, "detail", None) or ("it took too long" if isinstance(exc, asyncio.TimeoutError) else str(exc))
        _status.update(state="failed", message=f"The other names for your terms could not be listed: {detail}.")


def is_acronym(name: str) -> bool:
    """LLM, RAG, or CI/CD, as opposed to a full name or a different spelling."""
    compact = re.sub(r"[\s\-/&.]", "", name)
    return " " not in name.strip() and 2 <= len(compact) <= 6 and compact.upper() == compact and any(c.isalpha() for c in compact)


def _plural(found: str, name: str) -> bool:
    return found.lower().endswith("s") and not name.lower().endswith("s") and len(SEPARATORS.sub("", found)) > len(SEPARATORS.sub("", name))


def _singular(found: str, name: str) -> str:
    if not _plural(found, name):
        return found
    return found[:-2] if found.lower().endswith("es") and not name.lower().endswith("e") else found[:-1]


def _resume_parts(resume: dict) -> list[tuple[str, object, str]]:
    """Every rewritable text in the order a resume shows it: summary, entries by section, then skills."""
    parts = [("summary", None, resume["profile"].get("summary") or "")]
    for kind, _ in resumes.SECTIONS:
        parts += [("item", item, resumes.resume_text(item)) for item in resume.get("items", []) if item.get("kind") == kind]
    parts += [("skill", index, name) for index, name in enumerate(resume.get("skills", []))]
    return parts


def choices(all_bags: list[dict], job: dict, resume: dict) -> list[dict]:
    """For each bag, the posting's choice, when the resume writes the term another way."""
    posting = f"{job.get('title', '')}\n{job.get('description', '')}"
    shown = "\n".join(text for _, _, text in _resume_parts(resume))
    picked = []
    for bag in all_bags:
        hits = [(name, matches) for name in forms(bag) if (matches := list(ats.pattern(name).finditer(posting)))]
        if not hits:
            continue
        name, matches = max(hits, key=lambda hit: (len(hit[1]), -hit[1][0].start()))  # The posting's most used term.
        if ats.pattern(name).search(shown):
            continue  # The resume already uses the posting's term.
        others = [form for form in forms(bag) if form != name and ats.pattern(form).search(shown)]
        if not others:
            continue
        use = _singular(matches[0].group(0), name)
        renamed = [form for form in others if form in bag["names"]]
        if name in bag["names"] and renamed:
            picked.append({"use": use, "acronym": is_acronym(name), "replace": renamed})
        else:  # Another phrasing: added to the skills as the posting writes it, since a sentence may not take it.
            written = " ".join(matches[0].group(0).split())
            picked.append({"add": written[:1].upper() + written[1:], "like": others})
    return picked


def apply(resume: dict, picked: list[dict]) -> dict:
    """A copy of the resume that uses the chosen names and lists the chosen phrasings in its skills."""
    result = copy.deepcopy(resume)
    if not picked:
        return result
    paired, changes = set(), []
    renames = [pick for pick in picked if "replace" in pick]

    def rewrite(text: str) -> str:
        for index, pick in enumerate(renames):
            for other in pick["replace"]:
                def swap(match, index=index, pick=pick, other=other):
                    found = match.group(0)
                    use = pick["use"] + ("s" if _plural(found, other) else "")
                    if pick["acronym"] != is_acronym(other):
                        if index in paired:
                            return found  # Written together once; the resume's own name elsewhere.
                        paired.add(index)
                        written = f"{found} ({use})" if pick["acronym"] else f"{use} ({found})"
                    else:
                        written = use
                    changes.append({"from": found, "to": written})
                    return written
                text = ats.pattern(other).sub(swap, text)
        return text

    items = {id(item): item for item in result.get("items", [])}
    skills = list(result.get("skills", []))
    groups = result.setdefault("skill_groups", {})
    for where, target, text in _resume_parts(result):
        new = rewrite(text) if renames else text
        if new == text:
            continue
        if where == "summary":
            result["profile"]["summary"] = new
        elif where == "item":
            items[id(target)]["shown"] = new
        else:
            if text in groups:
                groups[new] = groups[text]
            skills[target] = new
    listed = {_key(name) for name in skills}
    for pick in picked:
        if "add" in pick and _key(pick["add"]) not in listed:
            skills.append(pick["add"])
            listed.add(_key(pick["add"]))
            # Shown with a skill from the same bag when there is one.
            label = next((groups[name] for name in skills if name in groups and any(ats.pattern(form).search(name) for form in pick["like"])), None)
            if label:
                groups[pick["add"]] = label
            changes.append({"from": pick["like"][0], "to": f"{pick['add']} (added to skills)"})
    result["skills"] = skills
    result["wording"] = list({(change["from"], change["to"]): change for change in changes}.values())
    return result


def _key(name: str) -> str:
    return SEPARATORS.sub(" ", name).strip().casefold()


def job_resume(state: dict, job: dict) -> dict:
    """The job's tailored resume, using the posting's names for your terms."""
    resume = job.get("resume") or resumes.build_resume(state, job)  # Your general resume until it is tailored.
    return apply(resume, choices(bags(state), job, resume))


@router.get("")
def bags_status():
    return status()


@router.post("/rebuild")
async def rebuild():
    await stop()
    kick(force=True)
    return status()
