# Publishing PutMeTo without local personal data

Publish the source through Git. Keep your workspace, imported CVs, credentials, and browser sessions on your computer.

## What stays local

`.gitignore` excludes:

- `data/`: contact details, CV content, AI settings, chat references, saved jobs, and the authenticated LinkedIn profile.
- `artifacts/`, `screenshots/`, `uploads/`, `exports/`, `backups/`, `resumes/`, and `cvs/`.
- PDF and Word documents, CV/resume text files, SQLite databases and their journals, logs, dumps, and backup archives.
- Environment files, known credential/cookie files, private keys, local Codex settings, editor settings, virtual environments, and caches.

Sanitized `.env.example` and `.env.sample` files are allowed. Never put real credentials in them. If you configure `PUTMETO_DATA_DIR`, choose a location outside the repository or an ignored directory. For ordinary text/JSON exports, use `exports/` or `artifacts/`; arbitrary filenames cannot reveal whether their contents are private.

These exclusions do not delete or reset your local application data. They also do not remove a file already committed, and `git add -f` can bypass them.

## Before a commit or push

Run the local check from the project directory with Python 3 and Git on macOS or Linux:

```bash
python3 scripts/check_public_repo.py
git add --dry-run .
```

The check inspects nonignored working files, the actual staged blobs, files in every locally reachable Git commit, and direct tree/blob references. It flags private paths, tracked files covered by ignore rules, recognizable secret formats, personal home-directory paths, and binary files that need manual review. It does not read ignored private files or print matched secret values. A failure must be resolved before publication. It is a manual check, not an automatically installed Git hook. Commit messages, tag annotations, and author details require separate review. Platforms lacking safe symlink-resistant file reads receive a failure rather than a partial approval.

Review the files selected for your commit. After staging, run the check again and review `git diff --cached --stat` and `git diff --cached` locally. Do not copy sensitive diff output into public issues or chats. Tests and examples must use synthetic applicant data and placeholder URLs, rather than your actual CV or browsing history.

A passing pattern check cannot prove that arbitrary names, addresses, resume paragraphs, or other personal information are absent. Review new screenshots and prose before sharing them. Do not use a Finder/Explorer ZIP of the whole project as the public release: filesystem archives can include files that Git ignores.

## Commit identity

GitHub publishes the author/committer name and email embedded in commits. Use your desired public identity and the exact GitHub no-reply address shown in your account's email settings if you want to keep your personal email private. Configure these for this repository before your first commit. `.gitignore` does not protect commit metadata.

## If private data was already committed

Removing a file in a later commit leaves its earlier contents in history. Stop before pushing and remove it from all history you intend to publish. If a credential or session token has already been published, revoke or rotate it as well; deleting the repository alone does not make that credential private again. A local scan covers local refs only, not unrelated remote branches, previous GitHub uploads, or releases.
