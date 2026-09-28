"""Skill proposal storage, review provenance, and equivalent-name matching."""

import re
import unicodedata
from uuid import uuid4

from fastapi import HTTPException

MAX_SKILLS = 150
METADATA = ("kind", "support", "evidence", "rationale", "aliases", "origin")


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s\u2010-\u2015-]+", " ", value)


def merge_candidates(state: dict, candidates: list, *, origin: str) -> tuple[int, int]:
    """Append reviewable proposals; preserve confirmations and dismissed choices."""
    existing = {normalized_name(skill["name"]): skill for skill in state["skills"]}
    dismissed = set(state.setdefault("dismissed_skills", []))
    added, updated = 0, 0
    for candidate in candidates:
        proposal = candidate.model_dump() if hasattr(candidate, "model_dump") else dict(candidate)
        proposal["name"] = proposal["name"].strip()
        key = normalized_name(proposal["name"])
        if not key or (origin == "suggestion" and key in dismissed):
            continue
        proposal["origin"] = origin
        current = existing.get(key)
        if current:
            if current.get("confirmed"):
                continue
            # A related option cannot overwrite a skill explicitly listed in a CV.
            if current.get("origin") == "resume" and origin != "resume":
                continue
            if current.get("support") == "supported" and proposal.get("support") == "related":
                continue
            metadata = {field: proposal[field] for field in METADATA if field in proposal}
            if any(current.get(field) != value for field, value in metadata.items()):
                current.update(metadata)
                updated += 1
            continue
        if len(state["skills"]) >= MAX_SKILLS:
            if origin == "resume":
                raise HTTPException(422, "Your skill list is full (150 skills). Remove unwanted skills before importing more from your CV.")
            continue
        record = {**proposal, "id": uuid4().hex, "confirmed": False}
        state["skills"].append(record)
        existing[key] = record
        added += 1
    return added, updated


def refresh_job_matches(state: dict) -> None:
    from .jobs import score_job
    for job in state["jobs"]:
        job["match_score"], job["matched_skills"], _ = score_job(job, state)
