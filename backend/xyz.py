"""Google XYZ wording for resume bullets: accomplished X, as measured by Y, by doing Z.

The master resume keeps the applicant's own wording; resumes show an XYZ
version of each bullet written by the AI. A rewritten bullet is kept only if
every number in it already appears in that entry, so no metric is ever
invented; otherwise the original bullet is used. Each entry's XYZ version
records the wording it was written from and is ignored once that changes.
"""

from __future__ import annotations

import re

from . import ai, resumes


NUMBER = re.compile(r"\d[\d,.]*\d|\d")
FIELDS = ("xyz", "xyz_basis", "xyz_measured")


def numbers(text: str) -> set[str]:
    return {match.replace(",", "") for match in NUMBER.findall(text)}


def bullets(text: str) -> list[tuple[int, str, str]]:
    """(line index, marker, text) for each line a resume shows as a bullet."""
    lines = text.splitlines()
    marked = any(resumes.BULLET.match(line.strip()) or resumes.SUB_BULLET.match(line.strip()) for line in lines)
    found = []
    for index, line in enumerate(lines):
        line = line.strip()
        if resumes.SUB_BULLET.match(line):
            found.append((index, "◦ ", resumes.SUB_BULLET.sub("", line).strip()))
        elif resumes.BULLET.match(line):
            found.append((index, "• ", resumes.BULLET.sub("", line).strip()))
        elif line and not marked:
            found.append((index, "• ", line))  # Entries without bullet symbols are shown as bullets.
    return [entry for entry in found if entry[2]]


def current(item: dict) -> bool:
    return bool(item.get("xyz")) and item.get("xyz_basis") == resumes.text_hash(resumes.resume_source(item))


def pending(state: dict) -> list[dict]:
    """Confirmed entries with bullets whose XYZ version is missing or out of date."""
    return [item for item in state["items"] if item.get("confirmed") and bullets(resumes.resume_source(item)) and not current(item)]


def _allowed(item: dict) -> set[str]:
    return numbers(" ".join(str(item.get(key) or "") for key in ("title", "organization", "start", "end")) + " " + resumes.resume_source(item))


def assemble(item: dict, rewritten: list[ai.XYZBullet]) -> dict | None:
    """The entry's XYZ fields, or None when the rewrite doesn't line up with its bullets."""
    source = resumes.resume_source(item)
    found = bullets(source)
    if len(rewritten) != len(found):
        return None
    allowed, lines, measured = _allowed(item), source.splitlines(), []
    for (index, marker, text), new in zip(found, rewritten):
        wording = " ".join(new.text.split())
        kept = bool(wording) and numbers(wording) <= allowed  # A new number would be an invented metric.
        lines[index] = marker + (wording if kept else text)
        measured.append(kept and new.measured)
    return {"xyz": "\n".join(lines), "xyz_basis": resumes.text_hash(source), "xyz_measured": measured}


async def write(settings: dict, items: list[dict], effort: str | None = None, timeout: float | None = None) -> dict[str, dict]:
    """XYZ fields for each entry, by id; entries the AI got wrong are left out."""
    request = [{"id": item["id"], "title": item.get("title", ""), "organization": item.get("organization", ""),
                "entry": resumes.resume_source(item), "bullets": [text for _, _, text in bullets(resumes.resume_source(item))]}
               for item in items]
    result = await ai.rewrite_xyz(settings, request, effort=effort, timeout=timeout)
    rewrites = {entry.id: entry.bullets for entry in result.entries}
    written = {}
    for item in items:
        fields = assemble(item, rewrites.get(item["id"], []))
        if fields:
            written[item["id"]] = fields
    return written


def apply(state: dict, written: dict[str, dict]) -> tuple[int, int]:
    """Store XYZ versions whose entry wording hasn't changed meanwhile; returns (entries, bullets without a measure)."""
    count = unmeasured = 0
    for item in state["items"]:
        fields = written.get(item["id"])
        if fields and fields["xyz_basis"] == resumes.text_hash(resumes.resume_source(item)):
            item.update(fields)
            count += 1
            unmeasured += fields["xyz_measured"].count(False)
    return count, unmeasured
