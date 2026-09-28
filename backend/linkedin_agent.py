"""Bounded tools for an agent using PutMeTo's existing LinkedIn browser.

Page content is data. These tools expose no arbitrary JavaScript, clicking,
credentials, or application submission. Form references belong to one inspected
document and expire on the next inspection or navigation.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from backend import linkedin
from backend.network import SourceError, validate_url_shape


MAX_FIELDS = 50
MAX_FILL_FIELDS = 20
MAX_VALUE_LENGTH = 4000
MAX_TOTAL_VALUE_LENGTH = 20000
GATES = {"login", "checkpoint", "rate_limited"}
_snapshot: dict | None = None

# All DOM evaluation below is fixed application code, never an agent argument.
# The bundle retains actual element identities, so a replacement input with the
# same name/label cannot accidentally inherit an old reference.
_DOM_HELPERS_JS = r"""
const visible = node => {
    if (!node || !node.isConnected || node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse' && node.getClientRects().length > 0;
};
const text = (node, limit = 300) => {
    if (!node) return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('input, textarea, select, script, style, [hidden], [aria-hidden="true"]').forEach(child => child.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim().slice(0, limit);
};
const scopeFor = node => {
    const known = node.closest('.jobs-easy-apply-modal, .jobs-easy-apply-content, [data-test-modal-id="easy-apply-modal"], [data-test-easy-apply-content], form[aria-label="Job application"]');
    if (known && visible(known)) return known;
    const dialog = node.closest('[role="dialog"], dialog');
    if (!dialog || !visible(dialog)) return null;
    const title = [dialog.getAttribute('aria-label') || '', ...((dialog.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => text(document.getElementById(id)))), text(dialog.querySelector('h1, h2, h3, [role="heading"]'))].join(' ');
    return /\b(?:easy apply|apply (?:to|for)\b|job application)\b/i.test(title) ? dialog : null;
};
const describe = node => {
    const scope = scopeFor(node);
    if (!scope || !visible(node) || node.disabled || node.readOnly || node.matches(':disabled')) return null;
    if ([...scope.querySelectorAll('input[type="password"]')].some(visible)) return null;
    const tag = node.tagName.toLowerCase();
    const kind = tag === 'input' ? (node.getAttribute('type') || 'text').toLowerCase() : tag;
    if (!['text', 'email', 'tel', 'textarea', 'select'].includes(kind) || (kind === 'select' && node.multiple)) return null;
    const labelled = (node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => text(document.getElementById(id))).filter(Boolean).join(' ');
    const label = (labelled || node.getAttribute('aria-label') || [...(node.labels || [])].map(label => text(label)).filter(Boolean).join(' ') || node.getAttribute('placeholder') || node.getAttribute('name') || '').replace(/\s+/g, ' ').trim().slice(0, 300);
    const identity = [node.id, node.name, label, node.getAttribute('autocomplete') || ''].join(' ');
    if (/password|passcode|one[\s_-]?time|verification[\s_-]?code|security[\s_-]?code|session[\s_-]?(?:key|username|password)|(?:^|[\s_-])(?:username|otp|login|sign[\s_-]?in)(?:$|[\s_-])/i.test(identity)) return null;
    const options = kind === 'select' ? [...node.options].slice(0, 200).map(option => ({value: option.value.slice(0, 1000), label: text(option), disabled: !!(option.disabled || option.parentElement?.disabled)})) : [];
    return {label: label || 'Unlabelled application field', kind, required: !!node.required || node.getAttribute('aria-required') === 'true', options,
        identity: {id: node.id.slice(0, 300), name: node.name.slice(0, 300), autocomplete: (node.getAttribute('autocomplete') || '').slice(0, 100), maxlength: node.maxLength ?? -1, scopeLabel: (scope.getAttribute('aria-label') || '').slice(0, 300), scopeHeading: text(scope.querySelector('h1, h2, h3, [role="heading"]'))}};
};
const collect = () => {
    // Query only controls; return at most fifty eligible application inputs.
    const fields = [];
    let examined = 0;
    for (const element of document.querySelectorAll('input, textarea, select')) {
        if (++examined > 1000) break;
        const metadata = describe(element);
        if (metadata) fields.push({element, metadata, scope: scopeFor(element)});
        if (fields.length >= 50) break;
    }
    const application = fields.length > 0 || [...document.querySelectorAll('.jobs-easy-apply-modal, .jobs-easy-apply-content, [data-test-modal-id="easy-apply-modal"], [data-test-easy-apply-content], form[aria-label="Job application"], [role="dialog"], dialog')].some(node => visible(node) && scopeFor(node));
    const jobContext = {
        documentTitle: document.title.slice(0, 1000),
        mainHeader: text(document.querySelector('main, [role="main"]'), 600),
        title: text(document.querySelector('.job-details-jobs-unified-top-card__job-title, .top-card-layout__title, h1')),
        company: text(document.querySelector('.topcard__org-name-link, .job-details-jobs-unified-top-card__company-name'))
    };
    return {application, fields, jobContext};
};
"""

EXTRACT_APPLICATION_FIELDS_JS = "() => {" + _DOM_HELPERS_JS + "return {...collect(), url: location.href, document};}"
FIELD_METADATA_JS = "element => {" + _DOM_HELPERS_JS + "return describe(element);}"
VALIDATE_SNAPSHOT_JS = "bundle => {" + _DOM_HELPERS_JS + r"""
    if (bundle.document !== document || bundle.url !== location.href) return false;
    const current = collect();
    if (current.application !== bundle.application || current.fields.length !== bundle.fields.length || JSON.stringify(current.jobContext) !== JSON.stringify(bundle.jobContext)) return false;
    return current.fields.every((field, index) => {
        const old = bundle.fields[index];
        return field.element === old.element && field.scope === old.scope && JSON.stringify(field.metadata) === JSON.stringify(old.metadata);
    });
}"""

STUDY_PAGE_JS = r"""() => {
    const excluded = 'input, textarea, select, script, style, noscript, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="dialog"], dialog, .jobs-easy-apply-modal, .jobs-easy-apply-content, [data-test-modal-id="easy-apply-modal"], [hidden], [inert], [aria-hidden="true"]';
    const visible = node => {
        if (!node || !node.isConnected || node.closest(excluded)) return false;
        const style = getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse' && node.getClientRects().length > 0;
    };
    const plainText = (node, limit) => {
        if (!visible(node)) return '';
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        const pieces = [];
        let length = 0, examined = 0;
        for (let item = walker.nextNode(); item && ++examined <= 20000; item = walker.nextNode()) {
            if (!visible(item.parentElement)) continue;
            const value = (item.textContent || '').replace(/\s+/g, ' ').trim();
            if (!value) continue;
            pieces.push(value.slice(0, limit - length));
            length += value.length + 1;
            if (length >= limit) break;
        }
        return pieces.join('\n').slice(0, limit);
    };
    const attribute = (node, name) => (node.getAttribute(name) || '').slice(0, 500);
    const headingNodes = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')].filter(visible);
    const headings = headingNodes.slice(0, 40).map(node => ({tag: node.tagName.toLowerCase(), text: plainText(node, 400), className: attribute(node, 'class')}));
    const candidates = new Set(document.querySelectorAll('main, [role="main"], .jobs-details, .jobs-search__job-details--container, .job-view-layout, .scaffold-layout__detail, [data-view-name*="job"], [data-testid*="job"], [data-test-id*="job"], article, section'));
    // Randomized CSS classes still leave useful structural ancestors around
    // headings. Inspect those ancestors without exposing their HTML.
    for (const heading of headingNodes.slice(0, 40)) {
        let ancestor = heading.parentElement;
        for (let depth = 0; ancestor && depth < 3 && ancestor !== document.body; depth++, ancestor = ancestor.parentElement) candidates.add(ancestor);
    }
    const visibleContainers = [...candidates].filter(visible);
    const containers = visibleContainers.slice(0, 40).map(node => ({
        tag: node.tagName.toLowerCase(), role: attribute(node, 'role'),
        data_testid: attribute(node, 'data-testid') || attribute(node, 'data-test-id'),
        data_view_name: attribute(node, 'data-view-name'), className: attribute(node, 'class'), text_excerpt: plainText(node, 400)
    }));
    const selectors = [
        '.top-card-layout__title', '.job-details-jobs-unified-top-card__job-title',
        '.topcard__org-name-link', '.job-details-jobs-unified-top-card__company-name',
        '.topcard__flavor--bullet', '.job-details-jobs-unified-top-card__primary-description-container',
        '.show-more-less-html__markup', '.description__text', '.jobs-description__content',
        '.jobs-description-content__text', '.jobs-box__html-content',
        '.base-search-card', '.job-card-container', '.job-card-list', '[data-job-id]',
        '[data-view-name*="job"]', '[data-testid*="job"]', 'script[type="application/ld+json"]'
    ];
    const selector_counts = Object.fromEntries(selectors.map(selector => [selector, document.querySelectorAll(selector).length]));
    const main = [...document.querySelectorAll('main, [role="main"]')].find(visible) || document.body;
    const main_text = plainText(main, 20000);
    return {headings, containers, selector_counts, main_text,
        truncated: headingNodes.length > 40 || visibleContainers.length > 40 || main_text.length >= 20000};
}"""

AGENT_TOOLS = [
    {"name": "inspect_page", "description": "Read the existing LinkedIn tab and get job data and temporary application-field references. Does not navigate or launch a browser.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "study_page", "description": "Inspect the existing LinkedIn tab and report bounded visible headings, job-container metadata, selector counts, and job-page text to diagnose changed layouts. Excludes field values and dialogs; does not navigate.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_job", "description": "Open one LinkedIn /jobs/view/ URL in the persistent browser and read structured job information. Stops for login, security checks, and rate limits.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}},
    {"name": "fill_fields", "description": "Fill explicit text or select values in an already open application form, using field references from the latest inspection. Does not submit or advance the application.", "parameters": {"type": "object", "properties": {"snapshot_id": {"type": "string"}, "values": {"type": "object", "additionalProperties": {"type": "string", "maxLength": MAX_VALUE_LENGTH}, "minProperties": 1, "maxProperties": MAX_FILL_FIELDS}}, "required": ["snapshot_id", "values"], "additionalProperties": False}},
]


def _linkedin_url(url: str) -> str:
    normalized = validate_url_shape(url)
    host = (urlsplit(normalized).hostname or "").rstrip(".").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise SourceError("Open a LinkedIn jobs page in the existing Camoufox window before inspecting it.")
    return normalized


def _active_session() -> dict:
    session = linkedin._session
    if not session or not session["browser"].is_connected():
        raise SourceError("No LinkedIn browser is open. Search LinkedIn or read a job URL to reopen its saved profile.")
    if session["page"].is_closed():
        available = [page for page in session["context"].pages if not page.is_closed()]
        if not available:
            raise SourceError("The LinkedIn tab is closed. Search LinkedIn or read a job URL to reopen it.")
        session["page"] = available[0]
    return session


def _check_busy():
    if linkedin._search_lock.locked():
        raise SourceError("A LinkedIn browser operation is already running. Wait for it to finish.")


async def _invalidate_snapshot():
    global _snapshot
    previous, _snapshot = _snapshot, None
    if previous:
        for field in previous.get("fields", {}).values():
            with suppress(Exception):
                await field["element"].dispose()
        if previous.get("bundle"):
            with suppress(Exception):
                await previous["bundle"].dispose()


async def _extract_application_fields(page) -> tuple[object, list[dict], bool]:
    bundle = await page.evaluate_handle(EXTRACT_APPLICATION_FIELDS_JS)
    fields = []
    try:
        metadata = await bundle.evaluate("bundle => ({application: bundle.application, fields: bundle.fields.map(field => field.metadata)})")
        for index, item in enumerate(metadata["fields"][:MAX_FIELDS]):
            handle = await bundle.evaluate_handle("(bundle, index) => bundle.fields[index].element", index)
            element = handle.as_element()
            if element is None:
                await handle.dispose()
                raise SourceError("The application form changed while it was inspected. Inspect it again.")
            fields.append({"ref": "field_" + secrets.token_hex(12), "metadata": item, "element": element})
        return bundle, fields, bool(metadata["application"])
    except BaseException:
        for field in fields:
            with suppress(Exception):
                await field["element"].dispose()
        with suppress(Exception):
            await bundle.dispose()
        raise


def _gate_kind(snapshot: dict, session: dict) -> str:
    kind = linkedin.classify_page(snapshot)
    # Search predates these tools and may not retain the last HTTP status.
    # Preserve a known rate limit on an otherwise unreadable page.
    if kind == "unknown" and session.get("requires_action") and session.get("gate_url") == snapshot.get("url") and "rate limit" in session.get("message", "").lower():
        return "rate_limited"
    return kind


def _current_job_url(url: str) -> str:
    """A signed-in search may show a selected job beside its result list."""
    try:
        return linkedin.canonical_job_url(url)
    except SourceError:
        _linkedin_url(url)
        parts = urlsplit(url)
        job_id = parse_qs(parts.query).get("currentJobId", [""])[0]
        if parts.path.startswith("/jobs/") and job_id.isascii() and job_id.isdecimal() and 1 <= len(job_id) <= 20:
            return linkedin.canonical_job_url(f"https://www.linkedin.com/jobs/view/{job_id}/")
        raise SourceError("The current LinkedIn page has no selected job ID.")


def _actual_gate(kind: str, snapshot: dict) -> bool:
    if kind != "login":
        return kind in GATES
    # Public job pages can contain a sign-in form beside a full description.
    try:
        _current_job_url(snapshot["url"])
    except SourceError:
        return True
    return not snapshot.get("has_login_form") or snapshot.get("status") in {401, 403, 999}


async def _inspect_unlocked(session: dict, status: int | None = None) -> dict:
    global _snapshot
    await _invalidate_snapshot()
    page = session["page"]
    _linkedin_url(page.url)
    raw = await linkedin._page_snapshot(page, status, False)
    if status is None and session.get("gate_url") == page.url and session.get("requires_action"):
        # A manual reload can resolve a 429 without changing the URL. Visible
        # job content is evidence that the old HTTP status no longer applies.
        with suppress(Exception):
            raw["has_jobs"] = await page.evaluate("() => [...document.querySelectorAll('.show-more-less-html__markup, .jobs-description__content, .jobs-description-content__text, .base-search-card, .job-card-container')].some(node => node.getClientRects().length && (node.innerText || '').trim().length > 30)")
    kind = _gate_kind(raw, session)
    jobs, job, fields, warnings = [], None, [], []
    bundle, application = None, False
    if not _actual_gate(kind, raw):
        with suppress(Exception):
            jobs = (await linkedin._extract_cards(page))[:25]
        with suppress(SourceError):
            url = _current_job_url(page.url)
            seed = next((item for item in jobs if item["url"] == url), {"url": url, "title": ""})
            try:
                job = await linkedin._extract_detail(page, seed)
            except SourceError:
                warnings.append("No recognizable job title was found on this page.")
        kind = _gate_kind({**raw, "has_jobs": bool(jobs or (job and job.get("description")))}, session)
        if kind not in GATES and urlsplit(page.url).path.startswith("/jobs/"):
            bundle, fields, application = await _extract_application_fields(page)
    requires_action = kind in GATES
    if requires_action:
        fields, job, jobs = [], None, []
        message = linkedin._gate_message(kind).replace("run the search again", "inspect the page again")
        page_type = kind
    elif application:
        page_type, message = "application", "Application fields are available for explicit values. Review and submit the application yourself in the browser."
    elif job:
        page_type, message = "job", "Read the current LinkedIn job."
        if not job.get("description"):
            warnings.append("The page did not expose a complete job description.")
    elif jobs or "/jobs/search" in urlsplit(page.url).path:
        page_type = "empty" if kind == "empty" else "search"
        message = f"Read {len(jobs)} job cards from the current page."
    else:
        page_type, message = kind if kind == "empty" else "unknown", "The page has no recognizable job or application form. Open a job or application in the browser, then inspect again."
    if status and status >= 400 and not requires_action:
        warnings.append(f"The page returned HTTP {status}; its content may be incomplete.")
    snapshot_id = "snapshot_" + secrets.token_hex(16)
    if bundle:
        _snapshot = {"id": snapshot_id, "page": page, "session": session, "url": page.url, "job_url": job["url"] if job else None, "page_type": page_type, "bundle": bundle, "fields": {field["ref"]: field for field in fields}}
    else:
        _snapshot = {"id": snapshot_id, "page": page, "session": session, "url": page.url, "job_url": job["url"] if job else None, "page_type": page_type, "bundle": None, "fields": {}}
    public_fields = [{"ref": field["ref"], **{key: field["metadata"][key] for key in ("label", "kind", "required", "options")}} for field in fields]
    if requires_action:
        actions = [{"name": "user_action", "description": message}]
    else:
        actions = [{"name": "linkedin_inspect", "description": "Inspect again after manually changing the LinkedIn page."}]
        if jobs:
            actions.append({"name": "linkedin_read_job", "description": "Read a job using one of the returned canonical job URLs."})
        if fields:
            actions.append({"name": "linkedin_fill", "description": "Provide explicit answers keyed by the current field references. Fields never include saved answers."})
    session.update(requires_action=requires_action, message=message, gate_url=page.url if requires_action else "")
    return {"snapshot_id": snapshot_id, "url": page.url, "title": str(raw.get("title", ""))[:500], "page_type": page_type,
            "status": status, "requires_action": requires_action, "message": message, "warnings": warnings,
            "jobs": jobs, "job": job, "fields": public_fields, "actions": actions,
            "captured_at": datetime.now(timezone.utc).isoformat()}


async def inspect_page() -> dict:
    """Inspect only the existing managed tab, without navigation or browser launch."""
    _check_busy()
    async with linkedin._search_lock:
        try:
            async with asyncio.timeout(25):
                return await _inspect_unlocked(_active_session())
        except SourceError:
            raise
        except Exception as exc:
            await _invalidate_snapshot()
            raise SourceError("The LinkedIn page could not be inspected. Check the open browser and inspect it again.") from exc


async def study_page() -> dict:
    """Describe visible job-page structure without navigation or private state."""
    _check_busy()
    async with linkedin._search_lock:
        try:
            async with asyncio.timeout(30):
                session = _active_session()
                result = await _inspect_unlocked(session)
                result["structure"] = None
                if not result["requires_action"]:
                    page = session["page"]
                    if page.url != result["url"]:
                        raise SourceError("The LinkedIn page changed during inspection. Study it again.")
                    result["structure"] = await page.evaluate(STUDY_PAGE_JS)
                    if page.url != result["url"]:
                        raise SourceError("The LinkedIn page changed during inspection. Study it again.")
                return result
        except SourceError:
            raise
        except Exception as exc:
            await _invalidate_snapshot()
            raise SourceError("The LinkedIn page structure could not be read. Check the open browser and study it again.") from exc


async def read_job(url: str) -> dict:
    """Read one canonical job URL, preserving unfinished sign-in or challenges."""
    target = linkedin.canonical_job_url(url)
    _check_busy()
    async with linkedin._search_lock:
        try:
            async with asyncio.timeout(65):
                if linkedin._session and linkedin._session["browser"].is_connected():
                    session = _active_session()
                    if session["page"].url != "about:blank":
                        current = await _inspect_unlocked(session)
                        if current["requires_action"]:
                            return current
                await linkedin.validate_public_url(target)
                session = await linkedin._get_session()
                await _invalidate_snapshot()
                page = session["page"]
                response = await page.goto(target, wait_until="domcontentloaded", timeout=35000)
                await linkedin.validate_public_url(page.url)
                _linkedin_url(page.url)
                status = response.status if response else None
                raw = await linkedin._page_snapshot(page, status, False)
                if not _actual_gate(_gate_kind(raw, session), raw):
                    redirected_job = None
                    with suppress(SourceError):
                        redirected_job = _current_job_url(page.url)
                    if redirected_job and redirected_job != target:
                        raise SourceError("LinkedIn opened a different job than requested. Inspect the current page before saving or filling anything.")
                    with suppress(Exception):
                        await page.wait_for_selector('.show-more-less-html__markup, .jobs-description__content, .jobs-description-content__text, .jobs-easy-apply-modal, form[action*="login-submit"], #captcha-internal', timeout=5000, state="visible")
                result = await _inspect_unlocked(session, status)
                if result.get("job") and result["job"]["url"] != target:
                    await _invalidate_snapshot()
                    raise SourceError("LinkedIn opened a different job than requested. Inspect the current page before saving or filling anything.")
                return result
        except SourceError:
            raise
        except Exception as exc:
            await _invalidate_snapshot()
            raise SourceError("The LinkedIn job could not be read. Inspect the open browser before trying again.") from exc


def _validate_values(values: dict):
    if not isinstance(values, dict) or not 1 <= len(values) <= MAX_FILL_FIELDS:
        raise SourceError(f"Provide between 1 and {MAX_FILL_FIELDS} application fields to fill.")
    if any(not isinstance(ref, str) or not isinstance(value, str) for ref, value in values.items()):
        raise SourceError("Application field references and values must be strings.")
    if any(len(value) > MAX_VALUE_LENGTH or "\x00" in value for value in values.values()):
        raise SourceError(f"Each application answer must be at most {MAX_VALUE_LENGTH} characters and contain no null characters.")
    if sum(len(value) for value in values.values()) > MAX_TOTAL_VALUE_LENGTH:
        raise SourceError(f"Application answers must total at most {MAX_TOTAL_VALUE_LENGTH} characters.")


async def _validate_current(snapshot: dict, session: dict):
    if snapshot["session"] is not session or snapshot["page"] is not session["page"] or snapshot["page"].is_closed() or snapshot["url"] != session["page"].url:
        raise SourceError("The LinkedIn page changed. Inspect it again and use the new field references.")
    _linkedin_url(session["page"].url)
    if snapshot.get("page_type") != "application" or not snapshot.get("job_url") or _current_job_url(session["page"].url) != snapshot["job_url"]:
        raise SourceError("Open an application for an identified LinkedIn job, then inspect it before filling fields.")
    if not snapshot.get("bundle") or not await snapshot["bundle"].evaluate(VALIDATE_SNAPSHOT_JS):
        raise SourceError("The application form changed. Inspect it again and use the new field references.")


async def fill_fields(snapshot_id: str, values: dict) -> dict:
    """Fill reviewed answers only; never click Next, consent, upload, or submit."""
    _validate_values(values)
    _check_busy()
    async with linkedin._search_lock:
        filled = []
        try:
            async with asyncio.timeout(35):
                session = _active_session()
                snapshot = _snapshot
                if not isinstance(snapshot_id, str) or not snapshot or snapshot_id != snapshot["id"]:
                    raise SourceError("This page snapshot has expired. Inspect the page again and use its new field references.")
                if any(ref not in snapshot["fields"] for ref in values):
                    raise SourceError("An application field reference is unknown or expired. Inspect the page again.")
                raw = await linkedin._page_snapshot(session["page"], None, False)
                kind = _gate_kind(raw, session)
                if _actual_gate(kind, raw):
                    session.update(requires_action=True, message=linkedin._gate_message(kind), gate_url=session["page"].url)
                    raise SourceError(linkedin._gate_message(kind))
                await _validate_current(snapshot, session)
                # Validate every answer before mutating any field.
                for ref, value in values.items():
                    metadata = snapshot["fields"][ref]["metadata"]
                    if metadata["kind"] == "select" and not any(option["value"] == value and not option["disabled"] for option in metadata["options"]):
                        raise SourceError("A selected answer is not one of this field's available option values.")
                    maxlength = metadata.get("identity", {}).get("maxlength", -1)
                    if isinstance(maxlength, int) and maxlength >= 0 and len(value) > maxlength:
                        raise SourceError("An answer exceeds the application field's maximum length.")
                for ref, value in values.items():
                    await _validate_current(snapshot, session)
                    field = snapshot["fields"][ref]
                    if field["metadata"]["kind"] == "select":
                        await field["element"].select_option(value=value, timeout=3500)
                    else:
                        await field["element"].fill(value, timeout=3500)
                    filled.append(ref)
                fresh = await _inspect_unlocked(session)
                return {"filled": filled, "snapshot": fresh}
        except SourceError as exc:
            await _invalidate_snapshot()
            if filled:
                raise SourceError(f"Filled {len(filled)} fields before the form changed. Inspect the page to review those answers before continuing. {exc}") from exc
            raise
        except Exception as exc:
            await _invalidate_snapshot()
            prefix = f"Filled {len(filled)} fields before stopping. " if filled else ""
            raise SourceError(prefix + "The application form changed or could not be filled. Inspect it again and review the visible answers before continuing.") from exc
