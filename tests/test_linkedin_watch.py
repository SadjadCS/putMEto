"""The background LinkedIn search is slow, bounded, and stops at every LinkedIn gate."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend import db, linkedin, linkedin_watch


def card(number, title="Senior AI Engineer", description=""):
    return {"title": title, "company": "Example", "location": "London, United Kingdom", "remote": False,
            "url": f"https://www.linkedin.com/jobs/view/{number}/", "description": description,
            "salary": "", "job_type": "", "posted_at": ""}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.initialize()
    db.mutate_state(lambda state: state.update(
        positions=[{"id": "ai", "name": "AI Engineer", "confirmed": True}],
        jobs=[{"id": "known", "title": "AI Engineer", "company": "Known", "url": "https://www.linkedin.com/jobs/view/3/",
               "status": "saved", "source": "LinkedIn", "description": "Already here.", "match_score": 70, "matched_skills": []}],
    ))
    monkeypatch.setattr(linkedin, "wait_gap", AsyncMock())
    monkeypatch.setattr(linkedin_watch, "BREAK_AFTER", (1000, 1000))
    monkeypatch.setattr(linkedin_watch, "_status", dict(linkedin_watch._status, found=0, skipped=0))


async def run_until(predicate):
    """Run the background search until the predicate holds; return the status seen at that moment."""
    task = asyncio.create_task(linkedin_watch._run())
    for _ in range(500):
        await asyncio.sleep(0)
        if task.done() or predicate():
            break
    seen = dict(linkedin_watch._status)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return seen


def test_a_pass_pages_through_results_and_opens_only_new_matching_jobs(workspace, monkeypatch):
    first_page = [card(1), card(2, "Cashier"), card(3), card(4, "LLM AI Engineer")]
    pages = []

    async def results(keywords, location, remote_only, start, open_new=False):
        pages.append((keywords, location, start, open_new))
        # LinkedIn repeats its last page when the results run out.
        return {"state": "ready", "jobs": first_page, "message": ""}

    async def job_page(job, remote_only=False, open_new=False):
        return {"state": "ready", "job": {**job, "description": f"About {job['title']}."}, "message": ""}

    monkeypatch.setattr(linkedin, "read_results_page", results)
    opened = AsyncMock(side_effect=job_page)
    monkeypatch.setattr(linkedin, "read_job_page", opened)
    finished = asyncio.run(run_until(lambda: linkedin_watch._status["message"].startswith("Checked every result page")))

    # Every load may reopen the LinkedIn window if it was closed.
    assert pages == [("AI Engineer", "United States", 0, True), ("AI Engineer", "United States", 25, True),
                     ("AI Engineer", "Europe", 0, True), ("AI Engineer", "Europe", 25, True)]
    assert [call.args[0]["url"] for call in opened.await_args_list] == [card(1)["url"], card(4)["url"]]
    saved = {job["title"]: job for job in db.get_state()["jobs"]}
    assert set(saved) == {"AI Engineer", "Senior AI Engineer", "LLM AI Engineer"}
    assert saved["Senior AI Engineer"]["description"] == "About Senior AI Engineer."
    assert linkedin_watch._status["found"] == 2
    assert linkedin_watch._status["skipped"] == 2, "The cashier listing is counted once per region and never opened."
    assert finished["state"] == "waiting" and finished["next_at"], "The next pass is scheduled hours later."
    assert linkedin_watch._status["state"] == "stopped"


@pytest.fixture
def waits(monkeypatch):
    """Long waits are recorded and skipped; zero-length yields still let other tasks run."""
    recorded, real_sleep = [], asyncio.sleep

    async def sleep(seconds, *args):
        if seconds:
            recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return recorded


def one_result_page(monkeypatch, *cards):
    pages = iter([{"state": "ready", "jobs": list(cards), "message": ""}])
    monkeypatch.setattr(linkedin, "read_results_page", AsyncMock(side_effect=lambda *args, **kwargs: next(pages, {"state": "empty", "jobs": [], "message": ""})))


def test_failed_page_loads_are_retried_with_growing_waits_instead_of_stopping(workspace, monkeypatch, waits, caplog):
    failures = iter([True, True, False])

    async def results(*args, **kwargs):
        if next(failures):
            try:
                raise TimeoutError("Timeout 35000ms exceeded.")
            except TimeoutError as cause:
                raise linkedin.SourceError("LinkedIn could not be loaded. Check the open browser and your connection.") from cause
        return {"state": "ready", "jobs": [card(1)], "message": ""}

    monkeypatch.setattr(linkedin, "read_results_page", results)
    monkeypatch.setattr(linkedin, "read_job_page", AsyncMock(return_value={"state": "ready", "job": {**card(1), "description": "About it."}, "message": ""}))
    statuses = []
    update = linkedin_watch._update
    monkeypatch.setattr(linkedin_watch, "_update", lambda state, message, next_in=None: (statuses.append(message), update(state, message, next_in)))
    asyncio.run(run_until(lambda: linkedin_watch._status["found"] == 1))
    assert waits[:2] == [60, 120], "One minute, then two: waits grow after each failure."
    assert any("(Timeout 35000ms exceeded.)" in message and "Trying again automatically" in message for message in statuses)
    assert linkedin_watch._status["found"] == 1, "The search continued after the failures."
    assert "LinkedIn page did not load" in caplog.text and "Timeout 35000ms exceeded" in caplog.text, "The real cause is logged."


def test_sign_in_waits_without_loading_pages_and_continues_by_itself(workspace, monkeypatch, waits):
    one_result_page(monkeypatch, card(1))
    opened = AsyncMock(side_effect=[{"state": "login", "job": card(1), "message": linkedin._gate_message("login")},
                                    {"state": "ready", "job": {**card(1), "description": "About it."}, "message": ""}])
    checks = AsyncMock(side_effect=["login", "clear"])
    monkeypatch.setattr(linkedin, "read_job_page", opened)
    monkeypatch.setattr(linkedin, "check_gate", checks)
    asyncio.run(run_until(lambda: linkedin_watch._status["found"] == 1))
    assert checks.await_count == 2 and opened.await_count == 2, "While you sign in, it only looks at the open page."
    assert waits.count(linkedin_watch.GATE_CHECK) == 2
    assert linkedin_watch._status["found"] == 1


def test_rate_limit_waits_about_forty_minutes_then_continues(workspace, monkeypatch, waits):
    one_result_page(monkeypatch, card(1))
    monkeypatch.setattr(linkedin, "read_job_page", AsyncMock(side_effect=[
        {"state": "rate_limited", "job": card(1), "message": linkedin._gate_message("rate_limited")},
        {"state": "ready", "job": {**card(1), "description": "About it."}, "message": ""}]))
    asyncio.run(run_until(lambda: linkedin_watch._status["found"] == 1))
    low, high = linkedin_watch.RATE_LIMIT_WAIT
    assert any(low <= wait <= high for wait in waits)
    assert linkedin_watch._status["found"] == 1


def test_without_a_target_position_it_waits_instead_of_stopping(workspace, monkeypatch, waits):
    db.mutate_state(lambda state: state["positions"][0].update(confirmed=False))
    loads = AsyncMock(return_value={"state": "empty", "jobs": [], "message": ""})
    monkeypatch.setattr(linkedin, "read_results_page", loads)
    seen = asyncio.run(run_until(lambda: linkedin.read_results_page.await_count == 0 and len(waits) >= 2))
    assert seen["state"] == "waiting" and "confirmed target position" in seen["message"]
    assert loads.await_count == 0


def test_pages_are_20_to_30_seconds_apart_with_no_hourly_or_daily_cap(workspace, monkeypatch):
    linkedin.navigations.extend([time.time() - 60] * 1000)  # Far more than any old cap.
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(linkedin_watch.asyncio, "sleep", sleep)
    asyncio.run(linkedin_watch._Pacer().turn())
    assert sleeps == [], "No waiting for an hourly or daily limit."
    linkedin.wait_gap.assert_awaited_once_with((20.0, 30.0))
    assert linkedin_watch.status()["pages_last_hour"] == 1000


def test_longer_break_after_several_pages(workspace, monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(linkedin_watch.asyncio, "sleep", sleep)
    monkeypatch.setattr(linkedin_watch, "BREAK_AFTER", (2, 2))
    pacer = linkedin_watch._Pacer()
    for _ in range(3):
        asyncio.run(pacer.turn())
    assert len(sleeps) == 1 and linkedin_watch.BREAK_LENGTH[0] <= sleeps[0] <= linkedin_watch.BREAK_LENGTH[1]


def test_start_needs_target_positions_and_stop_keeps_found_jobs(workspace, monkeypatch):
    from backend.main import app

    async def forever():
        await asyncio.Event().wait()

    monkeypatch.setattr(linkedin_watch, "_run", forever)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        db.mutate_state(lambda state: state["positions"][0].update(confirmed=False))
        assert client.post("/api/linkedin/continuous/start").status_code == 422
        db.mutate_state(lambda state: state["positions"][0].update(confirmed=True))
        started = client.post("/api/linkedin/continuous/start").json()
        assert started["running"] is True and "hourly_limit" not in started
        assert client.get("/api/linkedin/continuous").json()["running"] is True
        stopped = client.post("/api/linkedin/continuous/stop").json()
        assert stopped["running"] is False
    assert len(db.get_state()["jobs"]) == 1
