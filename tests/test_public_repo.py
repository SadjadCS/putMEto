"""Publication checks use synthetic temporary repositories and never credentials."""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from scripts import check_public_repo as guard


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "Example", "GIT_AUTHOR_EMAIL": "example@example.invalid",
             "GIT_COMMITTER_NAME": "Example", "GIT_COMMITTER_EMAIL": "example@example.invalid"},
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--quiet")
    return tmp_path


def write(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_clean_source_json_text_and_svg_are_publishable(repo):
    write(repo, "main.py", "print('Example application')\n")
    write(repo, "fixtures/example.json", '{"name": "Example"}')
    write(repo, "requirements.txt", "pytest\n")
    write(repo, "favicon.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
    write(repo, ".env.example", "API_KEY=\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Example sources")
    assert guard.audit(repo) == []


def test_ignored_private_files_are_never_read_or_reported(repo, monkeypatch):
    write(repo, ".gitignore", "data/\n.env\n*.pdf\n")
    write(repo, "data/auth.json", "synthetic private contents")
    write(repo, ".env", "synthetic environment")
    write(repo, "resume.pdf", "synthetic document")
    original = guard.read_worktree
    accessed = []

    def checked_read(root, path):
        assert path not in {"data/auth.json", ".env", "resume.pdf"}
        accessed.append(path)
        return original(root, path)

    monkeypatch.setattr(guard, "read_worktree", checked_read)
    assert guard.audit(repo) == []
    assert accessed == [".gitignore"]


def test_force_added_ignored_path_fails_without_reading_it(repo, monkeypatch):
    write(repo, ".gitignore", "data/\n")
    write(repo, "data/auth.json", "never read this")
    git(repo, "add", "-f", "data/auth.json")
    original = guard.git

    def checked_git(root, *args, **kwargs):
        assert "cat-file" not in args
        return original(root, *args, **kwargs)

    monkeypatch.setattr(guard, "git", checked_git)
    findings = guard.audit(repo)
    assert ("data/auth.json", 0, "index/ignored-file-tracked") in findings


def test_index_is_scanned_even_when_worktree_was_cleaned(repo, capsys):
    token = "sk-" + "A" * 32
    write(repo, "settings.py", "# Synthetic fixture\nKEY = '" + token + "'\n")
    git(repo, "add", "settings.py")
    write(repo, "settings.py", "KEY = ''\n")
    assert guard.main(["--repo", str(repo)]) == 1
    output = capsys.readouterr().out
    assert '"settings.py":2: index/openai-token' in output
    assert "worktree/openai-token" not in output
    assert token not in output
    assert "KEY =" not in output


def test_reachable_history_still_fails_after_secret_is_removed(repo):
    write(repo, "settings.py", "TOKEN = '" + "ghp_" + "x" * 36 + "'\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Synthetic old configuration")
    write(repo, "settings.py", "TOKEN = ''\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Remove synthetic token")
    assert guard.audit(repo) == [("settings.py", 1, "history/github-token")]


def test_history_checks_private_paths_even_for_same_blob_renamed(repo):
    write(repo, "resumes/notes.txt", "Synthetic text\n")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Synthetic private path")
    git(repo, "mv", "resumes/notes.txt", "notes.txt")
    git(repo, "commit", "--quiet", "-m", "Rename same blob")
    assert ("resumes/notes.txt", 0, "history/private-directory") in guard.audit(repo)


def test_history_includes_direct_tree_and_blob_refs(repo):
    write(repo, "settings.py", "TOKEN = '" + "ghp_" + "x" * 36 + "'\n")
    git(repo, "add", ".")
    tree = git(repo, "write-tree").stdout.decode().strip()
    blob = git(repo, "rev-parse", ":settings.py").stdout.decode().strip()
    git(repo, "update-ref", "refs/snapshots/example-tree", tree)
    git(repo, "update-ref", "refs/snapshots/example-blob", blob)
    write(repo, "settings.py", "TOKEN = ''\n")
    git(repo, "add", ".")
    findings = guard.audit(repo)
    assert ("settings.py", 1, "history/github-token") in findings
    assert ("refs/snapshots/example-blob", 1, "history/github-token") in findings


@pytest.mark.parametrize("name", [
    "data/workspace.json", "uploads/cv.txt", ".codex/auth.json", ".ssh/id_rsa",
    "person.DOCX", "personal.pdf", ".env.production", "backup.zip", "run.log",
    "workspace-copy.json", "cv-final.txt", "profile.sqlite3-wal", "credentials.json",
])
def test_private_paths_fail_without_ignore_rules(repo, name):
    write(repo, name, "Synthetic contents")
    assert any(path == name for path, _line, _category in guard.audit(repo))


@pytest.mark.parametrize("text,category", [
    ("github_pat_" + "x" * 50, "github-token"),
    ("AKIA" + "X" * 16, "aws-access-key"),
    ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private-key"),
    ("/Users/" + "actual-person/projects/app", "personal-machine-path"),
    ("/home/" + "actual-person/app", "personal-machine-path"),
])
def test_secret_categories_never_include_the_value(text, category):
    assert guard.inspect_text(text.encode()) == [(1, category)]


def test_generic_home_path_placeholders_remain_usable():
    text = "\n".join("/Users/" + user + "/project" for user in ("user", "<name>", "YOUR_USERNAME", "$USER"))
    assert guard.inspect_text(text.encode()) == []


def test_binary_and_symlink_are_not_silently_approved(repo):
    (repo / "image.png").write_bytes(b"\x89PNG\x00test")
    outside = repo.parent / "outside.txt"
    outside.write_text("do not read")
    (repo / "linked.txt").symlink_to(outside)
    findings = guard.audit(repo)
    assert ("image.png", 0, "worktree/unreviewed-binary-file") in findings
    assert ("linked.txt", 0, "worktree/unreadable-file-or-symlink") in findings


def test_filename_cannot_inject_terminal_output(repo, capsys):
    name = "odd\n\x1b[31m.py"
    write(repo, name, "sk-" + "B" * 32)
    assert guard.main(["--repo", str(repo)]) == 1
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert "\x1b" not in output
    assert "\\n" in output and "\\u001b" in output


def test_cli_fails_closed_outside_git_without_disclosing_local_path(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(guard.__file__)), "--repo", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert str(tmp_path) not in result.stderr
    assert "incomplete-check" in result.stderr


def test_unsupported_safe_reading_fails_without_a_traceback(repo, monkeypatch, capsys):
    write(repo, "main.py", "print('Example')\n")
    monkeypatch.delattr(guard.os, "O_NOFOLLOW")
    assert guard.main(["--repo", str(repo)]) == 1
    output = capsys.readouterr().out
    assert "worktree/unsupported-safe-file-reading" in output
