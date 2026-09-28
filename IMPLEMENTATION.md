# Implementation contract

Local Python 3.11+ FastAPI application, SQLite persistence, vanilla ES module frontend served from `static/`. Run via `python run.py`, bound to 127.0.0.1:8000. No Node build step.

## Shared state

`backend/db.py` exports `get_state() -> dict`, `mutate_state(callback) -> Any` (transactional synchronous callback receiving mutable state, persists changes, returns callback result), `initialize()`, and `DATA_DIR: Path`. State keys:

- `profile`: `{name,email,phone,location,headline,website,summary}`
- `items`: `[{id,kind,title,organization,start,end,original,enhanced,confirmed}]`, kind experience/project/education/publication
- `skills`: `[{id,name,confirmed,kind?,support?,evidence?,rationale?,aliases?,origin?}]`. `support` is supported/related; `origin` is resume/suggestion/manual. Existing records remain valid. Only confirmed skills enter resumes/matching, even when supported by a CV.
- `dismissed_skills`: additive list of normalized names excluded from automatic suggestions.
- `positions`: `[{id,name,confirmed}]`
- `preferences`: `{track: 'industry'|'academia',location,remote_only}`
- `sources`: `[{id,name,kind,url,enabled}]`, kind remotive/greenhouse/lever/camoufox/linkedin. LinkedIn defaults to disabled and is added once to preexisting workspaces by migration; canonical URL `https://www.linkedin.com/jobs/search/`.
- `jobs`: `[{id,title,company,location,url,source,description,salary,job_type,posted_at,match_score,matched_skills,status,created_at,resume?}]`; status discovered/saved/prepared/applied/archived
- `applications`: `[{id,job_id,status,created_at,updated_at,note}]`
- `settings`: `{provider:'codex'|'ollama'|'compatible',base_url,model,api_key}` (GET strips api_key, exposes api_key_set). Codex allows blank model for the account default and always clears API keys.
- `codex_chat`: additive `{thread_id,messages}` conversation metadata; excluded from `/api/state`, available through `/api/codex/chat`.

## Core endpoints (backend/main.py + AI services)

- GET `/api/state`: full sanitized state
- GET `/api/health`: local service readiness
- PUT `/api/profile`: complete profile object
- POST `/api/import`: multipart `file` PDF, DOCX or TXT; extracts with AI, appends draft items, merges profile and deduplicated unconfirmed CV skills; returns `{count,skill_count,message}`
- POST `/api/import/skills`: same upload types; recovers skills only, preserving profile/experience and confirmed choices; returns `{count,skill_count,updated,message}`
- POST `/api/items`: fields kind,title,organization,start,end,original; AI creates enhanced draft; returns item. If AI unavailable, keep original draft and show warning.
- PUT `/api/items/{id}`: fields as above plus enhanced,confirmed. When original/content fields change rerun enhancement and reset confirmed. Confirmation alone persists reviewed enhanced version.
- DELETE `/api/items/{id}`
- POST `/api/items/{id}/enhance`: retry AI wording generation and reset confirmation; failure preserves the existing item
- POST `/api/suggestions/skills`: optional `{job_id}`; typed candidates use CV skills, confirmed experience, and confirmed skills. Broader supported techniques and related unverified options carry evidence/rationale. A job description controls relevance, never applicant facts. Stale input returns 409; dismissed candidates remain excluded.
- POST `/api/suggestions/positions`: generates unconfirmed roles from confirmed content
- PUT `/api/skills` and `/api/positions`: `{items:[{id?,name,confirmed}]}`; skills may round-trip display metadata, but unchanged existing records preserve server provenance. Skill review updates saved-job matches and invalidates tailored resumes when confirmations change.
- PUT `/api/preferences`: full preferences
- PUT `/api/settings`: provider,base_url,model,api_key optional (missing/empty retains stored key),clear_api_key optional
- POST `/api/settings/test`: checks configured AI and returns `{message}`
- POST `/api/jobs/{id}/tailor`: prepares resume using confirmed content only, saves job.resume, sets status prepared, returns `{message,resume}`
- GET `/api/jobs/{id}/resume`: printable HTML
- GET `/api/jobs/{id}/resume.pdf`: PDF generated with reportlab
- GET `/api/resume`: general printable HTML from confirmed content
- GET `/api/resume.pdf`: general PDF

## Job endpoints (backend/jobs.py exports router)

- PUT `/api/sources`: `{items:[source...]}`
- POST `/api/jobs/discover`: searches enabled sources using confirmed positions and skills; returns `{count,message,warnings:[]}`. Source failures isolated. Persist and dedupe by URL.
- POST `/api/jobs`: `{title,company,location,url,description,salary?,job_type?}` manually save job
- PATCH `/api/jobs/{id}`: `{status}`
- POST `/api/jobs/{id}/apply`: launches visible Camoufox session, fills safe common form fields and tailored PDF when available; no silent submission; `{message,application}`
- POST `/api/applications/{id}/confirm`: user confirms externally completed submission; updates job + application applied
- GET `/api/browser/status`: browser installation/session info
- POST `/api/linkedin/search`: `{keywords:'',regions:['United States','Europe'],remote_only:false,limit:10,save:true}`; keywords are optional (blank means any job title), limit is 1–25 per region, and no confirmed profile or target role is required. Opens/reuses a visible Camoufox session and returns `{count,found,jobs,requires_action,message,search_url,warnings}`. `count` is newly saved jobs; `found` is the number of valid results. `save:false` returns results without workspace persistence. Saved results are deduplicated by canonical LinkedIn job URL.
- GET `/api/linkedin/status`: `{active,busy,requires_action,search_url,message,...}` for the current search browser and its persistent profile.

## LinkedIn integration

`backend/linkedin.py` provides bounded visible browser search and detail extraction, `search_linkedin(keywords, location='', remote_only=False, limit=10)`, `linkedin_status()`, and `close_linkedin()`. Blank keywords search any job title; this primitive searches the explicit location supplied by its caller. The API coordinates regional searches. A persistent Camoufox context stores cookies/browser data at `db.DATA_DIR / 'linkedin-browser-profile'`; normal closure/restart preserves the profile. No plaintext login password is recorded by the application. Login, checkpoints, and verification are handled by the user; no automatic bypass or unattended retry. Partial results and unavailable descriptions are reported through warnings.

The LinkedIn source in normal discovery searches any job title in the United States and Europe, with a limit of 10 per region and no filtering by saved skills/positions. The dedicated frontend modal (`#linkedin-form`) defaults to empty keywords, both regions selected, remote-only false, and limit 10 per region. An optional custom location overrides the selected region list. Search is independent of saved profile preferences and reads a bounded batch, not an exhaustive inventory. On `requires_action:true`, retain all form inputs, keep the modal open, and direct the user to complete the gate in the existing browser before manually submitting again. On completion, refresh the workspace, clear job filters, and navigate to jobs.

`linkedin_search.py` offers the same browser search as a standalone CLI: optional positional keywords (default blank), `--location` to replace default United States/Europe with one location, repeatable `--region` for up to five chosen regions, `--remote`, `--limit` (1–25 per region, default 10), `--output`, `--keep-open`, and `--wait-for-login`. It exports JSON without writing to SQLite. The login flag waits for explicit terminal input before retrying; normal closure preserves the browser profile.

Agent operations are in `backend/linkedin_agent.py`, with the local API and persistence in `backend/linkedin_routes.py`. They reuse the same browser and operation lock as search. Inspection returns temporary field references and structured job data; study adds bounded page-layout evidence; job reads can save or refresh local records. Filling accepts explicit text/select answers against the current document and form identity, without clicking or submitting. Snapshots expose no current field values. Gates stop operations and leave the browser open.

`linkedin_agent.py` is a provider-neutral Python HTTP client and JSON CLI for the running app; it does not own a second browser. The `/api/linkedin/agent/tools` catalog describes the available calls. Named search definitions live in the additive `linkedin_searches` workspace list, with their last-run result. `/api/linkedin/agent/jobs` searches saved LinkedIn records without a browser request or tailored resume data. See `docs/linkedin-agent.md` for schemas and usage.

Camoufox is optional dependency; show clear actionable errors if missing. Browser managers must clean up during FastAPI lifespan shutdown via `await close_linkedin()` from backend/linkedin.py and `await close_browsers()` from backend/jobs.py. Do not claim applications submitted unless confirmed. All fetched/AI text treated as untrusted content. Include practical tests. Keep networking bounded and validate external job URLs (http/https public addresses), AI URLs permit localhost by intent. Bind loopback and enforce same-origin mutating requests.
