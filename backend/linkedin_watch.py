"""Keep searching LinkedIn in the background, the way a person browses.

Each page load waits a random 20-30 seconds, with longer breaks every few
pages; there is no hourly or daily cap. Only new jobs that match a target
position are opened. Only the user's Stop ends the search: a page that fails
to load is tried again after a growing wait, a closed LinkedIn window is
reopened, a sign-in or security check waits for the user without loading
pages, and a rate limit waits about 40 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from . import db, linkedin
from .jobs import canonical_url, confirmed_positions, enrich_linkedin_job, make_job, positions_query, score_job
from .network import SourceError


PAGE_GAP = (20.0, 30.0)        # Seconds between LinkedIn page loads.
BREAK_AFTER = (8, 15)          # Page loads before a longer break...
BREAK_LENGTH = (5 * 60, 12 * 60)  # ...of this many seconds.
PAGES_PER_SEARCH = 4           # Result pages read per region in each pass.
PASS_INTERVAL = 4 * 60 * 60    # Seconds between passes, so new postings appear.
RETRY_WAIT = (60, 15 * 60)     # After a failure: one minute, doubling up to 15 minutes.
GATE_CHECK = 2 * 60            # While waiting for a sign-in or security check.
RATE_LIMIT_WAIT = (30 * 60, 50 * 60)
POSITIONS_CHECK = 5 * 60       # While waiting for a confirmed target position.

log = logging.getLogger("putmeto.linkedin")

router = APIRouter(prefix="/api/linkedin/continuous")
_task: asyncio.Task | None = None
_status: dict = {"state": "stopped", "message": "Not running.", "found": 0, "skipped": 0, "next_at": ""}


def _at(seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()


def _update(state: str, message: str, next_in: float | None = None) -> None:
    _status.update(state=state, message=message, next_at=_at(next_in) if next_in else "")


def status() -> dict:
    now, recent = time.time(), linkedin.recent_navigations()
    return {
        **_status, "running": bool(_task and not _task.done()),
        "pages_last_hour": sum(1 for moment in recent if now - moment < 3600),
        "pages_today": sum(1 for moment in recent if now - moment < 86400),
    }


class _Pacer:
    """Waits before each page load: a human-scale gap and occasional breaks."""

    def __init__(self):
        self.loads = 0
        self.break_after = random.randint(*BREAK_AFTER)

    async def turn(self) -> None:
        if self.loads >= self.break_after:
            pause = random.uniform(*BREAK_LENGTH)
            _update("waiting", "Taking a short break, as a person would.", pause)
            await asyncio.sleep(pause)
            self.loads, self.break_after = 0, random.randint(*BREAK_AFTER)
        _update("searching", "Searching, 20-30 seconds between pages.")
        await linkedin.wait_gap(PAGE_GAP)
        self.loads += 1


def _new_relevant(cards: list[dict]) -> list[dict]:
    """Cards worth opening: not saved yet, and matching a target position and the location preferences."""
    state = db.get_state()
    saved = {canonical_url(job["url"]) for job in state["jobs"]}
    fresh = [card for card in cards if canonical_url(card["url"]) not in saved]
    relevant = [card for card in fresh if score_job(card, state)[2]]
    _status["skipped"] += len(fresh) - len(relevant)
    return relevant


def _save(fields: dict) -> int:
    def persist(state):
        try:
            job = make_job(fields, "LinkedIn", state)
        except (SourceError, ValueError, TypeError):
            return 0
        existing = next((item for item in state["jobs"] if canonical_url(item["url"]) == canonical_url(job["url"])), None)
        if existing:
            enrich_linkedin_job(existing, job, state)
            return 0
        state["jobs"].append(job)
        return 1
    return db.mutate_state(persist)


def _reason(exc: BaseException) -> str:
    """The first line of what actually went wrong, for the status message."""
    cause = exc.__cause__ or exc
    lines = str(cause).strip().splitlines()
    return (lines[0] if lines else type(cause).__name__)[:140]


def _retry_wait(failures: int) -> float:
    return min(RETRY_WAIT[0] * 2 ** (failures - 1), RETRY_WAIT[1])


async def _load(step, *args) -> dict:
    """One LinkedIn page that never ends the search; it waits and tries again instead."""
    failures = 0
    while True:
        try:
            result = await step(*args, open_new=True)  # Reopens the LinkedIn window if it was closed.
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            wait = _retry_wait(failures)
            log.warning("LinkedIn page did not load (failure %s); trying again in %ss.", failures, int(wait), exc_info=True)
            _update("waiting", f"LinkedIn didn't load ({_reason(exc)}). Trying again automatically.", wait)
            await asyncio.sleep(wait)
            continue
        if result["state"] == "rate_limited":
            wait = random.uniform(*RATE_LIMIT_WAIT)
            _update("waiting", "LinkedIn is limiting requests, so the search is waiting about 40 minutes before continuing.", wait)
            await asyncio.sleep(wait)
            continue
        if result["state"] in linkedin.GATES:
            # Sign-in or security check: the user acts in the LinkedIn window; nothing is loaded meanwhile.
            while True:
                _update("waiting", f"{result['message']} The search continues by itself once you're through.", GATE_CHECK)
                await asyncio.sleep(GATE_CHECK)
                with contextlib.suppress(Exception):
                    if await linkedin.check_gate() not in {"login", "checkpoint"}:
                        break
            continue
        return result


async def _positions() -> list[str]:
    while not (positions := confirmed_positions(db.get_state())):
        _update("waiting", "Waiting for at least one confirmed target position in Job preferences.", POSITIONS_CHECK)
        await asyncio.sleep(POSITIONS_CHECK)
    return positions


async def _pass(pacer: _Pacer) -> None:
    """Read every results page for the target positions once, opening new matching jobs."""
    positions = await _positions()
    preferences = db.get_state().get("preferences", {})
    location = str(preferences.get("location", "")).strip()
    remote_only = bool(preferences.get("remote_only"))
    query = positions_query(positions)
    for region in [location] if location else ["United States", "Europe"]:
        seen: set[str] = set()
        for page_number in range(PAGES_PER_SEARCH):
            await pacer.turn()
            result = await _load(linkedin.read_results_page, query, region, remote_only, page_number * linkedin.RESULTS_PER_PAGE)
            cards = [card for card in result.get("jobs", []) if card["url"] not in seen]
            if result["state"] != "ready" or not cards:
                break  # No more results for this region.
            seen.update(card["url"] for card in cards)
            for card in _new_relevant(cards):
                await pacer.turn()
                opened = await _load(linkedin.read_job_page, card, remote_only)
                if _save(opened["job"]):
                    _status["found"] += 1
                    from . import job_matching
                    job_matching.kick()


async def _run() -> None:
    pacer, failures = _Pacer(), 0
    try:
        while True:
            try:
                await _pass(pacer)
                failures = 0
                _update("waiting", "Checked every result page. The next pass looks for new postings.", PASS_INTERVAL)
                await asyncio.sleep(PASS_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                wait = _retry_wait(failures)
                log.warning("Background LinkedIn search hit an error; continuing in %ss.", int(wait), exc_info=True)
                _update("waiting", f"Something went wrong ({_reason(exc)}). Continuing automatically.", wait)
                await asyncio.sleep(wait)
    except asyncio.CancelledError:
        _update("stopped", "Stopped. Jobs found so far are kept.")
        raise


@router.get("")
def continuous_status():
    return status()


@router.post("/start")
async def start():
    global _task
    if _task and not _task.done():
        return status()
    if not confirmed_positions(db.get_state()):
        raise HTTPException(422, "Confirm at least one target position so the search knows what to look for.")
    _status.update(found=0, skipped=0)
    _update("searching", "Starting. A LinkedIn window opens if it isn't already.")
    _task = asyncio.create_task(_run())
    return status()


@router.post("/stop")
async def stop():
    global _task
    task, _task = _task, None
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return status()
