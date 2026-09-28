"""Semantic adapters for LinkedIn layouts whose CSS classes are randomized.

The fallback requires the document title and visible main content to agree.
It never derives a job title from an arbitrary heading or a work-mode badge.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit


_NON_JOB_LABELS = {
    "onsite", "remote", "hybrid", "fulltime", "parttime", "contract",
    "temporary", "internship", "volunteer", "apply", "applynow", "easyapply",
    "save", "saved", "share", "showmore", "seemore", "follow", "following",
    "activelyrecruiting", "beanearlyapplicant", "jobdetails", "viewjob",
}


def is_non_job_title(value: str) -> bool:
    """Reject exact UI labels while retaining titles such as Remote Engineer."""
    key = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return bool(re.fullmatch("(?:" + "|".join(sorted(_NON_JOB_LABELS)) + ")+", key))


def _line(value: object, limit: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(value).split())[:limit]


def _description(structure: dict, lines: list[str]) -> str:
    headings = structure.get("headings")
    if not isinstance(headings, list):
        return ""
    about = next((index for index, heading in enumerate(headings) if isinstance(heading, dict) and _line(heading.get("text")).casefold() == "about the job"), None)
    if about is None:
        return ""
    start = next((index for index, line in enumerate(lines) if line.casefold() == "about the job"), None)
    if start is None:
        return ""
    # The DOM reader can establish the nearest section boundary directly.
    scoped = structure.get("description_text")
    if isinstance(scoped, str) and scoped.strip():
        return scoped.strip()[:60000]
    level = headings[about].get("tag", "h2")
    level = int(level[1:]) if isinstance(level, str) and re.fullmatch(r"h[1-6]", level) else 2
    boundaries = set()
    for heading in headings[about + 1:]:
        if not isinstance(heading, dict):
            continue
        tag = heading.get("tag", "h2")
        rank = int(tag[1:]) if isinstance(tag, str) and re.fullmatch(r"h[1-6]", tag) else 2
        if rank <= level:
            boundaries.add(_line(heading.get("text")).casefold())
    end = next((index for index in range(start + 1, len(lines)) if lines[index].casefold() in boundaries), None)
    # Without a proven end boundary, mixing employer/marketing content into a
    # resume is worse than returning an explicitly incomplete description.
    if end is None:
        return ""
    return "\n".join(lines[start + 1:end]).strip()[:60000]


def parse_study_job(snapshot: dict) -> dict | None:
    """Parse a saved study or fixed DOM-reader result without browser/network I/O.

    Returns a normalized job only when a canonical job URL and independently
    matching document-title/visible-title/company evidence are present.
    Ambiguous, gated, or mismatched observations return None.
    """
    from backend.linkedin import canonical_job_url, normalize_job
    from backend.network import SourceError, validate_url_shape

    if not isinstance(snapshot, dict) or snapshot.get("requires_action") or snapshot.get("page_type") in {"login", "checkpoint", "rate_limited"} or snapshot.get("status") in {401, 403, 429, 999}:
        return None
    structure = snapshot.get("structure")
    if not isinstance(structure, dict) or not isinstance(structure.get("main_text"), str):
        return None
    try:
        url = canonical_job_url(snapshot.get("url", ""))
    except SourceError:
        try:
            parts = urlsplit(validate_url_shape(snapshot.get("url", "")))
            host = (parts.hostname or "").lower()
            job_id = parse_qs(parts.query).get("currentJobId", [""])[0]
            if (host != "linkedin.com" and not host.endswith(".linkedin.com")) or not parts.path.startswith("/jobs/") or not re.fullmatch(r"[0-9]{1,20}", job_id):
                return None
            url = canonical_job_url(f"https://www.linkedin.com/jobs/view/{job_id}/")
        except (SourceError, TypeError, AttributeError):
            return None
    title_parts = _line(snapshot.get("title"), 1500).rsplit(" | ", 2)
    if len(title_parts) != 3 or title_parts[2].casefold() != "linkedin":
        return None
    title = re.sub(r"^\(\d+\)\s*", "", title_parts[0]).strip()
    company = title_parts[1].strip()
    if not title or not company or len(title) > 300 or len(company) > 300 or is_non_job_title(title):
        return None
    lines = [_line(line, 60000) for line in structure["main_text"][:90000].splitlines() if _line(line, 60000)]
    first = [line.casefold() for line in lines[:30]]
    if title.casefold() not in first or company.casefold() not in first:
        return None
    title_index, company_index = first.index(title.casefold()), first.index(company.casefold())
    if abs(title_index - company_index) > 3:
        return None
    about_index = next((index for index, line in enumerate(lines) if line.casefold() == "about the job"), len(lines))
    if max(title_index, company_index) >= about_index:
        return None
    header = lines[max(title_index, company_index) + 1:min(about_index, max(title_index, company_index) + 20)]
    tokens = [_line(part) for line in header for part in re.split(r"\s*[·•]\s*", line) if _line(part)]
    posted_pattern = r"\b(?:(?:re)?posted\s+)?(?:\d+|an?|one)\s+(?:minute|hour|day|week|month|year)s?\s+ago\b"
    location = ""
    for candidate in tokens[:5]:
        if not candidate or candidate in {"-", "|"} or is_non_job_title(candidate):
            continue
        if re.search(posted_pattern, candidate, re.I) or re.search(r"applicants?|clicked apply|promoted|responses managed|recruiting|hiring|^[$€£]", candidate, re.I):
            continue
        location = candidate[:300]
        break
    posted_at = next((match.group(0) for token in tokens if (match := re.search(posted_pattern, token, re.I))), "")
    employment = next((token for token in tokens if re.fullmatch(r"full[\s-]?time|part[\s-]?time|contract|temporary|internship|volunteer", token, re.I)), "")
    salary = next((token[:300] for token in tokens if re.match(r"^[$€£]\s*\d", token)), "")
    raw = {"title": title, "company": company, "url": url, "location": location,
           "description": _description(structure, lines), "posted_at": posted_at,
           "job_type": employment, "salary": salary,
           "remote": any(token.casefold() == "remote" for token in tokens)}
    try:
        return normalize_job(raw)
    except SourceError:
        return None


SEMANTIC_DETAIL_JS = r"""() => {
    const excluded = 'input, textarea, select, script, style, noscript, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="dialog"], dialog, .jobs-easy-apply-modal, .jobs-easy-apply-content, [hidden], [inert], [aria-hidden="true"]';
    const visible = node => {
        if (!node || !node.isConnected || node.closest(excluded)) return false;
        const style = getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse' && node.getClientRects().length > 0;
    };
    const text = (node, limit = 60000) => {
        if (!visible(node)) return '';
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        const parts = [];
        let size = 0, count = 0;
        for (let item = walker.nextNode(); item && ++count < 20000; item = walker.nextNode()) {
            if (!visible(item.parentElement)) continue;
            const value = (item.textContent || '').replace(/\s+/g, ' ').trim();
            if (value) {parts.push(value.slice(0, limit - size)); size += value.length + 1;}
            if (size >= limit) break;
        }
        return parts.join('\n').slice(0, limit);
    };
    const main = [...document.querySelectorAll('main, [role="main"]')].find(visible);
    if (!main) return {url: location.href, title: document.title, structure: {headings: [], main_text: ''}};
    const headingNodes = [...main.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')].filter(visible).slice(0, 100);
    const headings = headingNodes.map(node => ({tag: node.tagName.toLowerCase(), text: text(node, 400)}));
    const about = headingNodes.find(node => text(node, 400).toLowerCase() === 'about the job');
    let description_text = '';
    if (about) {
        const rank = Number(about.tagName.slice(1)) || 2;
        let container = about.parentElement;
        for (let depth = 0; container && container !== main && depth < 6; depth++, container = container.parentElement) {
            const otherTopHeading = [...container.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')].some(node => node !== about && visible(node) && (Number(node.tagName.slice(1)) || 2) <= rank);
            if (otherTopHeading) break;
            const content = text(container).split('\n');
            const index = content.findIndex(line => line.toLowerCase() === 'about the job');
            const after = index >= 0 ? content.slice(index + 1).join('\n').trim() : '';
            if (after.length >= 60) {description_text = after; break;}
        }
    }
    return {url: location.href, title: document.title, structure: {headings, main_text: text(main, 90000), description_text}};
}"""
