"""Optional real Chrome smoke test, with an isolated server and offline AI stubs.

Run: .venv/bin/python tests/browser_smoke.py
Requires: python -m pip install playwright, plus an installed Google Chrome.
Set PUTMETO_CHROME to its executable when Chrome is not in the usual macOS path.
No existing workspace data or external job sites are accessed.
"""

from __future__ import annotations

import argparse
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

    async def order_resume(settings, state, job):
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
        assert keywords == ""  # Saved software engineering preferences must not narrow this search.
        assert location in {"United States", "Europe"}
        assert remote_only is False
        assert limit == 2
        search_url = "https://www.linkedin.com/jobs/search/?location=" + location.replace(" ", "%20")
        if len(linkedin_calls) == 1:
            return {"jobs": [], "requires_action": True, "message": "Complete sign-in in Camoufox, then retry.", "search_url": search_url, "warnings": []}
        number = "900000001" if location == "United States" else "900000002"
        return {
            "jobs": [{"title": "Lab Technician" if location == "Europe" else "Accountant", "company": "LinkedIn Fixture", "location": location, "url": f"https://www.linkedin.com/jobs/view/{number}/", "description": "Synthetic job description for UI verification."}],
            "requires_action": False, "message": "Search complete.", "search_url": search_url, "warnings": [],
        }

    ai.enhance_item = enhance_item
    ai.suggest = suggest
    ai.order_resume = order_resume
    ai.test_connection = test_connection
    jobs.validate_public_url = validate_without_dns
    jobs.start_application = browser_stub
    jobs.search_linkedin = linkedin_stub
    return app


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
            expect(preview.locator("body")).to_contain_text("Built reliable Python services and SQL database queries.")
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
            page.get_by_role("button", name="Saved", exact=True).click()
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
            expect(page.locator("#modal")).not_to_be_visible()
            expect(page.locator('.tab[data-filter="all"]')).to_have_class("tab active")
            expect(page.locator(".job-card").filter(has_text="Lab Technician")).to_be_visible()
            expect(page.locator(".job-card").filter(has_text="Accountant")).to_be_visible()

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
            print("PASS: 7 routes, full resume/application workflow, broad US+Europe LinkedIn search, manual-login retry, source URL autofill, mobile layout; no JavaScript errors.")
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
