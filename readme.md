# PutMeTo

A personal job search web application that runs on your computer. Import your CV, review AI suggestions, choose the skills and positions that represent you, discover jobs, and prepare applications from confirmed content.

The interface and SQLite database run locally. Use Codex with your ChatGPT sign-in (no GPT API key), local Ollama, or a compatible server. No Node.js installation or frontend build is needed.

## Start the application

Requirements: **Python 3.11 or newer** and a web browser.

From this project directory on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Keep the terminal running; press **Ctrl+C** to stop. On subsequent starts, activate the environment and run `python run.py`.

The server binds to `127.0.0.1`. This is a single-user local application without account authentication; it is not intended for public hosting.

## Configure AI

### Codex with ChatGPT — no API key

1. In **Settings**, choose **Codex — sign in with ChatGPT** and save. Leave the model blank to use your account's default model.
2. Open **Assistant**. If Codex already recognizes your ChatGPT sign-in on this computer, it connects automatically. Otherwise select **Sign in with ChatGPT**, finish the browser sign-in, and return to the app.
3. In Settings, **Test connection** verifies a real structured response. CV extraction, rephrasing, skill/position suggestions, and tailoring now use the same Codex connection.
4. Chat in **Assistant**, for example: “Show my saved jobs”, “Find jobs in the US and Europe”, or “Help me improve my resume.”

The assistant can read your workspace, save resume drafts, propose and confirm skills/positions with your review, search LinkedIn, save named searches, inspect job pages, and prepare tailored resumes. Skill proposals distinguish evidenced methods from related technologies you may know; each includes context and a reason. It shows activity while working and asks for approval of exact resume confirmations or LinkedIn form answers. **Stop** interrupts a response; **New chat** starts a separate conversation while keeping your workspace. The local chat display retains the latest 200 messages, and the Codex conversation resumes after a restart.

The project installs a pinned official Codex runtime through `openai-codex`; it does not depend on a separate global `codex` command. Codex manages authentication using its normal account storage. PutMeTo does not read or copy your sign-in tokens, and inherited API-key variables are excluded from its Codex process. ChatGPT/Codex usage limits apply. Prompts and relevant tool results are sent to OpenAI; the web server, workspace and LinkedIn browser run on your computer.

The app gives Codex a fixed set of application tools. It does not expose a terminal, arbitrary filesystem access, or final application submission. LinkedIn sign-in remains separate from ChatGPT sign-in and uses the existing persistent Camoufox profile. If a site asks for verification or rate limits a request, the assistant stops and reports the required action.

Implementation: [Codex integration](docs/codex.md). Official references: [App Server](https://learn.chatgpt.com/docs/app-server), [SDK](https://learn.chatgpt.com/docs/codex-sdk), and [authentication](https://learn.chatgpt.com/docs/auth).

### Local Ollama

1. [Install Ollama](https://ollama.com/download).
2. Download the default model:

   ```bash
   ollama pull llama3.2
   ```

3. Open Ollama, or start its server in a separate terminal if it is not already running:

   ```bash
   ollama serve
   ```

4. In PutMeTo **Settings**, choose **Ollama**, use `http://127.0.0.1:11434` as the server URL and `llama3.2` as the model, then save and test the connection.

You can select another installed model that supports structured JSON output. Model quality and speed depend on the model and your computer. The app checks generated JSON before saving it, but you still need to review the meaning and accuracy of suggested wording. See the [Ollama quickstart](https://docs.ollama.com/quickstart) and [chat API documentation](https://docs.ollama.com/api/chat).

For local inference, choose a locally installed model and a local server URL. Selecting a cloud model or remote endpoint sends the supplied content outside your computer.

### Compatible API server

In **Settings**, select the compatible API provider, enter its API base URL, exact model name, and an API key if required. For example, a local server might use `http://127.0.0.1:1234/v1`.

The app appends `/chat/completions` and requires JSON response support. Use the API base URL, not the full completion endpoint. Save and test the connection.

Resume content, confirmed background, and job descriptions needed for each operation are sent to the configured AI server. A remote provider may charge for requests and applies its own data policies. API keys are stored locally in the workspace database, omitted from API responses, and cleared when changing provider or server URL unless a new key is supplied.

Without a working AI connection, you can still enter and review content manually, manage preferences and jobs, and export confirmed content. Manual experience saves preserve the original wording as an unconfirmed draft when AI is unavailable. CV extraction, AI suggestions, rephrasing, and tailoring require a working connection.

## Use your workspace

1. **Build your profile.** Under **My profile**, import a text-based PDF, Word `.docx`, or UTF-8 `.txt` CV, or add experience, projects, education, and publications manually. The importer reads the dedicated skills section as well as experience. Word paragraphs, tables, text boxes, headers, and footers are supported. Older `.doc` files must be saved as `.docx` or PDF. PDFs are recognized by their contents even without a `.pdf` filename. The import dialog shows elapsed time and any error directly, preserving the selected file for retry. Uploads are limited to 10 MB, 100 PDF pages, and 70,000 extracted characters. Scanned PDFs need OCR before import. Imports append draft entries and merge skill candidates; they do not overwrite populated profile fields or confirmed skills.
2. **Review every entry.** Compare the original with the suggested description, edit the suggestion as needed, and confirm that it accurately describes your work. Changes to an entry's source content trigger another enhancement and reset confirmation. Rephrasing also creates a draft for another review.
3. **Confirm technical skills.** Review skills extracted from the CV and generate additional suggestions using that section, confirmed experience, and skills you already confirmed. Suggestions favor transferable methods: “fine-tuned Llama 2” supports **LLM fine-tuning**, with the model name retained as evidence. Related database and vector-database products are offered separately as options to review; they are not assumed qualifications. Each candidate shows its category, supporting context, and reason. Confirm only the skills you have; unconfirmed suggestions never enter resumes or matching. Dismissed suggestions are not automatically proposed again. For CVs imported with an earlier version, use **Import skills from CV** to recover the missing section without duplicating experience.
4. **Choose your direction.** Under **Job preferences**, choose industry or academia, location, and remote preference. Generate or manually add job titles, then confirm the positions you want to explore.
5. **Choose job sources.** Enable the sources you want and supply real company board URLs for Greenhouse, Lever, or a custom careers page. LinkedIn is available as an optional browser source and searches independently of your saved roles. Other sources use confirmed target positions for matching. Source failures are reported separately so other sources can still return results.
6. **Discover and save jobs.** Run discovery, inspect descriptions, and save relevant opportunities. **Search LinkedIn** also supports a direct search before completing your profile. You can add a job by pasting its public URL and description. Duplicate listing URLs are skipped. The match percentage measures title and skill keyword overlap; it is not a hiring probability or an employer's ATS score.
7. **Tailor and review your resume.** Open a job and choose **Suggest relevant skills** to prioritize your skill review using its description, then **Tailor my resume**. Job requirements help prioritize options but never establish that you possess a skill. AI prioritizes your existing confirmed entries within their sections and your confirmed skills, while preserving reviewed wording. Equivalent skill names (such as PostgreSQL/Postgres) also participate in keyword matching; competing products remain separate skills. Preview or download the PDF before applying. Changes to saved profile details or confirmed content invalidate existing tailored resumes so they can be regenerated.
8. **Complete the application.** With optional Camoufox support installed, **Open application** opens a visible browser, fills recognized empty contact fields, and attaches the tailored PDF where it recognizes a resume upload. Complete login, unanswered questions, and any verification yourself, then review and submit on the site. Return to PutMeTo and mark the application submitted. Opening the browser alone never counts as a submitted application.

The resume uses a single-column, text-based layout with standard section headings, HTML print preview, and PDF export. Automated tests check text extraction and exclusion of unconfirmed content. This is an ATS-friendly template, not an externally certified template or a guarantee that every applicant tracking system will parse it identically.

## Optional Camoufox browser

Camoufox is required for LinkedIn search, custom careers-page discovery, and assisted application forms. Remotive, Greenhouse, Lever, profile editing, and PDF export work without it.

With the project environment activated:

```bash
python -m pip install -r requirements-browser.txt
python -m camoufox fetch
```

Restart PutMeTo after installation. The browser download is separate from the Python package. Installation or launch may require additional system libraries on Linux; consult the [official Camoufox installation instructions](https://camoufox.com/python/installation/).

Application sessions run in separate browser windows, with at most three active sessions. Close unused windows before opening more. The app attempts common fields on the page it opens; it does not complete arbitrary multi-step forms or automatically submit them. If a site redirects, requires login, or uses an unrecognized upload control, complete those steps and attach the downloaded PDF manually.

Camoufox provides browser automation and fingerprint handling through its [Python interface](https://camoufox.com/python/). **There is no guarantee that automation will be undetectable.** Sites may restrict access or show verification challenges.

## Search LinkedIn

After installing Camoufox, open **Discover jobs → Search LinkedIn**, or use **Search LinkedIn** on its source card. **United States** and **Europe** start selected, keywords start blank for **any job title**, and the remote-only option starts off. This search does not use saved position, skill, or location preferences and requires neither a completed profile nor an AI connection.

Leave keywords empty to search across all titles, or supply your own keywords. Keep either or both regions selected, or enter a custom location to override the selected regions. The app opens a visible Camoufox window, reads available job cards and detail pages, and saves results in your workspace. The default is **10 jobs per region**, with a maximum of **25 per region**. Each search reads a bounded batch of listings; it does not retrieve every job in the United States or Europe. Review any warnings: descriptions can be partial or unavailable when detail pages cannot be read.

If LinkedIn requests sign-in or verification, complete it yourself in the opened browser, then return to the unchanged search form and select **Search LinkedIn** again. The app does not solve or bypass those gates and does not retry automatically. Rate limits or unavailable pages may require trying again later. Any readable cards can still be returned with warnings.

LinkedIn uses a persistent local Camoufox profile at `data/linkedin-browser-profile/`, or under your configured `PUTMETO_DATA_DIR`. Cookies and browser storage survive normal window closure and application restarts, so you can reuse your sign-in. The app does not record your plaintext password. Browser cookies can grant account access, so treat this directory and its backups as sensitive. LinkedIn can still expire a session or request verification.

If you signed in with an earlier version that used temporary sessions, that login cannot be recovered after its browser closed. Sign in once in the new persistent browser profile to retain the session going forward.

To include LinkedIn in **Discover jobs** across selected sources, enable its source card. Its canonical source URL is `https://www.linkedin.com/jobs/search/`. This mode also searches across all job titles in the United States and Europe, with up to 10 jobs per region and no saved-role filtering. LinkedIn starts disabled, including when the source is added to an existing workspace.

### Standalone search script

The same search is available without starting the web application:

```bash
python linkedin_search.py --limit 10 --output artifacts/linkedin/jobs.json --keep-open --wait-for-login
```

Run it in the activated project environment after installing the browser requirements. With no keywords or location flags, it searches all job titles in the United States and Europe. An optional positional keyword phrase narrows titles, `--location "London"` replaces the two default regions with one location, or repeat `--region` up to five times to choose your own regions (for example, `--region Germany --region France`). `--remote` requests remote jobs.

`--wait-for-login` keeps the process waiting when manual sign-in or verification is needed; finish in the browser and press Enter in the terminal to retry. `--keep-open` leaves the browser open after the search until you close its window or press Ctrl+C. Without that flag, the browser closes when the script finishes. The persistent login profile is retained after normal closure.

The script exports JSON only; it does not save jobs to the application's SQLite workspace. `--output` writes the full result, including jobs, warnings, and any action-needed status, to the specified file; without it, the result is printed. The script defaults to 10 jobs per region and accepts limits from 1 to 25 per region.

## Job sources and integration limits

| Source | Configuration | Current behavior |
| --- | --- | --- |
| Remotive | Enable the supplied source. | Uses the public remote jobs API. Responses are cached for six hours. Remotive documents that public listings are delayed by 24 hours. Listings retain Remotive attribution and their source link. |
| Greenhouse | A company board URL, such as `https://job-boards.greenhouse.io/company`. Replace `company` with a real board token. | Reads that company's public job board and descriptions. Supports recognized global and EU board URLs; processes up to 2,000 jobs per source. |
| Lever | A company board URL, such as `https://jobs.lever.co/company`. | Reads that company's public postings; supports global and EU boards and requests up to 500 postings. |
| LinkedIn | Enable the supplied source, or use **Search LinkedIn**; Camoufox installed. | Defaults to any job title in the United States and Europe, independently of saved roles. Reads a batch of up to 10 jobs per region; direct searches allow up to 25 per region. The local browser profile retains login; sign-in/verification gates require your attention. |
| Custom careers page | A public careers or job-detail URL; Camoufox installed. | Reads structured `JobPosting` JSON-LD. If the starting page has no postings, visits up to eight matching job-detail links on the same site. It does not scrape arbitrary page layouts. |
| Manual job | Public job URL, title, company, and description. | Saves the supplied opportunity for review, tailoring, and tracking. |

Greenhouse and Lever need individual company boards; they are not global job search engines. Each discovery across selected sources processes the 500 highest-ranked matching candidates and saves new URLs from that set. There are no dedicated Indeed or academic-job portal integrations. The academia preference affects suggested target roles; discovery still uses your configured sources.

Location matching uses the location text supplied by sources, including worldwide/remote indicators; it does not geocode addresses or infer eligibility. Review location restrictions in each job description. Source availability, data quality, and site layout affect results.

Integration references: [Remotive public API and usage terms](https://github.com/remotive-com/remote-jobs-api), [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html), and [Lever Postings API](https://github.com/lever/postings-api). The application uses these APIs for discovery; form submission remains under your control in the browser.

## Local data and backups

Your profile, original and reviewed entries, confirmed skills and roles, preferences, source configuration, jobs, applications, tailored resumes, chat display/history reference, and AI settings are stored in:

```text
data/workspace.sqlite3
```

The directory is created automatically under the project root. Remotive's temporary cache and the persistent `linkedin-browser-profile/` directory also live in `data/`. Uploaded CV files are read for extraction rather than retained as original file copies. API credentials and resume content in the SQLite database are **not encrypted at rest**; protect this directory and its backups, including the LinkedIn session cookies.

To back up your workspace, stop PutMeTo and copy the entire `data/` directory to your backup location. To restore it, keep the app stopped, preserve any current data you want to retain, and copy the backed-up directory into place before restarting. Backing up the complete directory avoids separating a SQLite database from any remaining journal files.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUTMETO_DATA_DIR` | `data/` under the project root | Select another workspace directory. An absolute path is recommended. |
| `PUTMETO_PORT` | `8000` | Change the local web server port. The host remains `127.0.0.1`. |

For example, on macOS or Linux:

```bash
PUTMETO_DATA_DIR="/absolute/path/to/my-workspace" PUTMETO_PORT=8001 python run.py
```

The app rejects nonlocal Host headers and cross-origin writes. Job requests accept public HTTP/HTTPS destinations and reject private-network targets. AI URLs intentionally allow localhost. These protections support the local-use model; they do not add multi-user authentication.

## Publishing the source

The repository excludes local workspace data, LinkedIn browser sessions, credentials, CV documents, exports, backups, logs, and generated screenshots. These files remain on your computer. Keep any custom `PUTMETO_DATA_DIR` outside the repository or in an ignored directory.

Before committing or pushing, run:

```bash
python3 scripts/check_public_repo.py
```

This checks publishable working files, staged contents, and files in reachable Git history for private file paths and recognizable secrets. It reports locations without printing matched values. Review the changes too: arbitrary personal information in prose cannot be identified reliably by a pattern scanner. [Publication guidance](docs/publishing.md) covers the review and commit-email privacy.

## Development and verification

### LinkedIn tools for agents

Use the running app from another program with `linkedin_agent.py`. It exposes page inspection, reading and saving a job URL, local job lookup, named searches you can run later, and explicit filling of supported application fields for your review. It reuses the persistent LinkedIn browser profile.

```bash
python linkedin_agent.py tools
python linkedin_agent.py inspect
python linkedin_agent.py jobs --query "engineer"
python linkedin_agent.py save-search "US and Europe" --limit 10
```

See [the agent guide](docs/linkedin-agent.md) for the Python client, CLI, API schemas, saved-search workflow, and form-filling examples. An agent runtime can call these tools; a language model is not required by this client.

### Automated checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Coverage includes PDF and DOCX import, evidence-based skill review, structured AI validation, draft review and rephrasing, confirmed-only exports, settings and credential handling, persistence, request-origin protection, job matching and discovery, regional LinkedIn searches, agent page inspection and form filling, saved searches, browser-session behavior, and application tracking. Tests use temporary databases, synthetic applicants and jobs, and mocked network/AI/browser responses; they do not submit real applications or prove every external website works. Optional browser checks are skipped unless explicitly enabled.

Synthetic LinkedIn DOM fixtures cover semantic job extraction on pages with generated CSS classes, rejection of work-mode links mistaken for job cards, and exclusion of unrelated marketing content. Live LinkedIn availability varies by region, session, page layout, and rate limits; automated fixture checks do not establish current live coverage.

For the optional desktop/mobile UI smoke test, install Playwright and have Google Chrome installed:

```bash
python -m pip install playwright
python tests/browser_smoke.py --artifacts artifacts/browser
```

This script starts an isolated local server with a temporary database and AI/browser stubs. It exercises the interface in real Chrome, including experience review, skills and positions, tailoring, resume preview, application tracking, and mobile layouts. It also checks LinkedIn's blank-keyword US/Europe defaults, preserved inputs after a login gate, manual retry, and source URL autofill. It does not access your existing workspace or external job sites. `--port` changes its server port; `PUTMETO_CHROME` can point to your Chrome executable. Screenshots go to the optional artifacts directory. The latest run passed all seven routes and the full application workflow on desktop and mobile with no JavaScript errors.

To verify that LinkedIn browser storage survives a restart using a temporary profile and a synthetic page:

```bash
PUTMETO_RUN_PERSISTENCE_TESTS=1 python -m pytest -q tests/test_linkedin.py -k retains_cookie_and_local_storage
```

This check closes and reopens Camoufox, then verifies a test cookie and local storage. It does not read your LinkedIn cookies or access LinkedIn. The latest run passed, confirming both survive a browser restart.

After installing Camoufox and its browser binary, an additional live browser check is available on macOS or Linux:

```bash
PUTMETO_BROWSER_SMOKE=1 python -m pytest -q tests/test_browser_live.py
```

That check visits `example.com` and exercises filling a synthetic application form and browser cleanup. The form is supplied inside the browser; it does not send an application. The initial live run passed navigation, five contact fields, PDF attachment, and session cleanup. This test is skipped in the regular suite unless the environment variable is set.

Implementation layout:

```text
backend/
  main.py       FastAPI routes, input validation, local request protection
  db.py         Transactional SQLite workspace
  ai.py         Ollama / compatible API calls and structured outputs
  resumes.py    HTML and PDF resume rendering
  jobs.py       Source discovery, matching, job and application tracking
  network.py    Public URL validation and bounded HTTP requests
  browser.py    Optional Camoufox discovery and assisted forms
  linkedin.py   Visible LinkedIn search, detail reading, and session lifecycle
static/         Browser interface; plain JavaScript and CSS
tests/          Automated workflow and integration tests
run.py          Local server entry point
linkedin_search.py  Standalone LinkedIn search with JSON export
```

Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) while the server runs. [IMPLEMENTATION.md](IMPLEMENTATION.md) describes the shared state and endpoint contract. The [original project requirements](docs/original-requirements.md) are preserved separately.

## Troubleshooting

- **AI cannot connect:** Start the configured server, check its URL, confirm that the model is installed, and use **Test connection** in Settings. Do not start another `ollama serve` process if Ollama is already listening on that port.
- **AI returns invalid JSON or times out:** Try a model with better structured-output support or import a shorter CV. Failed imports do not partially confirm content. Manually entered experience is retained as a draft when enhancement fails.
- **PDF has no readable text:** Run OCR first or export a UTF-8 text version. The app does not perform OCR.
- **No jobs appear:** Confirm target titles, enable at least one valid source, and try broader titles or location criteria. Read any per-source warning. A custom page must expose structured job data.
- **LinkedIn asks for sign-in or verification:** Complete it in the visible Camoufox window and submit the same search form again. For the standalone script, use `--wait-for-login` and press Enter when ready. A login from the old temporary browser cannot be recovered; sign in once in the persistent profile after upgrading. Searches can also be limited by session expiry, site availability, or rate limits.
- **Browser cannot launch:** Install `requirements-browser.txt`, download the browser with `python -m camoufox fetch`, and restart the application.
- **Some form fields remain blank:** Fill them manually in the opened browser. Check the attached resume before submitting.
- **Port 8000 is already in use:** Stop the other server or set `PUTMETO_PORT` to a free local port.
