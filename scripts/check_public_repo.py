#!/usr/bin/env python3
"""Read-only publication checks; no network, hooks, or file changes.

Checks publishable working files, the Git index, and every reachable commit.
This is a guard against known private files and high-confidence secrets, not a
proof that arbitrary personal information is absent. Review the final diff too.
Ignored/private files are classified by path without reading their contents.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


MAX_TEXT_BYTES = 8 * 1024 * 1024
PRIVATE_DIRS = {
    "data", "artifacts", "uploads", "exports", "backups", "resumes", "cvs",
    "screenshots", "codex-worker", ".codex", ".agents", ".claude", ".cursor",
    ".ssh", ".aws", ".azure", ".config", ".vscode", ".idea", "browser-profile", "browser-profiles",
    "linkedin-browser-profile", "camoufox-profile", "chrome-profile",
    "chromium-profile", "firefox-profile",
}
PRIVATE_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".docm", ".odt", ".rtf", ".pages", ".sqlite", ".sqlite3",
    ".db", ".db3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".log", ".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx", ".bak",
    ".backup", ".dump", ".sql", ".keystore",
)
PRIVATE_NAMES = re.compile(
    r"^(?:credentials?|secrets?|tokens?|auth|cookies?|storage[-_]?state|"
    r"session[-_]?state|browser[-_]?state|client_secret|id_rsa|id_ed25519|id_ecdsa|id_dsa)"
    r"(?:[._-].*)?$", re.I,
)
SECRET_PATTERNS = (
    ("openai-token", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
)
HOME_PATH = re.compile(r"/(?:Users|home)/([^/\s\"'`<>]+)")
HOME_PLACEHOLDERS = {"user", "username", "yourname", "your_name", "your_username", "your-user", "name", "example", "$user", "${user}"}


class AuditError(Exception):
    """A check could not finish; messages must never contain command output."""


def git(repo: Path, *args: str, data: bytes | None = None, allowed=(0,)) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode not in allowed:
        raise AuditError("git-check-failed")
    return result.stdout


def decode_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def private_path(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or ".." in parts:
        return "unsafe-path"
    lower = [part.lower() for part in parts]
    if any(part in PRIVATE_DIRS or part.endswith("-browser-profile") for part in lower[:-1]):
        return "private-directory"
    name = lower[-1]
    if (name == ".env" or name.startswith(".env.")) and name not in {".env.example", ".env.sample"}:
        return "environment-file"
    if name.endswith(PRIVATE_SUFFIXES):
        return "private-document-or-storage"
    if re.search(r"\.(?:db|sqlite|sqlite3)-|\.log\.", name) or (
        name.startswith(("cv", "resume")) and name.endswith(".txt")
    ) or (name.startswith("workspace") and name.endswith(".json")):
        return "private-document-or-storage"
    if PRIVATE_NAMES.fullmatch(name):
        return "credentials-or-session-file"
    return None


def inspect_text(content: bytes) -> list[tuple[int, str]]:
    if len(content) > MAX_TEXT_BYTES:
        return [(0, "unreviewed-large-file")]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [(0, "unreviewed-binary-file")]
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        return [(0, "unreviewed-binary-file")]
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for category, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append((line_number, category))
        if any(match.group(1).lower() not in HOME_PLACEHOLDERS for match in HOME_PATH.finditer(line)):
            findings.append((line_number, "personal-machine-path"))
    return findings


def read_worktree(repo: Path, path: str) -> bytes:
    """Open each component without following symlinks, including parent dirs."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd:
        raise AuditError("unsupported-safe-file-reading")
    parts = PurePosixPath(path).parts
    directory = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(file_descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise AuditError("unreviewed-file-type")
            return source.read(MAX_TEXT_BYTES + 1)
    finally:
        os.close(directory)


def ignored_paths(repo: Path, paths: set[str]) -> set[str]:
    if not paths:
        return set()
    payload = b"".join(os.fsencode(path) + b"\0" for path in sorted(paths))
    output = git(repo, "check-ignore", "--no-index", "-z", "--stdin", data=payload, allowed=(0, 1))
    return {decode_path(path) for path in output.split(b"\0") if path}


def index_entries(repo: Path) -> list[tuple[str, str, str]]:
    entries = []
    for record in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split()
            if stage != "0":
                raise AuditError("unresolved-index-conflict")
            entries.append((decode_path(path), mode, object_id))
    return entries


def history_entries(repo: Path) -> set[tuple[str, str, str]]:
    entries = set()

    def visit_tree(object_id: str) -> None:
        for record in git(repo, "ls-tree", "-r", "-z", object_id).split(b"\0"):
            if record:
                metadata, path = record.split(b"\t", 1)
                mode, _kind, object_id = metadata.decode("ascii").split()
                entries.add((decode_path(path), mode, object_id))

    for commit in git(repo, "rev-list", "--all").decode("ascii").splitlines():
        visit_tree(commit)
    # Include direct tree/blob references, such as local Codex snapshot refs.
    refs = git(repo, "for-each-ref", "--format=%(refname)").decode("utf-8", errors="surrogateescape")
    for ref in refs.splitlines():
        object_id = git(repo, "rev-parse", "--verify", ref + "^{}").decode("ascii").strip()
        kind = git(repo, "cat-file", "-t", object_id).strip()
        if kind == b"tree":
            visit_tree(object_id)
        elif kind == b"blob":
            entries.add((ref, "100644", object_id))
        elif kind != b"commit":
            raise AuditError("unreviewed-reference")
    return entries


def audit(repo: Path) -> list[tuple[str, int, str]]:
    repo = Path(os.fsdecode(git(repo, "rev-parse", "--show-toplevel")).strip()).resolve()
    index = index_entries(repo)
    history = history_entries(repo)
    tracked = {path for path, _mode, _oid in index}
    working = {
        decode_path(path) for path in git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
        if path
    }
    excluded = ignored_paths(repo, working | {entry[0] for entry in history})
    findings: set[tuple[str, int, str]] = set()
    blob_results: dict[str, list[tuple[int, str]]] = {}

    def record(path: str, source: str, line: int, category: str) -> None:
        findings.add((path, line, source + "/" + category))

    def blocked(path: str, source: str) -> bool:
        if path in excluded:
            record(path, source, 0, "ignored-file-tracked")
            return True
        category = private_path(path)
        if category:
            record(path, source, 0, category)
            return True
        return False

    for path in sorted(working):
        if blocked(path, "worktree"):
            continue
        try:
            content = read_worktree(repo, path)
        except FileNotFoundError:
            # A tracked deletion is still checked in the index/history below.
            if path not in tracked:
                record(path, "worktree", 0, "unreadable-file")
            continue
        except AuditError as error:
            record(path, "worktree", 0, str(error))
            continue
        except OSError:
            record(path, "worktree", 0, "unreadable-file-or-symlink")
            continue
        for line, category in inspect_text(content):
            record(path, "worktree", line, category)

    for source, entries in (("index", index), ("history", sorted(history))):
        for path, mode, object_id in entries:
            if blocked(path, source):
                continue
            if mode not in {"100644", "100755"}:
                record(path, source, 0, "unreviewed-symlink-or-submodule")
                continue
            if object_id not in blob_results:
                size = int(git(repo, "cat-file", "-s", object_id))
                if size > MAX_TEXT_BYTES:
                    blob_results[object_id] = [(0, "unreviewed-large-file")]
                else:
                    blob_results[object_id] = inspect_text(git(repo, "cat-file", "blob", object_id))
            for line, category in blob_results[object_id]:
                record(path, source, line, category)
    return sorted(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Git repository to audit (default: current directory)")
    args = parser.parse_args(argv)
    try:
        findings = audit(args.repo)
    except (AuditError, OSError, ValueError):
        print('"<repository>":0: audit/incomplete-check', file=sys.stderr)
        return 2
    for path, line, category in findings:
        # JSON quoting prevents filenames from injecting terminal escapes/lines.
        print(f"{json.dumps(path, ensure_ascii=True)}:{line}: {category}")
    if findings:
        return 1
    print("No publication risks detected by the local checks; review the final diff for personal information.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
