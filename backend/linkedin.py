"""Visible, session-based LinkedIn job search using rendered pages only.

The browser stays open for the user to complete login or security checks. This
module never enters credentials, solves challenges, or submits applications.
"""

from __future__ import annotations

import asyncio
import html
import re
from contextlib import suppress
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from backend import db
from backend.browser import INSTALL_HELP, extract_jsonld
from backend.linkedin_layouts import SEMANTIC_DETAIL_JS, is_non_job_title, parse_study_job
from backend.network import SourceError, validate_public_url, validate_url_shape


_session: dict | None = None
_search_lock = asyncio.Lock()
_cleanup_tasks: set[asyncio.Task] = set()
CARD_SELECTOR = ".base-search-card, .base-card, .job-card-container, .job-card-list, .jobs-search-results__list-item, [data-job-id]"

# Fixed DOM readers; page text is always treated as data.
EXTRACT_CARDS_JS = r"""() => {
    const read = (node, selectors) => {
        for (const selector of selectors.split(',')) {
            const found = node.querySelector(selector.trim());
            if (found && found.innerText.trim()) return found.innerText.trim();
        }
        return '';
    };
    let cards = [...document.querySelectorAll('.base-search-card, .base-card, .job-card-container, .job-card-list, .jobs-search-results__list-item, [data-job-id]')];
    if (!cards.length) cards = [...document.querySelectorAll('a[href*="/jobs/view/"]')];
    return cards.slice(0, 250).map(node => {
        const link = node.matches('a[href]') ? node : node.querySelector('a.base-card__full-link, a.job-card-list__title--link, a.job-card-container__link, a[href*="/jobs/view/"]');
        const jobId = node.getAttribute('data-job-id') || (node.getAttribute('data-entity-urn') || '').match(/jobPosting:(\d+)$/)?.[1] || '';
        const time = node.querySelector('time');
        return {
            title: read(node, '.base-search-card__title, .job-card-list__title--link, .job-card-list__title, .artdeco-entity-lockup__title') || link?.innerText?.trim() || link?.getAttribute('aria-label') || '',
            company: read(node, '.base-search-card__subtitle, .job-card-container__primary-description, .artdeco-entity-lockup__subtitle'),
            location: read(node, '.job-search-card__location, .job-card-container__metadata-item, .artdeco-entity-lockup__caption, .job-card-container__metadata-wrapper'),
            url: link?.href || (/^\d+$/.test(jobId) ? `https://www.linkedin.com/jobs/view/${jobId}/` : ''),
            description: '',
            salary: read(node, '.job-search-card__salary-info, .job-card-container__salary-info'),
            job_type: '',
            posted_at: time?.getAttribute('datetime') || time?.innerText?.trim() || ''
        };
    }).filter(card => !/^(?:onsite|remote|hybrid|fulltime|parttime|contract|temporary|internship|volunteer|apply(?:now)?|easyapply|save|saved|share|showmore|seemore|follow|following|activelyrecruiting|beanearlyapplicant|jobdetails|viewjob)+$/.test(card.title.toLowerCase().replace(/[^a-z0-9]/g, '')));
}"""

EXTRACT_DETAIL_JS = r"""() => {
    const read = (selectors) => {
        for (const selector of selectors.split(',')) {
            const found = document.querySelector(selector.trim());
            if (found && found.innerText.trim()) return found.innerText.trim();
        }
        return '';
    };
    let employment = '';
    for (const row of document.querySelectorAll('.description__job-criteria-item')) {
        if (/employment type/i.test(row.querySelector('h3')?.innerText || '')) {
            employment = row.querySelector('.description__job-criteria-text')?.innerText?.trim() || '';
        }
    }
    const time = document.querySelector('time[datetime]');
    const description = read('.show-more-less-html__markup, .description__text, .jobs-description__content, .jobs-description-content__text, .jobs-box__html-content');
    const company = read('.topcard__org-name-link, .job-details-jobs-unified-top-card__company-name');
    return {
        title: read('.top-card-layout__title, .job-details-jobs-unified-top-card__job-title') || ((description || company) ? read('h1') : ''),
        company,
        location: read('.topcard__flavor--bullet, .job-details-jobs-unified-top-card__primary-description-container'),
        description,
        salary: read('.compensation__salary, .salary'),
        job_type: employment,
        posted_at: time?.getAttribute('datetime') || ''
    };
}"""


class _ContextBrowser:
    """Lifecycle adapter for Playwright versions with context.browser=None."""

    def __init__(self, context):
        self.context = context
        self.closed = False
        context.on("close", self._closed)

    def _closed(self, *_):
        self.closed = True

    def is_connected(self):
        return not self.closed

    def on(self, event, callback):
        if event == "disconnected":
            self.context.on("close", lambda *_: callback(self))


async def _launch(headless: bool):
    """Open the dedicated on-disk profile without inspecting its credentials."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError as exc:
        raise SourceError(INSTALL_HELP) from exc
    profile = db.DATA_DIR / "linkedin-browser-profile"
    try:
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile.chmod(0o700)
    except OSError as exc:
        raise SourceError("The LinkedIn browser profile cannot be saved. Check write permissions for PutMeTo's data folder.") from exc
    manager = AsyncCamoufox(
        headless=headless, persistent_context=True, user_data_dir=str(profile),
        service_workers="block", accept_downloads=False,
    )
    try:
        context = await asyncio.wait_for(manager.__aenter__(), timeout=60)

        async def guard(route):
            try:
                await validate_public_url(route.request.url)
            except SourceError:
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await context.route("**/*", guard)
        browser = context.browser or _ContextBrowser(context)
        return manager, browser, context
    except BaseException as exc:
        with suppress(Exception):
            await manager.__aexit__(None, None, None)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise SourceError("The LinkedIn browser could not open its saved profile. Close other PutMeTo or CLI browsers using this profile and try again. " + INSTALL_HELP) from exc


def build_search_url(keywords: str, location: str = "", remote_only: bool = False) -> str:
    if not isinstance(keywords, str):
        raise SourceError("LinkedIn search keywords must be text; leave them blank to search all roles.")
    if len(keywords) > 300 or not isinstance(location, str) or len(location) > 300:
        raise SourceError("Search keywords and location must each be 300 characters or fewer.")
    params = {}
    if keywords.strip():
        params["keywords"] = keywords.strip()
    if location.strip():
        params["location"] = location.strip()
    if remote_only:
        params["f_WT"] = "2"
    return "https://www.linkedin.com/jobs/search/" + ("?" + urlencode(params) if params else "")


def canonical_job_url(url: str) -> str:
    if not isinstance(url, str):
        raise SourceError("LinkedIn job URLs must be strings.")
    parsed = urlsplit(validate_url_shape(url))
    host = (parsed.hostname or "").rstrip(".").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise SourceError("A LinkedIn job must link to linkedin.com.")
    match = re.fullmatch(r"/jobs/view/(?:[^/]+-)?([0-9]{1,20})/?", parsed.path)
    if not match:
        raise SourceError("Use a LinkedIn /jobs/view/ URL ending in a numeric job ID.")
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit: int = 300, multiline: bool = False) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    parser = _Text()
    parser.feed(html.unescape(str(value))[:150_000])
    text = "".join(parser.parts).strip()
    return (re.sub(r"\n\s*\n", "\n\n", text) if multiline else " ".join(text.split()))[:limit]


def normalize_job(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise SourceError("LinkedIn returned an invalid job card.")
    title = _text(raw.get("title"))
    if not title or is_non_job_title(title):
        raise SourceError("LinkedIn returned a job without a title.")
    location = _text(raw.get("location"))
    return {
        "title": title, "company": _text(raw.get("company")), "location": location,
        "url": canonical_job_url(raw.get("url", "")),
        "description": _text(raw.get("description"), 60_000, multiline=True),
        "salary": _text(raw.get("salary")), "job_type": _text(raw.get("job_type"), 100),
        "posted_at": _text(raw.get("posted_at"), 100),
        "remote": raw.get("remote") is True or bool(re.search(r"\bremote\b", location, re.I)),
    }


def classify_page(snapshot: dict) -> str:
    """Distinguish access gates, genuine empty results, and changed page markup."""
    path = urlsplit(str(snapshot.get("url", ""))).path.casefold()
    status = snapshot.get("status", 200)
    title = str(snapshot.get("title", "")).casefold()
    text = str(snapshot.get("text", "")).casefold()
    if status == 429 or "too many requests" in title or "temporarily restricted" in title:
        return "rate_limited"
    if snapshot.get("has_challenge") or any(part in path for part in ("/checkpoint", "/challenge", "/captcha")) or any(part in title for part in ("security verification", "security check", "verify your identity")):
        return "checkpoint"
    if any(part in path for part in ("/authwall", "/login", "/uas/login", "/signup")) or status in {401, 403, 999}:
        return "login"
    # Guest search pages include "Sign in to view more jobs" alongside results.
    if snapshot.get("has_jobs"):
        return "ready"
    if snapshot.get("has_login_form"):
        return "login"
    if any(phrase in text for phrase in ("no matching jobs", "no jobs found", "no results found", "we couldn't find any jobs", "we couldn’t find any jobs", "no results for")) or re.search(r"(?<![\d,])\b0\s+jobs\b", text):
        return "empty"
    if any(phrase in text for phrase in ("verify you're not a robot", "verify you are not a robot", "complete this security check")):
        return "checkpoint"
    return "unknown"


async def _page_snapshot(page, status: int | None, has_jobs: bool) -> dict:
    async def visible(selector):
        try:
            matches = page.locator(selector)
            for index in range(min(await matches.count(), 4)):
                if await matches.nth(index).is_visible():
                    return True
        except Exception:
            pass
        return False

    title, text = "", ""
    with suppress(Exception):
        title = await page.title()
    with suppress(Exception):
        text = (await page.locator("body").inner_text(timeout=2500))[:60_000]
    return {
        "url": page.url, "status": status, "title": title, "text": text, "has_jobs": has_jobs,
        "has_login_form": await visible('form[action*="login-submit"], form.login__form, input#username, input[name="session_password"]'),
        "has_challenge": await visible('iframe[src*="captcha"], input[name="captcha"], #captcha-internal, #challenge-dialog'),
    }


def linkedin_status() -> dict:
    active = bool(_session and _session["browser"].is_connected())
    return {
        "active": active, "busy": _search_lock.locked(),
        "persistent": True,
        "requires_action": bool(active and _session.get("requires_action")),
        "search_url": _session.get("search_url", "") if active else "",
        "message": _session.get("message", "LinkedIn browser is open.") if active else "Search LinkedIn to reopen its saved local browser profile. After upgrading from temporary sessions, sign in once in this window.",
    }


async def _close_session(expected: dict | None = None):
    global _session
    if expected is not None and _session is not expected:
        return
    session, _session = _session, None
    if session:
        with suppress(Exception):
            await session["manager"].__aexit__(None, None, None)


def _schedule_close(session: dict):
    task = asyncio.create_task(_close_session(session))
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def close_linkedin():
    await _close_session()
    if _cleanup_tasks:
        await asyncio.gather(*list(_cleanup_tasks), return_exceptions=True)


async def _get_session() -> dict:
    global _session
    if _session and _session["browser"].is_connected():
        page = _session["page"]
        if page.is_closed():
            available = _session["context"].pages
            if available:
                _session["page"] = available[0]
            else:
                await _close_session()
        if _session:
            return _session
    await _close_session()
    manager, browser, context = await _launch(headless=False)
    try:
        available = [page for page in context.pages if not page.is_closed()]
        page = available[0] if available else await context.new_page()
        page.set_default_timeout(1500)
        session = {"manager": manager, "browser": browser, "context": context, "page": page, "requires_action": False, "search_url": "", "message": "LinkedIn browser is open."}
        _session = session
        browser.on("disconnected", lambda _: _schedule_close(session))

        def track_page(value):
            value.on("close", lambda _: _schedule_close(session) if not context.pages else None)

        for existing_page in context.pages:
            track_page(existing_page)
        context.on("page", track_page)
        return session
    except BaseException:
        with suppress(Exception):
            await manager.__aexit__(None, None, None)
        raise


def _gate_message(kind: str) -> str:
    if kind == "rate_limited":
        return "LinkedIn is rate limiting this session. The browser is left open; wait before trying the search again."
    if kind == "checkpoint":
        return "Complete LinkedIn's security check yourself in the open Camoufox window, then run the search again."
    return "LinkedIn requires sign-in or access confirmation. Complete it yourself in the open Camoufox window, then run the search again."


def _result(session: dict, jobs: list[dict], search_url: str, warnings: list[str], message: str, requires_action: bool = False) -> dict:
    session.update(requires_action=requires_action, search_url=search_url, message=message)
    session["gate_url"] = session["page"].url if requires_action else ""
    return {"jobs": jobs, "requires_action": requires_action, "message": message, "search_url": search_url, "warnings": warnings}


async def _extract_cards(page) -> list[dict]:
    raw = await page.evaluate(EXTRACT_CARDS_JS)
    result, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        try:
            job = normalize_job(item)
        except SourceError:
            continue
        if job["url"] not in seen:
            seen.add(job["url"])
            result.append(job)
    return result


def _posting_fields(posting: dict, url: str) -> dict:
    company = posting.get("hiringOrganization", {})
    locations = posting.get("jobLocation", [])
    if isinstance(locations, dict):
        locations = [locations]
    parts = []
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        address = location.get("address", {})
        if isinstance(address, dict):
            parts.append(", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)))
        elif isinstance(address, str):
            parts.append(address)
    remote = str(posting.get("jobLocationType", "")).casefold() == "telecommute"
    salary = posting.get("baseSalary") or {}
    salary_text = ""
    if isinstance(salary, dict):
        value = salary.get("value") or {}
        if isinstance(value, dict):
            salary_text = " ".join(str(value) for value in (salary.get("currency", ""), value.get("value") or value.get("minValue", ""), "–" if value.get("maxValue") else "", value.get("maxValue", ""), value.get("unitText", "")) if value)
    employment = posting.get("employmentType", "")
    return {
        "title": posting.get("title", ""), "company": company.get("name", "") if isinstance(company, dict) else company,
        "url": posting.get("url") or url, "location": ", ".join(filter(None, parts)) or ("Remote" if remote else ""),
        "description": posting.get("description", ""), "salary": salary_text,
        "job_type": ", ".join(str(value) for value in employment) if isinstance(employment, list) else employment,
        "posted_at": posting.get("datePosted", ""), "remote": remote,
    }


async def _extract_detail(page, job: dict) -> dict:
    details = await page.evaluate(EXTRACT_DETAIL_JS)
    combined = dict(job)
    if isinstance(details, dict):
        combined.update({key: value for key, value in details.items() if value and key != "url"})
    for raw in (await page.locator('script[type="application/ld+json"]').all_text_contents())[:20]:
        if len(raw) > 1_000_000:
            continue
        for posting in extract_jsonld(raw):
            with suppress(SourceError):
                normalized = normalize_job(_posting_fields(posting, job["url"]))
                if normalized["url"] == job["url"]:
                    combined.update({key: value for key, value in normalized.items() if value})
    if not combined.get("description") or not combined.get("company") or not combined.get("title") or is_non_job_title(combined.get("title", "")):
        # Signed-in pages may use randomized classes. Require independent
        # visible title/company evidence instead of guessing from an h1.
        with suppress(Exception):
            observed = await page.evaluate(SEMANTIC_DETAIL_JS)
            semantic = parse_study_job(observed)
            if semantic and semantic["url"] == job["url"]:
                combined.update({key: value for key, value in semantic.items() if value})
    return normalize_job(combined)


async def search_linkedin(keywords: str, location: str = "", remote_only: bool = False, limit: int = 10) -> dict:
    search_url = build_search_url(keywords, location, remote_only)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
        raise SourceError("Choose between 1 and 25 LinkedIn jobs per search.")
    if _search_lock.locked():
        raise SourceError("A LinkedIn search is already running. Wait for it to finish.")
    async with _search_lock:
        await validate_public_url(search_url)
        session = await _get_session()
        page = session["page"]
        jobs, warnings = [], []
        try:
            async with asyncio.timeout(210):
                if session.get("requires_action"):
                    # Preserve an unfinished login or challenge instead of replacing it.
                    existing_cards = []
                    with suppress(Exception):
                        existing_cards = await _extract_cards(page)
                    existing = classify_page(await _page_snapshot(page, None, bool(existing_cards)))
                    if existing in {"login", "checkpoint"}:
                        return _result(session, jobs, search_url, warnings, _gate_message(existing), True)
                response = await page.goto(search_url, wait_until="domcontentloaded", timeout=35_000)
                await validate_public_url(page.url)
                status = response.status if response else None
                with suppress(Exception):
                    await page.wait_for_selector(CARD_SELECTOR + ', form[action*="login-submit"], input#username, #captcha-internal', timeout=6000, state="attached")
                jobs = (await _extract_cards(page))[:limit]
                if remote_only:
                    for job in jobs:
                        job["remote"] = True
                state = classify_page(await _page_snapshot(page, status, bool(jobs)))
                if state in {"login", "checkpoint", "rate_limited"}:
                    return _result(session, jobs, search_url, warnings, _gate_message(state), True)
                if state == "empty":
                    return _result(session, [], search_url, warnings, "LinkedIn returned no jobs for this search. Try broader keywords or a different location.")
                if not jobs:
                    warnings.append("The page loaded without recognizable job cards. LinkedIn may have changed its layout or the page may not have finished loading.")
                    return _result(session, [], search_url, warnings, "No job cards could be read. Inspect the open LinkedIn window, then retry or add a job URL manually.")

                for index, job in enumerate(jobs):
                    try:
                        response = await page.goto(job["url"], wait_until="domcontentloaded", timeout=15_000)
                        await validate_public_url(page.url)
                        status = response.status if response else None
                        snapshot = await _page_snapshot(page, status, False)
                        state = classify_page(snapshot)
                        # An actual authwall/checkpoint ends browsing immediately.
                        # A public job page can include an incidental sign-in form.
                        job_page = False
                        with suppress(SourceError):
                            canonical_job_url(snapshot.get("url", ""))
                            job_page = True
                        incidental_form = state == "login" and job_page and snapshot.get("has_login_form") and snapshot.get("status") not in {401, 403, 999}
                        if state in {"login", "checkpoint", "rate_limited"} and not incidental_form:
                            warnings.append("Full descriptions could not be read for every result; available job cards are included.")
                            return _result(session, jobs, search_url, warnings, _gate_message(state), True)
                        with suppress(Exception):
                            await page.wait_for_selector('.show-more-less-html__markup, .jobs-description__content, .jobs-description-content__text, script[type="application/ld+json"]', timeout=3500, state="attached")
                        detail = await _extract_detail(page, job)
                        state = classify_page({**snapshot, "has_jobs": bool(detail.get("description"))})
                        if state in {"login", "checkpoint", "rate_limited"}:
                            warnings.append("Full descriptions could not be read for every result; available job cards are included.")
                            return _result(session, jobs, search_url, warnings, _gate_message(state), True)
                        if status and status >= 400:
                            warnings.append(f"{job['title']}: the detail page returned HTTP {status}; saved the search card.")
                            continue
                        jobs[index] = detail
                        if remote_only:
                            jobs[index]["remote"] = True
                        if not jobs[index]["description"]:
                            warnings.append(f"{job['title']}: no full description was available; saved the search card.")
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        with suppress(Exception):
                            state = classify_page(await _page_snapshot(page, None, False))
                            if state in {"login", "checkpoint", "rate_limited"}:
                                warnings.append("Full descriptions could not be read for every result; available job cards are included.")
                                return _result(session, jobs, search_url, warnings, _gate_message(state), True)
                        warnings.append(f"{job['title']}: the detail page could not be read; saved the search card.")
                message = f"Read {len(jobs)} LinkedIn {'job' if len(jobs) == 1 else 'jobs'}. The browser stays open for review."
                return _result(session, jobs, search_url, warnings, message)
        except TimeoutError:
            warnings.append("LinkedIn took too long to respond; search stopped after its time limit. Any results already read are included.")
            return _result(session, jobs, search_url, warnings, "LinkedIn search timed out. Inspect the browser and try a smaller search.")
        except SourceError:
            raise
        except Exception as exc:
            if page.is_closed() or not session["browser"].is_connected():
                raise SourceError("The LinkedIn browser was closed. Search again to open a new window.") from exc
            with suppress(Exception):
                state = classify_page(await _page_snapshot(page, None, False))
                if state in {"login", "checkpoint", "rate_limited"}:
                    return _result(session, jobs, search_url, warnings, _gate_message(state), True)
            raise SourceError("LinkedIn could not be loaded. Inspect the open browser, check your connection, and try again.") from exc
