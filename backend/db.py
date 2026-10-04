"""Small transactional SQLite store. Nothing is sent to a remote database."""

import copy
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable


DATA_DIR = Path(os.environ.get("PUTMETO_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))


def default_state() -> dict:
    return {
        "schema_version": 2,
        "profile": {key: "" for key in ("name", "email", "phone", "location", "headline", "website", "summary")},
        "items": [],
        "skills": [],
        "dismissed_skills": [],
        "positions": [],
        "preferences": {"track": "industry", "location": "", "remote_only": False},
        "sources": [
            {"id": "remotive", "name": "Remotive", "kind": "remotive", "url": "https://remotive.com/api/remote-jobs", "enabled": True},
            {"id": "linkedin", "name": "LinkedIn", "kind": "linkedin", "url": "https://www.linkedin.com/jobs/search/", "enabled": False},
            {"id": "greenhouse", "name": "Greenhouse board", "kind": "greenhouse", "url": "", "enabled": False},
            {"id": "lever", "name": "Lever board", "kind": "lever", "url": "", "enabled": False},
            {"id": "career-page", "name": "Company career page", "kind": "camoufox", "url": "", "enabled": False},
        ],
        "jobs": [],
        "linkedin_searches": [],
        "applications": [],
        "application_answers": {},
        "auto_apply": {"enabled": False},
        "settings": {"provider": "codex", "base_url": "", "model": "", "api_key": ""},
    }


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = DATA_DIR / "workspace.sqlite3"
    connection = sqlite3.connect(path, timeout=15)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)")
    connection.execute("INSERT OR IGNORE INTO workspace (id, state) VALUES (1, ?)", (json.dumps(default_state()),))
    connection.commit()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return connection


def _load(connection: sqlite3.Connection) -> dict:
    state = json.loads(connection.execute("SELECT state FROM workspace WHERE id=1").fetchone()[0])
    defaults = default_state()
    if state.get("schema_version", 1) < 2:
        sources = state.setdefault("sources", copy.deepcopy(defaults["sources"]))
        if not any(source.get("kind") == "linkedin" for source in sources):
            new_source = copy.deepcopy(next(source for source in defaults["sources"] if source["kind"] == "linkedin"))
            if any(source.get("id") == new_source["id"] for source in sources):
                from uuid import uuid4
                new_source["id"] = uuid4().hex
            sources.append(new_source)
        state["schema_version"] = 2
    for key, value in defaults.items():
        state.setdefault(key, copy.deepcopy(value))
    for key in ("profile", "preferences", "settings"):
        state[key] = {**defaults[key], **state[key]}
    return state


def initialize() -> None:
    # Persist upgrades once so intentionally deleted sources stay deleted.
    mutate_state(lambda state: None)


def get_state() -> dict:
    connection = _connect()
    try:
        return _load(connection)
    finally:
        connection.close()


def mutate_state(callback: Callable[[dict], Any]) -> Any:
    """Persist a callback's changes atomically, rolling back on every exception."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = _load(connection)
        result = callback(state)
        connection.execute("UPDATE workspace SET state=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
