"""Optional Camoufox discovery and visible application sessions."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from contextlib import suppress
from urllib.parse import urljoin, urlsplit

from backend.network import SourceError, validate_public_url, validate_url_shape


INSTALL_HELP = "Install browser support with python -m pip install -r requirements-browser.txt, then run python -m camoufox fetch and restart PutMeTo."
_sessions: dict[str, dict] = {}
_cleanup_tasks: set[asyncio.Task] = set()


def browser_status() -> dict:
    available = importlib.util.find_spec("camoufox") is not None
    return {"installed": available, "active_sessions": len(_sessions), "message": "Camoufox Python package is installed. Browser availability is checked when launched." if available else INSTALL_HELP}


async def _launch(headless: bool):
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        raise SourceError(INSTALL_HELP) from exc
    manager = AsyncCamoufox(headless=headless)
    try:
        browser = await asyncio.wait_for(manager.__aenter__(), timeout=60)
        context = await browser.new_context(service_workers="block", accept_downloads=False)

        async def guard(route):
            try:
                await validate_public_url(route.request.url)
            except SourceError:
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await context.route("**/*", guard)
        return manager, browser, context
    except Exception as exc:
        with suppress(Exception):
            await manager.__aexit__(None, None, None)
        raise SourceError(f"Camoufox could not start. {INSTALL_HELP}") from exc


async def _release(session_id: str):
    session = _sessions.pop(session_id, None)
    if session:
        with suppress(Exception):
            await session["manager"].__aexit__(None, None, None)


def _release_soon(session_id: str):
    task = asyncio.create_task(_release(session_id))
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def close_browsers():
    for session_id in list(_sessions):
        await _release(session_id)
    if _cleanup_tasks:
        await asyncio.gather(*list(_cleanup_tasks), return_exceptions=True)


async def start_application(session_id: str, url: str, profile: dict, pdf: bytes) -> dict:
    """Prepare common fields; user must handle site-specific questions and submit."""
    await validate_public_url(url)
    if len(_sessions) >= 3:
        raise SourceError("Close an existing application browser before opening another (maximum three).")
    manager, browser, context = await _launch(headless=False)
    _sessions[session_id] = {"manager": manager, "browser": browser}
    browser.on("disconnected", lambda _: _release_soon(session_id))
    context.on("page", lambda page: page.on("close", lambda _: _release_soon(session_id) if not context.pages else None))
    filled = 0
    attached = False
    try:
        page = await context.new_page()
        page.set_default_timeout(1200)
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await validate_public_url(page.url)
        name = str(profile.get("name", "")).strip()
        first, _, last = name.partition(" ")
        fields = [
            ('input[autocomplete="given-name"], input[name="first_name"], input[id="first_name"]', first),
            ('input[autocomplete="family-name"], input[name="last_name"], input[id="last_name"]', last),
            ('input[autocomplete="name"], input[name="name"], input[name="full_name"], input[name="fullName"]', name),
            ('input[type="email"], input[autocomplete="email"], input[name="email"]', profile.get("email", "")),
            ('input[type="tel"], input[autocomplete="tel"], input[name="phone"]', profile.get("phone", "")),
            ('input[name="urls[Portfolio]"], input[name="website"], input[name="portfolio"]', profile.get("website", "")),
        ]
        for selector, value in fields:
            if not value:
                continue
            for frame in page.frames:
                with suppress(Exception):
                    locator = frame.locator(selector).first
                    if await locator.count() and await locator.is_visible() and not await locator.input_value():
                        await locator.fill(str(value))
                        filled += 1
        for frame in page.frames:
            with suppress(Exception):
                inputs = frame.locator('input[type="file"]')
                for index in range(min(await inputs.count(), 5)):
                    field = inputs.nth(index)
                    identity = " ".join([await field.get_attribute("name") or "", await field.get_attribute("id") or "", await field.get_attribute("aria-label") or ""])
                    if re.search(r"resume|\bcv\b", identity, re.I):
                        await field.set_input_files({"name": "resume.pdf", "mimeType": "application/pdf", "buffer": pdf})
                        attached = True
                        break
            if attached:
                break
        return {"filled_fields": filled, "resume_attached": attached}
    except Exception as exc:
        await _release(session_id)
        if isinstance(exc, SourceError):
            raise
        raise SourceError("The application page could not be opened. Check its URL and try again.") from exc


def extract_jsonld(raw: str) -> list[dict]:
    """Handle normal, array, and @graph schema.org payloads without evaluating JS."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    found: list[dict] = []
    stack = [value]
    while stack and len(found) < 100:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current[:1000])
        elif isinstance(current, dict):
            types = current.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(str(kind).rsplit("/", 1)[-1] == "JobPosting" for kind in types):
                found.append(current)
            for key in ("@graph", "itemListElement", "item"):
                if key in current:
                    stack.append(current[key])
    return found


async def discover_jsonld(url: str) -> list[dict]:
    """Visit the board and at most eight same-site job detail links."""
    url = await validate_public_url(url)
    manager, browser, context = await _launch(headless=True)
    results: list[dict] = []
    try:
        async with asyncio.timeout(150):
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
            await validate_public_url(page.url)
            origin = urlsplit(page.url).netloc

            async def read_page():
                scripts = page.locator('script[type="application/ld+json"]')
                for raw in (await scripts.all_text_contents())[:30]:
                    if len(raw) <= 1_000_000:
                        for posting in extract_jsonld(raw):
                            posting = dict(posting)
                            posting["_page_url"] = page.url
                            results.append(posting)

            await read_page()
            # A detail page should not trigger an additional crawl.
            if not results:
                candidates = []
                links = page.locator('a[href]')
                for index in range(min(await links.count(), 300)):
                    href = await links.nth(index).get_attribute("href")
                    if not href:
                        continue
                    target = urljoin(page.url, href)
                    try:
                        target = validate_url_shape(target)
                    except SourceError:
                        continue
                    if urlsplit(target).netloc == origin and target != page.url and re.search(r"/(?:jobs?|careers?|positions?|vacancies|opportunities)/.+", urlsplit(target).path, re.I) and target not in candidates:
                        candidates.append(target)
                for target in candidates[:8]:
                    try:
                        await validate_public_url(target)
                        await page.goto(target, wait_until="domcontentloaded", timeout=15_000)
                        await validate_public_url(page.url)
                        await read_page()
                    except Exception:
                        continue
        return results
    except asyncio.TimeoutError:
        if results:
            return results
        raise SourceError("The careers site timed out. Try a direct job posting URL.")
    except Exception as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("This careers site could not be read. Try a direct job posting URL or add the job manually.") from exc
    finally:
        with suppress(Exception):
            await manager.__aexit__(None, None, None)
