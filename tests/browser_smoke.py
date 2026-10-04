"""Optional real Chrome smoke test, with an isolated server and offline AI stubs.

Run: .venv/bin/python tests/browser_smoke.py
Requires: python -m pip install playwright, plus an installed Google Chrome.
Set PUTMETO_CHROME to its executable when Chrome is not in the usual macOS path.
No existing workspace data or external job sites are accessed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure_server(data_dir: Path):
    from backend import ai, db, jobs
    from backend.main import app
    from backend.network import validate_url_shape

    db.DATA_DIR = data_dir

    async def enhance_item(settings, item):
        return "Developed Python backend services and maintained SQL database queries."

    async def suggest(settings, state, kind):
        return ["Python", "SQL"] if kind == "skills" else ["Software engineer"]

    async def order_resume(settings, state, job, effort=None, timeout=None):
        return ai.ResumeOrder(
            item_ids=[item["id"] for item in state["items"] if item.get("confirmed")],
            skill_ids=[item["id"] for item in state["skills"] if item.get("confirmed")],
        )

    async def test_connection(settings):
        return None

    async def validate_without_dns(url):
        return validate_url_shape(url)

    async def browser_stub(session_id, url, profile, pdf):
        assert pdf.startswith(b"%PDF")
        return {"filled_fields": 2, "resume_attached": True}

    linkedin_calls = []

    async def linkedin_stub(keywords, location="", remote_only=False, limit=10):
        linkedin_calls.append((keywords, location, remote_only, limit))
        assert keywords == ""  # A blank search asks LinkedIn for every title; only unrelated results are skipped.
        assert location in {"United States", "Europe"}
        assert remote_only is False
        assert limit == 2
        search_url = "https://www.linkedin.com/jobs/search/?location=" + location.replace(" ", "%20")
        if len(linkedin_calls) == 1:
            return {"jobs": [], "requires_action": True, "message": "Complete sign-in in Camoufox, then retry.", "search_url": search_url, "warnings": []}
        number = 900000001 if location == "United States" else 900000003
        titles = ("Software Engineer, Payments", "Accountant") if location == "United States" else ("Backend Software Engineer", "Lab Technician")
        return {
            "jobs": [{"title": title, "company": "LinkedIn Fixture", "location": location, "url": f"https://www.linkedin.com/jobs/view/{number + index}/", "description": "Synthetic job description for UI verification."}
                     for index, title in enumerate(titles)],
            "requires_action": False, "message": "Search complete.", "search_url": search_url, "warnings": [],
        }

    from backend import linkedin_watch

    async def continuous_search_stub():
        # The real loop opens LinkedIn; the UI only needs a running search to show.
        linkedin_watch._update("searching", "Searching, 20-30 seconds between pages.")
        await asyncio.Event().wait()

    linkedin_watch._run = continuous_search_stub

    async def same_model(settings, level=None):
        return settings, None  # Never the real Codex in this check.

    async def suggest_roles(settings, state, effort=None, timeout=None):
        await asyncio.sleep(1.5)  # Long enough to see the progress line.
        return [ai.RoleSuggestion(title="Backend Engineer", family="Software Engineering", fit="strong", reason="Built Python services."),
                ai.RoleSuggestion(title="Engineering Manager", family="Leadership", fit="possible", reason="Led a small team.")]

    async def match_job(settings, state, job, effort=None, timeout=None):
        return ai.JobMatch(score=82, summary="A strong backend fit; payments work is not shown.",
                           strengths=["Built Python services and SQL queries (Software engineer)."], gaps=["Payments domain experience."])

    from backend import apply_agent, auto_apply

    async def apply_stub(job, applicant, resume_pdf, model, should_stop=None, on_step=None):
        # The real agent drives Chrome; here the Payments form asks one question the answers don't cover yet.
        assert resume_pdf.read_bytes().startswith(b"%PDF")
        await asyncio.sleep(0.5)
        question = "Years of professional Rust experience (required)"
        if "Payments" in job["title"] and not any(item["question"] == question for item in applicant["other_answers"]):
            return apply_agent.Outcome("needs_input", question, job["url"])
        return apply_agent.Outcome("submitted", "Reference APP-1042.", job["url"])

    apply_agent.apply = apply_stub
    auto_apply.blocker = lambda state: ""
    auto_apply.GAP = (0.5, 0.5)

    async def group_skills(settings, skills, existing, effort=None, timeout=None):
        return ai.SkillGroups(groups=[ai.SkillGroup(name="Programming Languages", skills=list(range(1, len(skills) + 1)))])

    async def ats_terms(settings, job, effort=None, timeout=None):
        return [ai.ATSTerm(term="Python", kind="skill", importance="required", variants=[]),
                ai.ATSTerm(term="SQL", kind="skill", importance="required", variants=[]),
                ai.ATSTerm(term="Databases", kind="skill", importance="required", variants=[]),
                ai.ATSTerm(term="Payments", kind="domain", importance="preferred", variants=["payment processing"])]

    async def term_bags(settings, state, effort=None, timeout=None):
        return [ai.TermBag(term="SQL", names=["Structured Query Language"], phrasings=[]),
                ai.TermBag(term="Python", names=[], phrasings=["Python programming"])]

    ai.term_bags = term_bags
    ai.group_skills = group_skills
    ai.ats_terms = ats_terms
    ai.strongest = same_model
    ai.suggest_roles = suggest_roles
    ai.match_job = match_job
    ai.enhance_item = enhance_item
    ai.suggest = suggest
    ai.order_resume = order_resume
    ai.test_connection = test_connection
    jobs.validate_public_url = validate_without_dns
    jobs.start_application = browser_stub
    jobs.search_linkedin = linkedin_stub
    return app


def payments_id(workspace: dict) -> str:
    return next(job["id"] for job in workspace["jobs"] if job["title"] == "Software Engineer, Payments")


def waiting_id(workspace: dict) -> str:
    return next(item["id"] for item in workspace["applications"] if item.get("status") == "needs_input")


def smoke(base_url: str, artifacts: Path | None):
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError as error:
        raise SystemExit("Browser smoke test is optional. Install playwright with: python -m pip install playwright") from error

    failures: list[str] = []
    routes = ("overview", "profile", "preferences", "sources", "jobs", "applications", "settings")
    chrome = os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    with sync_playwright() as playwright:
        options = {"headless": True}
        if Path(chrome).exists():
            options["executable_path"] = chrome
        else:
            options["channel"] = "chrome"
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce")
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base_url + "/") else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on("console", lambda message: failures.append("Console: " + message.text) if message.type == "error" else None)

        def state():
            response = context.request.get(base_url + "/api/state")
            assert response.ok, response.text()
            return response.json()

        def action(path, method, perform):
            with page.expect_response(lambda response: urlsplit(response.url).path == path and response.request.method == method) as pending:
                perform()
            response = pending.value
            assert response.ok, f"{method} {path}: {response.status} {response.text()}"
            # The write response precedes the UI's follow-up state refresh.
            expect(page.locator("button:disabled, input:disabled")).to_have_count(0)
            page.wait_for_load_state("networkidle")
            expect(page.locator(".toast.error")).to_have_count(0)
            return response.json()

        def navigate(route, mobile=False):
            if page.locator("#modal").evaluate("element => element.open"):
                page.get_by_role("button", name="Close dialog").click()
            if mobile:
                page.get_by_role("button", name="Toggle navigation").click()
            page.locator(f'.nav a[href="#{route}"]').click()
            expect(page.locator("#main h1")).to_be_visible()
            expect(page.locator(f'.nav a[href="#{route}"]')).to_have_class("nav-item active")

        try:
            page.goto(base_url, wait_until="networkidle")
            expect(page.get_by_role("heading", name="Your next chapter starts here.")).to_be_visible()
            for route in routes:
                navigate(route)
            navigate("profile")
            profile = page.locator("#profile-form")
            profile.get_by_label("Full name").fill("Ada Applicant")
            profile.get_by_label("Professional headline").fill("Software engineer")
            profile.get_by_label("Email address").fill("ada@example.com")
            profile.get_by_label("Location", exact=True).fill("Boston, MA")
            profile.get_by_label("Profile summary").fill("Backend engineer building useful software with Python and SQL.")
            action("/api/profile", "PUT", lambda: profile.get_by_role("button", name="Save details").click())
            assert state()["profile"]["name"] == "Ada Applicant"

            page.get_by_role("button", name="Add experience", exact=True).click()
            form = page.locator("#item-form")
            form.get_by_label("Title", exact=True).fill("Software engineer")
            form.get_by_label("Organization", exact=True).fill("Example Company")
            form.get_by_label("Start date").fill("2021")
            form.get_by_label("End date").fill("2024")
            form.get_by_label("What did you do?").fill("Built Python APIs and wrote SQL queries.")
            item = action("/api/items", "POST", lambda: form.get_by_role("button", name="Save & enhance").click())
            expect(page.locator("#modal")).not_to_be_visible()
            expect(page.locator(".item-card")).to_contain_text("Needs review")
            page.get_by_role("button", name="Review & confirm").click()
            review = page.locator("#review-form")
            review.get_by_label("Reviewed description").fill("Built reliable Python services and SQL database queries.")
            review.get_by_role("checkbox").check()
            action(f'/api/items/{item["id"]}', "PUT", lambda: review.get_by_role("button", name="Confirm experience").click())
            expect(page.locator(".item-card .badge")).to_have_text("Confirmed")
            # Resumes use the Google XYZ wording; the card shows it with the original one click away.
            xyz_wording = "• Developed backend services by building Python APIs and SQL queries."
            assert page.evaluate("""([id, text]) => fetch(`/api/items/${id}/xyz`, {method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({xyz: text})}).then(response => response.status)""", [item["id"], xyz_wording]) == 200
            page.reload()
            card = page.locator(".item-card")
            expect(card.locator(".item-description").first).to_have_text(xyz_wording)
            expect(card.locator(".xyz-note summary")).to_contain_text("Google XYZ wording. 1 bullet has no number yet")
            card.locator(".xyz-note summary").click()
            expect(card.locator(".xyz-note .item-description")).to_be_visible()
            card.get_by_role("button", name="Edit XYZ").click()
            expect(page.locator("#xyz-form textarea")).to_have_value(xyz_wording)
            page.locator('#modal [data-action="close-modal"]').click()
            assert state()["items"][0]["enhanced"] == "Built reliable Python services and SQL database queries."

            page.get_by_label("Add a technical skill").fill("Python")
            action("/api/skills", "PUT", lambda: page.get_by_role("button", name="Add skill", exact=True).click())
            checkbox = page.get_by_role("checkbox", name="Confirm Python", exact=True)
            expect(checkbox).to_be_checked()
            action("/api/skills", "PUT", checkbox.uncheck)
            assert state()["skills"][0]["confirmed"] is False
            action("/api/skills", "PUT", checkbox.check)
            assert state()["skills"][0]["confirmed"] is True

            navigate("preferences")
            page.get_by_label("Only show remote opportunities").check()
            action("/api/preferences", "PUT", lambda: page.get_by_role("button", name="Save preferences").click())
            page.get_by_label("Add a job title").fill("Software engineer")
            action("/api/positions", "PUT", lambda: page.get_by_role("button", name="Add role", exact=True).click())
            checkbox = page.get_by_role("checkbox", name="Confirm Software engineer", exact=True)
            expect(checkbox).to_be_checked()
            action("/api/positions", "PUT", checkbox.uncheck)
            assert state()["positions"][0]["confirmed"] is False
            action("/api/positions", "PUT", checkbox.check)
            action("/api/master-resume/roles", "POST", lambda: page.get_by_role("button", name="Suggest roles", exact=True).click())
            expect(page.locator("#roles-progress")).to_contain_text("Listing every job title")
            expect(page.locator(".role-group h3")).to_have_text(["Your roles", "Software Engineering", "Leadership"], timeout=15000)
            expect(page.locator(".chip.possible")).to_have_attribute("title", "Possible fit: Led a small team.")
            expect(page.get_by_role("checkbox", name="Confirm Software engineer", exact=True)).to_be_checked()
            expect(page.get_by_role("checkbox", name="Confirm Backend Engineer", exact=True)).not_to_be_checked()
            if artifacts:
                page.locator("section.panel", has=page.locator("#roles-progress")).screenshot(path=str(artifacts / "roles.png"))

            navigate("sources")
            remotive = page.get_by_role("checkbox", name="Enable Remotive", exact=True)
            action("/api/sources", "PUT", remotive.uncheck)
            action("/api/sources", "PUT", remotive.check)

            navigate("jobs")
            page.get_by_role("button", name="Add a job", exact=True).click()
            form = page.locator("#job-form")
            form.get_by_label("Job title", exact=True).fill("Python software engineer")
            form.get_by_label("Company", exact=True).fill("Example Company")
            form.get_by_label("Location", exact=True).fill("Remote")
            form.get_by_label("Job URL", exact=True).fill("https://jobs.example.com/python")
            form.get_by_label("Full job description").fill("Build Python backend services using SQL. Remote opportunity.")
            job = action("/api/jobs", "POST", lambda: form.get_by_role("button", name="Save opportunity").click())
            expect(page.locator(".job-card")).to_contain_text("Python software engineer")
            page.get_by_role("button", name="View opportunity").click()
            action(f'/api/jobs/{job["id"]}/tailor', "POST", lambda: page.get_by_role("button", name="Tailor my resume").click())
            expect(page.locator("#modal")).to_contain_text("Your tailored resume is ready.")
            with context.expect_page() as popup:
                page.get_by_role("link", name="Preview resume", exact=True).click()
            preview = popup.value
            preview.wait_for_load_state("networkidle")
            expect(preview.get_by_role("heading", name="Ada Applicant", exact=True)).to_be_visible()
            # The tailored resume uses the Google XYZ wording, not the entry's own.
            expect(preview.locator("body")).to_contain_text("Developed backend services by building Python APIs and SQL queries.")
            expect(preview.locator("body")).not_to_contain_text("Built reliable Python services and SQL database queries.")
            assert preview.url.endswith(f'/api/jobs/{job["id"]}/resume')
            preview.close()
            assert state()["jobs"][0]["status"] == "prepared"
            action(f'/api/jobs/{job["id"]}/apply', "POST", lambda: page.get_by_role("button", name="Open application").click())
            navigate("applications")
            page.get_by_role("button", name="Mark submitted").click()
            application = state()["applications"][0]
            action(f'/api/applications/{application["id"]}/confirm', "POST", lambda: page.get_by_role("button", name="Yes, I submitted it").click())
            expect(page.locator(".application-row")).to_contain_text("Submitted")

            navigate("sources")
            page.get_by_role("button", name="Add a source", exact=True).click()
            source_form = page.locator("#source-form")
            source_form.locator('[name="kind"]').select_option("linkedin")
            expect(source_form.locator('[name="url"]')).to_have_value("https://www.linkedin.com/jobs/search/")
            page.get_by_role("button", name="Close dialog").click()

            navigate("jobs")
            page.get_by_role("button", name="Full match", exact=True).click()
            page.get_by_role("button", name="Search LinkedIn", exact=True).click()
            search_form = page.locator("#linkedin-form")
            expect(search_form.locator('[name="keywords"]')).to_have_value("")
            expect(search_form.locator('[name="location"]')).to_have_value("")
            expect(search_form.get_by_role("checkbox", name="United States", exact=True)).to_be_checked()
            expect(search_form.get_by_role("checkbox", name="Europe", exact=True)).to_be_checked()
            expect(search_form.locator('[name="remote_only"]')).not_to_be_checked()
            search_form.locator('[name="limit"]').fill("2")
            first = action("/api/linkedin/search", "POST", lambda: search_form.get_by_role("button", name="Search LinkedIn", exact=True).click())
            assert first["requires_action"] is True
            expect(search_form).to_be_visible()
            expect(search_form.locator('[name="keywords"]')).to_have_value("")
            expect(search_form.locator('[name="limit"]')).to_have_value("2")
            expect(search_form.get_by_role("checkbox", name="Europe", exact=True)).to_be_checked()
            expect(search_form.locator("#linkedin-status")).to_contain_text("Your search details have been kept")
            second = action("/api/linkedin/search", "POST", lambda: search_form.get_by_role("button", name="Search LinkedIn", exact=True).click())
            assert second["found"] == second["count"] == 2
            assert second["skipped"] == 2, "Titles unrelated to the confirmed role are not saved."
            expect(page.locator("#modal")).not_to_be_visible()
            expect(page.locator('.tab[data-filter="all"]')).to_have_class("tab active")
            expect(page.locator(".job-card").filter(has_text="Software Engineer, Payments")).to_be_visible()
            expect(page.locator(".job-card").filter(has_text="Backend Software Engineer")).to_be_visible()
            expect(page.locator(".job-card").filter(has_text="Lab Technician")).to_have_count(0)
            expect(page.locator(".job-card").filter(has_text="Accountant")).to_have_count(0)

            watch = page.locator("#linkedin-watch")
            expect(watch).to_contain_text("Keep searching LinkedIn")
            expect(watch).to_contain_text("20-30 seconds between pages")
            action("/api/linkedin/continuous/start", "POST", lambda: watch.get_by_role("button", name="Start", exact=True).click())
            expect(watch.get_by_role("button", name="Stop", exact=True)).to_be_visible()
            expect(watch).to_contain_text("pages this hour")
            expect(watch).not_to_contain_text("/30")
            if artifacts:
                page.screenshot(path=str(artifacts / "jobs-continuous-search.png"), full_page=True)
            action("/api/linkedin/continuous/stop", "POST", lambda: watch.get_by_role("button", name="Stop", exact=True).click())
            expect(watch.get_by_role("button", name="Start", exact=True)).to_be_visible()

            # LinkedIn jobs scoring between 30% and 100% are matched to the master CV and get a tailored resume;
            # cards show just the ATS keyword match once the background check has read each resume.
            payments = page.locator(".job-card").filter(has_text="Software Engineer, Payments")
            for _ in range(80):
                listed = next(job for job in state()["jobs"] if job["title"] == "Software Engineer, Payments")
                if listed.get("resume") and listed["ats_score"] is not None:
                    break
                page.wait_for_timeout(250)
            page.reload()
            expect(payments.locator(".match")).to_contain_text(f"ATS {listed['ats_score']}%")
            expect(payments).not_to_contain_text("master CV match")
            payments.get_by_role("button", name="View opportunity").click()
            expect(page.locator("#modal")).to_contain_text("How you match")
            expect(page.locator(".match-list").first).to_contain_text("Built Python services and SQL queries")
            expect(page.locator(".match-list").last).to_contain_text("Payments domain experience.")
            expect(page.locator("#modal .actions").first).to_contain_text(f"ATS {listed['ats_score']}%")
            expect(page.locator("#modal")).not_to_contain_text("keyword match:")
            expect(page.locator("#modal").get_by_role("link", name="Download PDF")).to_be_visible()
            if artifacts:
                page.screenshot(path=str(artifacts / "job-match.png"), full_page=True)

            # The ATS check reads the PDF back, finds the posting's keywords, and adds only those your CV has.
            check = page.locator("#ats-check")
            expect(check).not_to_contain_text("Reading your resume")
            if check.get_by_role("button", name="Check ATS").count():  # Goldmove may already have listed the keywords.
                action(f"/api/jobs/{payments_id(state())}/ats", "POST", lambda: check.get_by_role("button", name="Check ATS").click())
            expect(check).to_contain_text("keyword match")
            expect(check.locator(".ats-term.present")).to_have_count(2)
            expect(check.locator(".ats-term.addable")).to_have_text(["Databases*"])
            expect(check.locator(".ats-term.missing")).to_have_text(["Payments"])
            expect(check.locator(".ats-checks li.fail")).to_have_count(0)
            if artifacts:
                check.screenshot(path=str(artifacts / "ats-check.png"))
            action(f"/api/jobs/{payments_id(state())}/ats/add", "POST", lambda: check.get_by_role("button", name="Add 1").click())
            expect(check.locator(".ats-term.addable")).to_have_count(0)
            expect(check.locator(".ats-term.missing")).to_have_text(["Payments"])
            assert "database" in next(job for job in state()["jobs"] if job["title"] == "Software Engineer, Payments")["resume"]["skills"]
            page.locator('#modal [data-action="close-modal"]').click()

            # Goldmove offers the keywords still missing, and adds only the ones ticked.
            gold = page.locator("#goldmove")
            expect(gold).to_contain_text("Goldmove", timeout=20000)
            expect(gold.locator(".gold-term").filter(has_text="Payments")).to_be_visible(timeout=20000)
            assert "Payments" not in [skill["name"] for skill in state()["skills"]], "Nothing is added by itself."
            if artifacts:
                gold.screenshot(path=str(artifacts / "goldmove.png"))
            gold.locator(".gold-term").filter(has_text="Payments").locator("input").check()
            action("/api/goldmove/confirm", "POST", lambda: gold.get_by_role("button", name="Add to my skills").click())
            expect(page.locator(".toast").last).to_contain_text("Added 1 skill you have")
            skill = next(skill for skill in state()["skills"] if skill["name"] == "Payments")
            assert skill["origin"] == "goldmove" and skill["confirmed"]
            assert "Payments" in next(job for job in state()["jobs"] if job["title"] == "Software Engineer, Payments")["resume"]["skills"]
            expect(gold.locator(".gold-term").filter(has_text="Payments")).to_have_count(0)

            # With every keyword now in its resume, the job is a full match and shows where it started, in red.
            for _ in range(40):
                if next(job for job in state()["jobs"] if job["title"] == "Software Engineer, Payments")["ats_score"] == 100:
                    break
                page.wait_for_timeout(250)
            page.reload()
            page.locator('.tab[data-filter="full"]').click()
            full = page.locator(".job-card").filter(has_text="Software Engineer, Payments")
            listed = next(job for job in state()["jobs"] if job["title"] == "Software Engineer, Payments")
            assert listed["ats_before"] < 100
            expect(full.locator(".match")).to_contain_text("ATS 100%")
            expect(full.locator(".match-before")).to_have_text(f"was {listed['ats_before']}%")
            assert page.locator(".match-before").evaluate("element => getComputedStyle(element).color") == "rgb(176, 65, 47)"
            expect(page.locator(".job-card").filter(has_text="Backend Software Engineer")).to_have_count(0)
            if artifacts:
                page.screenshot(path=str(artifacts / "full-match.png"), full_page=True)
            page.locator('.tab[data-filter="all"]').click()
            payments = page.locator(".job-card").filter(has_text="Software Engineer, Payments")
            payments.get_by_role("button", name="View opportunity").click()
            page.locator('#modal [data-action="close-modal"]').click()

            # Auto-apply sends well-matched jobs and stops to ask when a form needs an answer it doesn't have.
            navigate("applications")
            panel = page.locator("#auto-apply")
            expect(panel).to_contain_text("master-CV match of 70% or more")
            answers = page.locator("#answers-form")
            answers.get_by_label("Visa sponsorship").fill("No")
            action("/api/auto-apply/answers", "PUT", lambda: answers.get_by_role("button", name="Save answers").click())
            assert state()["application_answers"]["sponsorship"] == "No"
            action("/api/auto-apply/start", "POST", lambda: panel.get_by_role("button", name="Start", exact=True).click())
            expect(panel.get_by_role("button", name="Stop", exact=True)).to_be_visible()
            waiting = page.locator(".application-row").filter(has_text="Software Engineer, Payments")
            expect(waiting).to_contain_text("Needs your answer", timeout=20000)
            expect(waiting).to_contain_text("Years of professional Rust experience (required)")
            expect(page.locator(".application-row").filter(has_text="Backend Software Engineer")).to_contain_text("Submitted", timeout=20000)
            if artifacts:
                page.screenshot(path=str(artifacts / "auto-apply.png"), full_page=True)
            waiting.get_by_placeholder("Your answer, used for every application").fill("2 years")
            action(f'/api/auto-apply/applications/{waiting_id(state())}/answer', "POST", lambda: waiting.get_by_role("button", name="Save answer").click())
            expect(page.locator('#other-answers [name="other_answer"]')).to_have_value("2 years")
            expect(page.locator(".application-row").filter(has_text="Software Engineer, Payments")).to_contain_text("Submitted", timeout=20000)
            assert {job["status"] for job in state()["jobs"] if job["title"] in {"Software Engineer, Payments", "Backend Software Engineer"}} == {"applied"}
            action("/api/auto-apply/stop", "POST", lambda: panel.get_by_role("button", name="Stop", exact=True).click())
            expect(panel.get_by_role("button", name="Start", exact=True)).to_be_visible()

            if artifacts:
                navigate("overview")
                page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            for route in routes:
                navigate(route, mobile=True)
                dimensions = page.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
                assert dimensions["scroll"] <= dimensions["width"] + 1, f"Horizontal overflow on mobile {route}: {dimensions}"
                expect(page.locator("#main h1")).to_be_visible()
            if artifacts:
                navigate("overview", mobile=True)
                page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
            assert not failures, "JavaScript errors: " + json.dumps(failures)
            assert not page.locator(".toast.error").count()
            print("PASS: 7 routes, full resume/application workflow, broad US+Europe LinkedIn search without unrelated jobs, continuous search start/stop, grouped role suggestions, Google XYZ resume wording, master CV job matching with tailored resumes, ATS check with keywords added from the CV, Goldmove confirmation, Full match tab, auto-apply with a question answered and retried, manual-login retry, source URL autofill, mobile layout; no JavaScript errors.")
        except Exception:
            if artifacts:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
            print("Browser errors:", failures, file=sys.stderr)
            raise
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="putmeto-browser-") as temporary:
        app = configure_server(Path(temporary))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen()
        actual_port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{actual_port}"
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    raise RuntimeError("The isolated smoke test server did not start.")
                time.sleep(0.05)
            with urlopen(base_url + "/api/health", timeout=5) as health:
                assert health.status == 200
            smoke(base_url, args.artifacts)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()


if __name__ == "__main__":
    main()
