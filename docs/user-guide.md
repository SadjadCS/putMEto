# User guide

[Back to PutMeTo](../readme.md) · [Build a custom agent](custom-agents.md)

Detailed setup, workflow, browser, and troubleshooting instructions.

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

New workspaces use the local Codex CLI by default. The built-in **Assistant** chat uses Codex. Provider selection controls CV extraction, rephrasing, suggestions, and tailoring; those features also work with local Ollama or a compatible server when selected in **Settings**.

### Codex with ChatGPT — no API key

Use the Codex access included in your ChatGPT subscription. [ChatGPT sign-in provides subscription access without an API key](https://learn.chatgpt.com/docs/auth); usage is subject to your plan's limits and account permissions.

1. In **Settings**, choose **Codex — sign in with ChatGPT** and save. Leave the model blank to use your account's default model.
2. Open **Assistant**. If Codex already recognizes your ChatGPT sign-in on this computer, it connects automatically. Otherwise select **Sign in with ChatGPT**, finish the browser sign-in, and return to the app.
3. In Settings, **Test connection** verifies the connection. CV extraction, rephrasing, skill and position suggestions, and tailoring use the selected AI provider.
4. Chat in **Assistant**, for example: “Show my saved jobs”, “Find jobs in the US and Europe”, or “Help me improve my resume.”

The assistant can read your workspace, save resume drafts, propose and confirm skills/positions with your review, search LinkedIn, save named searches, inspect job pages, and prepare tailored resumes. Skill proposals distinguish evidenced methods from related technologies you may know; each includes context and a reason. It shows activity while working and asks for approval of exact resume confirmations or LinkedIn form answers. **Stop** interrupts a response; **New chat** starts a separate conversation while keeping your workspace. The local chat display retains the latest 200 messages, and the Codex conversation resumes after a restart.

PutMeTo runs the official Codex CLI locally in App Server mode. Installation includes a pinned runtime through `openai-codex`, so you can get started without installing a global `codex` command. It uses Codex's normal account storage and can reuse your existing ChatGPT sign-in. PutMeTo does not read or copy your sign-in tokens, and inherited API-key variables are excluded from its Codex process. Prompts and relevant tool results are sent to OpenAI; the web server, workspace and LinkedIn browser run on your computer.

The app gives Codex a fixed set of application tools. It does not expose a terminal, arbitrary filesystem access, or final application submission. LinkedIn sign-in remains separate from ChatGPT sign-in and uses the existing persistent Camoufox profile. If a site asks for verification or rate limits a request, the assistant stops and reports the required action.

Implementation: [Codex integration](codex.md). Official references: [App Server](https://learn.chatgpt.com/docs/app-server), [SDK](https://learn.chatgpt.com/docs/codex-sdk), and [authentication](https://learn.chatgpt.com/docs/auth).

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

1. **Build your profile.** Under **My profile**, import your CV as a PDF, or add entries manually. Save Word, Pages, or Google Docs CVs as PDF first. Each page is turned into an image that the AI reads, so multi-column layouts and scanned CVs work. Your own wording is kept: the AI first transcribes the pages, then only decides which transcribed lines belong to which entry, and descriptions are copied from those lines, never rewritten. Entries without a description stay empty. All extracted information is accepted by default, and imported experience and skills are immediately available for resumes and job matching. Use **Import skills from CV** to add confirmed skills while keeping existing experience entries.
2. **Edit if you want.** Your imported profile, experience, projects, education, and publications are ready to use. You can edit or remove anything. Manually added entries and fresh AI rewordings still need confirmation before use.
3. **Manage technical skills.** Import reads your CV's dedicated skills section first. Suggestions use that section as their starting point and draw on confirmed experience to add broad, meaningful skills aligned with it. Older imports use saved CV skills when section information is unavailable. Skills extracted from your CV are confirmed automatically. You can uncheck or remove any skill. Additional AI suggestions need your confirmation before they are used in job matching or resumes. On **My profile**, your skills are listed in groups by context, such as Programming Languages, Multi-Agent Frameworks, Large Language Models, Machine Learning, Web Development, and Databases. The AI chooses groups that fit your skills, and new skills join a matching group automatically. **Group by context** groups everything again from scratch. Groups only organize the list; your resume keeps the skill labels from your CV.

After your master CV is ready, the AI also makes a **bag of similar terms** for each technical term in it, in two parts. Other names mean exactly the same thing: abbreviations and full names (LLM and Large Language Model, RAG and Retrieval-Augmented Generation) and other spellings (PostgreSQL and Postgres, scikit-learn and sklearn). Other phrasings are the ways postings and recruiters phrase the same skill (software development and software engineering, agentic AI and AI agents). A bag never holds a different product or a broader or narrower category: PyTorch is not deep learning. Each job's resume then uses the posting's choice from the bag. A name is switched in the text: an abbreviation and its full name are written together once, as in "Large Language Models (LLMs)", so a search for either finds it, and other spellings take the posting's form everywhere. A phrasing is added to that job's skills as the posting writes it, because swapping it inside a sentence could break it ("agentic AI systems" would become "AI agents systems"). Only terms from the bag of a term your CV already uses are written, and your saved resume and master CV keep your wording; the posting's choices are applied when the job's resume is previewed, downloaded, checked, or sent. The bags are at the bottom of **Your technical skills** under **Similar terms for your skills** (= other names, ≈ other phrasings), and are made again whenever your master CV changes.
4. **Choose your direction.** Under **Job preferences**, choose industry or academia, location, and remote preference. After your master resume is built, the same model lists **every job title it supports**: each career track your experience covers, its specializations, and the seniority levels your years and scope support. Titles are grouped by track; solid outlines are strong fits and dashed ones possible fits, and hovering a title shows why. **Suggest roles** lists them again from your current resume. Check the titles you want to search for, or add your own. Removing an unchecked suggestion means it is not suggested again; checked roles and roles you added are never removed.
5. **Choose job sources.** Enable the sources you want and supply real company board URLs for Greenhouse, Lever, or a custom careers page. LinkedIn is available as an optional browser source and searches independently of your saved roles. Other sources use confirmed target positions for matching. Source failures are reported separately so other sources can still return results.
6. **Discover and save jobs.** Run discovery, inspect descriptions, and save relevant opportunities. **Search LinkedIn** also supports a direct search before completing your profile. You can add a job by pasting its public URL and description. Duplicate listing URLs are skipped. The match percentage measures title and skill keyword overlap; it is not a hiring probability or an employer's ATS score.
6a. **See how well each job really fits.** Keyword scores are a rough first pass. Every job scoring above 30% and below 100% (and not archived or applied for) is matched to your master CV in the background by Codex's strongest model at high reasoning: its card then shows a **master CV match** score, and **View opportunity** shows the strengths it found in your CV, the gaps, and a tailored resume ready to preview or download. New jobs are matched as they arrive, and all of them again after your master resume changes. The Jobs page shows progress; **Match now** starts it for jobs still waiting, for example after you edit your entries. Each job uses some of your plan's limits.
6b. **Check how the resume reads to an ATS.** Employers screen resumes with an applicant tracking system (ATS) such as Workday, Greenhouse, Lever, iCIMS, or Taleo, and their own rankings can't be seen. **View opportunity** therefore checks the two things that decide whether a resume comes up in them. **Parsing:** the job's resume PDF is read back the way an ATS parser reads it, to confirm your name, email, phone, section titles, and dates come through, with no unreadable characters, and to flag unusual section names or more than two pages. **Keywords:** select **Check ATS** and the AI lists the keywords a recruiter would search for, from the posting (technologies, tools, methods, the core job title, degrees, certifications), each with common variants. Each keyword is looked up literally in the PDF's text, because ATS searches are literal: "LLMs" and "large language models" count as variants, but "agentic AI" is not "AI agents". A keyword your resume writes only under another name (LLM where the posting says large language models) counts half, because searches use the posting's words. Keywords fall into these groups: in your resume; under another name; in your CV but not in this resume (for example when the XYZ wording left a term out), which **Add** puts in that resume's skills worded as in your CV; and not in your CV, which are real gaps and are never added. The score is the share of keywords found, with required ones counting double. It is an estimate of keyword coverage, not the employer's ranking. Every job is checked in the background, those without a tailored resume against your general resume, and job cards show only this ATS match: the resume as sent, and in red the match before similar terms, added keywords, and Goldmove skills, for example "ATS 78%, was 45%". Jobs are sorted by it. The **Full match** tab on **Discover jobs** lists every job whose resume has all of the posting's keywords (ATS 100%).
6c. **Goldmove: confirm keywords you have but never wrote down.** For every job that started above a 30% match and has a tailored resume, the job's ATS keywords are listed in the background and its resume is checked, after your CV's own keywords and similar terms are used. The **Goldmove** panel on **Discover jobs** gathers the keywords still missing across those jobs, most asked-for first, with how many jobs want each (hover to see which). Tick the ones you really have and select **Add to my skills** (**Select all** ticks everything): they join your skills and every tailored resume not yet sent, without matching or tailoring jobs again. **Not mine** dismisses keywords so they aren't offered again. Unticked keywords are never added, and job titles, degrees, and certifications are never offered, because a skills line can't claim them.
7. **Tailor and review your resume.** Open a job and choose **Suggest relevant skills** to review suggestions based on its description, then **Tailor my resume**. The resume uses confirmed content, ordered by relevance to the job. Preview or download the PDF before applying.
8. **Complete the application.** With optional Camoufox support installed, **Open application** opens a visible browser, fills recognized empty contact fields, and attaches the tailored PDF where it recognizes a resume upload. Complete login, unanswered questions, and any verification yourself, then review and submit on the site. Return to PutMeTo and mark the application submitted. Opening the browser alone never counts as a submitted application.
9. **Or let auto-apply send them.** See [Apply automatically](#apply-automatically).

With Codex, importing also builds your **master resume** in the background: Codex's strongest model at its deepest reasoning level (for example gpt-6-astra at ultra on a Pro plan) reorganizes the whole CV the way you wrote it, with projects kept inside the jobs they belong to, intro lines, bullets, bold phrases, your skill labels, and your own section titles. The wording is still copied line by line from the CV. When it finishes, usually within several minutes, it replaces the quick entries; entries you added yourself are kept, and if you edit CV entries while it runs, nothing is replaced. The panel on **My profile** shows its progress and offers **Rebuild**, which reuses your last CV without another upload. Deep reasoning uses more of your plan's limits than the quick import.

CV imports accept PDFs up to 10 MB and 30 pages, with up to 70,000 characters of text. Reading the pages needs Codex or a vision-capable model; a text-only model is refused before any CV content is sent. Imports never overwrite populated profile fields. Importing the same CV again refreshes the entries it contains (matched by type, title, organization, and start date) with the CV's wording instead of adding duplicates; entries you added yourself are kept. Duplicate imported skills are merged. Additional AI suggestions remain unconfirmed. Dismissed skills remain excluded from additional AI suggestions; importing the skill again from a CV restores it as confirmed. Editing an entry to generate new AI wording requires review again; changes to confirmed content require regenerating tailored resumes.

Resume bullets use **Google's XYZ format** (accomplished X, as measured by Y, by doing Z). Your master CV keeps your own wording; Codex's strongest model writes an XYZ version of each bullet as part of building the master resume, and every resume uses it: the preview, the PDF, and each job's tailored resume. A rewritten bullet is kept only if every number in it already appears in that entry, so no metric is invented; bullets without a measurable result are counted on the entry so you can add a real number. On **My profile**, each entry shows its XYZ wording with your original one click away, and **Edit XYZ** changes it. If you edit an entry's own wording, its XYZ version is set aside until **Write XYZ** writes a new one.

Resumes follow the layout of `template.docx`: a centered header, navy section titles (Summary, Experience, Projects, Publications, Skills, Education; your CV's own title is used when it is one an ATS recognizes, such as Professional Experience or Technical Skills, and otherwise the standard name), "Title | Organization" headings with italic right-aligned dates, and US Letter pages set in Calibri or its metric-compatible twin Carlito (bundled in `static/fonts`, SIL Open Font License). Description lines keep your CV's structure: lines starting with • are bullets, ◦ lines are nested bullets, a sentence before the bullets is shown as an italic introduction, a short phrase names a sub-project, and text in **double asterisks** is bold. Skills are grouped under your CV's own labels, or by type when the import recorded none. Lines that only repeat an entry's title and organization are left out. Parsing can vary across applicant tracking systems.

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

## Apply automatically

Auto-apply uses [browser-use](https://github.com/browser-use/browser-use) to fill in and **submit** applications for you, driven by Codex through your ChatGPT sign-in. It needs Codex as your AI provider, Google Chrome, and the application agent, installed in the activated project environment:

```bash
python -m pip install -r requirements-apply.txt
```

Restart PutMeTo after installing. Then, on **Applications**:

1. **Fill in Your answers for applications.** Work authorization, visa sponsorship, salary, start date, relocation, work arrangement, years of experience, your LinkedIn URL, and any other questions you expect. The agent uses only these answers, your profile, and the job's tailored resume, and never guesses. Leave a self-identification question (gender, race, veteran, disability) empty and the agent declines to answer it.
2. **Sign in once.** Select **Open application browser**, sign in to LinkedIn (needed for Easy Apply) and any job sites you use, then select **Done signing in**. The browser keeps its own profile in `data/apply-browser-profile`, separate from your everyday Chrome.
3. **Select Start.** Auto-apply applies to every job whose **master CV match is 70% or more** and that has a tailored resume, best match first, one at a time, with a pause of one to two minutes between applications. Before each application it runs the ATS check and adds the job's keywords that your CV already has to that job's resume. Under **Before each application** you can set a minimum keyword coverage; jobs below it are skipped. The default, 0, applies regardless. It uses LinkedIn **Easy Apply** when a listing offers it and otherwise follows the employer's Apply link. It submits without asking you, then marks the job applied. While it is on, jobs at 100% keyword match are matched to your master CV as well, because auto-apply relies only on that match.

When auto-apply can't finish an application, the application shows what it needs:

- **Needs your answer:** a required question your answers don't cover. Type the answer in the application's row; it is added to your answers and used for every later application. Every application waiting for an answer is tried again whenever your answers change.
- **Needs sign-in:** the site asked for a sign-in, a new account, an email or phone check, or a CAPTCHA. The agent never gets around these. Open the application browser from that row, finish the step, and select **Done signing in**. Waiting applications are tried again afterwards.
- **Didn't finish:** something else went wrong. It is tried again after an hour, up to three tries. If auto-apply was stopped or PutMeTo shut down part-way through an application, it is never retried by itself, because it may already have been sent. Check the site, then select **Try again** or **Mark submitted**.

Auto-apply turns on again after a restart if you left it on. **Stop** ends the current application after its current step. It never pays anything, enters ID or bank details, or opts into marketing. Each application step is one Codex request, so an application uses some of your plan's limits. Applying automatically to many jobs, especially through LinkedIn, can lead sites to restrict your account; you accept that risk by turning it on. Web pages are treated as untrusted, but no automated agent is perfect. Review your applications in the list and on the sites themselves.

## Search LinkedIn

After installing Camoufox, open **Discover jobs → Search LinkedIn**, or use **Search LinkedIn** on its source card. **United States** and **Europe** start selected, keywords start blank, and the remote-only option starts off. It requires neither a completed profile nor an AI connection. LinkedIn mixes unrelated jobs into its results, so a job is saved only when its title matches your keywords or one of your confirmed target positions; the result message says how many were skipped. With blank keywords and no confirmed positions, every title is kept.

Leave keywords empty to browse every title, keeping only jobs that match your target positions, or supply your own keywords. Keep either or both regions selected, or enter a custom location to override the selected regions. The app opens a visible Camoufox window, reads available job cards and detail pages, and saves results in your workspace. The default is **10 jobs per region**, with a maximum of **25 per region**. Each search reads a bounded batch of listings; it does not retrieve every job in the United States or Europe. Review any warnings: descriptions can be partial or unavailable when detail pages cannot be read.

If LinkedIn requests sign-in or verification, complete it yourself in the opened browser, then return to the unchanged search form and select **Search LinkedIn** again. The app does not solve or bypass those gates and does not retry automatically. Rate limits or unavailable pages may require trying again later. Any readable cards can still be returned with warnings.

LinkedIn uses a persistent local Camoufox profile at `data/linkedin-browser-profile/`, or under your configured `PUTMETO_DATA_DIR`. Cookies and browser storage survive normal window closure and application restarts, so you can reuse your sign-in. The app does not record your plaintext password. Browser cookies can grant account access, so treat this directory and its backups as sensitive. LinkedIn can still expire a session or request verification.

To keep finding jobs over time, select **Start** under **Keep searching LinkedIn** on the Jobs page. It searches your confirmed target positions in the background, page by page, in your preferred location (or the United States and Europe), and opens only new jobs that match a target position. It waits 20–30 seconds between page loads and takes a longer break every 8–15 pages, with no hourly or daily cap, and repeats every four hours to pick up new postings. Faster browsing finds jobs sooner but looks less like a person to LinkedIn. Only **Stop** ends it. If a page doesn't load, it tries again after a minute, waiting longer after each failure (up to 15 minutes); if the LinkedIn window is closed, it opens it again; if LinkedIn asks you to sign in or complete a security check, it waits without loading pages and continues by itself once you're through; and if LinkedIn limits requests, it waits about 40 minutes. The cause of each failure is written to `data/putmeto.log`. It runs while PutMeTo is open and stops when the app closes. Direct searches also wait a few seconds between pages.

To include LinkedIn in **Discover jobs** across selected sources, enable its source card. Its canonical source URL is `https://www.linkedin.com/jobs/search/`. This mode searches for your confirmed target positions in your preferred location (or the United States and Europe when none is set), with up to 10 jobs per region, and saves only jobs that match a target position. LinkedIn starts disabled, including when the source is added to an existing workspace.

### Standalone search script

The same search is available without starting the web application:

```bash
python linkedin_search.py --limit 10 --output artifacts/linkedin/jobs.json --keep-open --wait-for-login
```

Run it in the activated project environment after installing the browser requirements. With no keywords or location flags, it browses all job titles in the United States and Europe and saves those matching your confirmed target positions. An optional positional keyword phrase narrows titles, `--location "London"` replaces the two default regions with one location, or repeat `--region` up to five times to choose your own regions (for example, `--region Germany --region France`). `--remote` requests remote jobs.

`--wait-for-login` keeps the process waiting when manual sign-in or verification is needed; finish in the browser and press Enter in the terminal to retry. `--keep-open` leaves the browser open after the search until you close its window or press Ctrl+C. Without that flag, the browser closes when the script finishes. The persistent login profile is retained after normal closure.

The script exports JSON only; it does not save jobs to the application's SQLite workspace. `--output` writes the full result, including jobs, warnings, and any action-needed status, to the specified file; without it, the result is printed. The script defaults to 10 jobs per region and accepts limits from 1 to 25 per region.

## Job sources and integration limits

| Source | Configuration | Current behavior |
| --- | --- | --- |
| Remotive | Enable the supplied source. | Uses the public remote jobs API. Responses are cached for six hours. Remotive documents that public listings are delayed by 24 hours. Listings retain Remotive attribution and their source link. |
| Greenhouse | A company board URL, such as `https://job-boards.greenhouse.io/company`. Replace `company` with a real board token. | Reads that company's public job board and descriptions. Supports recognized global and EU board URLs; processes up to 2,000 jobs per source. |
| Lever | A company board URL, such as `https://jobs.lever.co/company`. | Reads that company's public postings; supports global and EU boards and requests up to 500 postings. |
| LinkedIn | Enable the supplied source, or use **Search LinkedIn**; Camoufox installed. | Discovery searches your target positions; **Search LinkedIn** uses your keywords. Either way, jobs unrelated to your keywords and target positions are skipped. Reads a batch of up to 10 jobs per region; direct searches allow up to 25 per region. The local browser profile retains login; sign-in/verification gates require your attention. |
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

The directory is created automatically under the project root. Remotive's temporary cache, the persistent `linkedin-browser-profile/` directory, and auto-apply's `apply-browser-profile/` also live in `data/`. Auto-apply writes each tailored resume to a private temporary file for the upload and deletes it afterwards. Uploaded CV files are read for extraction rather than retained as original file copies. API credentials and resume content in the SQLite database are **not encrypted at rest**; protect this directory and its backups, including the LinkedIn session cookies.

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

This checks publishable working files, staged contents, and files in reachable Git history for private file paths and recognizable secrets. It reports locations without printing matched values. Review the changes too: arbitrary personal information in prose cannot be identified reliably by a pattern scanner. [Publication guidance](publishing.md) covers the review and commit-email privacy.

## Development and verification

### LinkedIn tools for agents

Use the running app from another program with `linkedin_agent.py`. It exposes page inspection, reading and saving a job URL, local job lookup, named searches you can run later, and explicit filling of supported application fields for your review. It reuses the persistent LinkedIn browser profile.

```bash
python linkedin_agent.py tools
python linkedin_agent.py inspect
python linkedin_agent.py jobs --query "engineer"
python linkedin_agent.py save-search "US and Europe" --limit 10
```

See [the agent guide](linkedin-agent.md) for the Python client, CLI, API schemas, saved-search workflow, and form-filling examples. An agent runtime can call these tools; a language model is not required by this client.

### Automated checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Coverage includes PDF page reading and automatic confirmation of imports, evidence-based skill review, structured AI validation, draft review and rephrasing, confirmed-only exports, settings and credential handling, persistence, request-origin protection, job matching and discovery, regional LinkedIn searches, agent page inspection and form filling, saved searches, browser-session behavior, and application tracking. Tests use temporary databases, synthetic applicants and jobs, and mocked network/AI/browser responses; they do not submit real applications or prove every external website works. Optional browser checks are skipped unless explicitly enabled.

Synthetic LinkedIn DOM fixtures cover semantic job extraction on pages with generated CSS classes, rejection of work-mode links mistaken for job cards, and exclusion of unrelated marketing content. Live LinkedIn availability varies by region, session, page layout, and rate limits; automated fixture checks do not establish current live coverage.

For the optional desktop/mobile UI smoke test, install Playwright and have Google Chrome installed:

```bash
python -m pip install playwright
python tests/browser_smoke.py --artifacts artifacts/browser
```

This script starts an isolated local server with a temporary database and AI/browser stubs. It exercises the interface in real Chrome, including experience review, skills and positions, tailoring, resume preview, application tracking, and mobile layouts. It also checks LinkedIn search defaults, login handling, and source configuration. It does not access your existing workspace or external job sites. `--port` changes its server port; `PUTMETO_CHROME` can point to your Chrome executable. Screenshots go to the optional artifacts directory.

To verify that LinkedIn browser storage survives a restart using a temporary profile and a synthetic page:

```bash
PUTMETO_RUN_PERSISTENCE_TESTS=1 python -m pytest -q tests/test_linkedin.py -k retains_cookie_and_local_storage
```

This check closes and reopens Camoufox, then verifies that a test cookie and local storage survive the restart. It does not read your LinkedIn cookies or access LinkedIn.

After installing Camoufox and its browser binary, an additional live browser check is available on macOS or Linux:

```bash
PUTMETO_BROWSER_SMOKE=1 python -m pytest -q tests/test_browser_live.py
```

That check visits `example.com` and exercises filling a synthetic application form, PDF attachment, and browser cleanup. The form is supplied inside the browser; it does not send an application. This test is skipped in the regular suite unless the environment variable is set.

Implementation layout:

```text
backend/
  main.py       FastAPI routes, input validation, local request protection
  db.py         Transactional SQLite workspace
  ai.py         Ollama / compatible API calls and structured outputs
  cv_import.py  PDF CV pages to images for the AI to read
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

Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) while the server runs. [IMPLEMENTATION.md](../IMPLEMENTATION.md) describes the shared state and endpoint contract. The [original project requirements](original-requirements.md) are preserved separately.

## Troubleshooting

- **AI cannot connect:** Start the configured server, check its URL, confirm that the model is installed, and use **Test connection** in Settings. Do not start another `ollama serve` process if Ollama is already listening on that port.
- **AI returns invalid JSON or times out:** Try a model with better structured-output support or import a shorter CV. Failed imports do not partially confirm content. Manually entered experience is retained as a draft when enhancement fails.
- **Your AI model cannot read PDF pages:** Choose Codex or a vision-capable model (for Ollama, one that lists vision among its capabilities) in Settings.
- **No jobs appear:** Confirm target titles, enable at least one valid source, and try broader titles or location criteria. Read any per-source warning. A custom page must expose structured job data.
- **LinkedIn asks for sign-in or verification:** Complete it in the visible Camoufox window and submit the same search form again. For the standalone script, use `--wait-for-login` and press Enter when ready. Searches can also be limited by session expiry, site availability, or rate limits.
- **Browser cannot launch:** Install `requirements-browser.txt`, download the browser with `python -m camoufox fetch`, and restart the application.
- **Some form fields remain blank:** Fill them manually in the opened browser. Check the attached resume before submitting.
- **Port 8000 is already in use:** Stop the other server or set `PUTMETO_PORT` to a free local port.

