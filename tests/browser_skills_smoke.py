"""Offline Chrome smoke test for compact skills, CV recovery, and job suggestions.

Run: .venv/bin/python tests/browser_skills_smoke.py --artifacts artifacts/skills-browser
Every request is intercepted. No real CV, workspace data, AI, or job site is used.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db import default_state


def smoke(artifacts: Path | None = None) -> None:
    from playwright.sync_api import expect, sync_playwright

    workspace = default_state()
    workspace["settings"].pop("api_key", None)
    workspace["settings"]["api_key_set"] = False
    workspace["items"] = [{
        "id": "existing-experience", "kind": "experience", "title": "Data engineer",
        "organization": "Fixture Company", "original": "Orchestrated scheduled data pipelines using Apache Airflow.",
        "enhanced": "Orchestrated scheduled data pipelines using Apache Airflow.", "confirmed": True,
    }]
    workspace["skills"] = [
        {"id": "legacy-python", "name": "Python", "confirmed": True},
        {"id": "legacy-r", "name": "R", "confirmed": False},
        {"id": "workflow", "name": "Workflow orchestration", "kind": "technique", "support": "supported", "origin": "resume",
         "confirmed": False, "evidence": "Orchestrated scheduled data pipelines using Apache Airflow.",
         "rationale": "A reusable data engineering technique demonstrated in your project.", "aliases": ["Workflow-orchestration"]},
        {"id": "airflow", "name": "Apache Airflow", "kind": "tool", "support": "supported", "origin": "resume",
         "confirmed": False, "evidence": "Orchestrated scheduled data pipelines using Apache Airflow."},
        {"id": "sql", "name": "SQL querying", "kind": "technique", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": "Review this option if you have queried relational data."},
        {"id": "postgres", "name": "PostgreSQL", "kind": "database", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": "A database option to confirm if you have used it."},
        {"id": "vectors", "name": "Vector databases", "kind": "database", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": '<img src=x onerror="window.bad=true"> Review your actual experience.'},
    ]
    workspace["jobs"] = [{"id": "fixture-job", "title": "Data engineer", "company": "Fixture Company",
                           "location": "Europe", "status": "saved", "match_score": 0,
                           "description": "Work on data pipelines and workflow orchestration.", "source": "Fixture",
                           "url": "https://example.com/jobs/data"}]
    original_items = deepcopy(workspace["items"])
    writes, imports, suggestions, errors = [], [], [], []
    pending_imports = []
    origin = "http://putmeto.test"
    job_attempts = 0
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)

    def route_request(route):
        nonlocal job_attempts
        request = route.request
        path = urlsplit(request.url).path
        if not request.url.startswith(origin + "/"):
            errors.append(f"Unexpected external request: {request.url}")
            route.abort()
        elif path == "/api/state":
            route.fulfill(json=workspace)
        elif path == "/api/job-matching":
            route.fulfill(json={"state": "idle", "message": "", "done": 0, "total": 0, "model": "", "effort": "", "running": False, "waiting": 0})
        elif path == "/api/master-resume":
            route.fulfill(json={"state": "idle", "message": "", "model": "", "effort": "", "started_at": "", "running": False, "has_cv": False, "built": None})
        elif path == "/api/linkedin/continuous":
            route.fulfill(json={"running": False, "state": "stopped", "message": "Not running.", "found": 0, "skipped": 0, "next_at": "", "pages_last_hour": 0, "pages_today": 0})
        elif path.startswith("/api/jobs/") and path.endswith("/ats"):
            route.fulfill(json={"ready": False, "message": ""})
        elif path == "/api/goldmove":
            route.fulfill(json={"state": "idle", "message": "", "done": 0, "total": 0, "running": False, "waiting": 0,
                                "eligible": 0, "checked": 0, "dismissed": 0, "candidates": []})
        elif path == "/api/skill-groups":
            route.fulfill(json={"state": "idle", "message": "", "model": "", "effort": "", "running": False, "waiting": 0})
        elif path == "/api/skill-groups/regroup" and request.method == "POST":
            # The real grouping asks the AI; here each skill gets the group a model would choose.
            contexts = {"Python": "Programming Languages", "R": "Programming Languages", "PostgreSQL": "Databases",
                        "Vector databases": "Databases", "SQL querying": "Databases"}
            for item in workspace["skills"]:
                item["group"] = contexts.get(item["name"], "Data Engineering")
            workspace["skill_group_order"] = ["Programming Languages", "Data Engineering", "Databases"]
            route.fulfill(json={"state": "done", "message": "", "model": "", "effort": "", "running": False, "waiting": 0})
        elif path == "/api/skills" and request.method == "PUT":
            payload = request.post_data_json
            writes.append(deepcopy(payload))
            workspace["skills"] = [{"id": item.get("id", f"manual-{index}"), **item}
                                   for index, item in enumerate(payload["items"])]
            route.fulfill(json={"items": workspace["skills"]})
        elif path == "/api/import/skills":
            imports.append(request)
            pending_imports.append(route)
        elif path == "/api/suggestions/skills":
            payload = request.post_data_json if request.post_data else {}
            suggestions.append(payload)
            if payload.get("job_id"):
                job_attempts += 1
                if job_attempts == 1:
                    route.fulfill(status=503, json={"detail": "Skills suggestions are temporarily unavailable. Retry shortly."})
                    return
                workspace["skills"].append({"id": "data-quality", "name": "Data quality checks",
                                             "kind": "technique", "support": "related", "origin": "suggestion",
                                             "confirmed": False, "rationale": "Relevant to the saved Data engineer opportunity."})
            route.fulfill(json={"count": 1, "message": "Skill suggestions are ready for review."})
        elif path.startswith("/api/"):
            errors.append(f"Unexpected API request: {request.method} {path}")
            route.fulfill(status=404, json={"detail": "Unknown fixture endpoint"})
        else:
            filename = ROOT / "static" / (path.rsplit("/", 1)[-1] or "index.html")
            if filename.is_file():
                route.fulfill(body=filename.read_bytes(), content_type=mimetypes.guess_type(filename.name)[0] or "application/octet-stream")
            else:
                route.fulfill(status=404)

    chrome = Path(os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {"channel": "chrome"}))
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        skills = page.locator("#technical-skills")

        def card(skill_id: str):
            return skills.locator(f'[data-skill-id="{skill_id}"]')

        def navigate(name: str):
            page.locator(f'.nav a[href="#{name}"]').click()
            expect(page.locator(f'.nav a[href="#{name}"]')).to_have_class("nav-item active")

        def assert_compact_skills():
            bounds = skills.locator("[data-skill-id]").evaluate_all("""elements => elements.map(element => {
                const rect = element.getBoundingClientRect();
                return {top: rect.top, bottom: rect.bottom, height: rect.height};
            })""")
            assert bounds and all(bound["height"] <= 48 for bound in bounds), "Skills should be small chips"
            assert len({round(bound["top"]) for bound in bounds}) < len(bounds), "Skills should share wrapping rows"
            assert max(bound["bottom"] for bound in bounds) - min(bound["top"] for bound in bounds) <= 160, "Skill list is too tall"

        try:
            page.goto(origin + "/#profile")
            expect(skills.locator("[data-skill-id]")).to_have_count(7)
            expect(page.get_by_role("checkbox", name="Confirm Python", exact=True)).to_be_checked()
            expect(page.get_by_role("checkbox", name="Confirm R", exact=True)).not_to_be_checked()
            for skill in workspace["skills"]:
                expect(card(skill["id"])).to_contain_text(skill["name"])
                for field in ("evidence", "rationale"):
                    if skill.get(field):
                        expect(skills).not_to_contain_text(skill[field])
                for alias in skill.get("aliases", []):
                    expect(skills).not_to_contain_text(alias)
            expect(skills.locator("[data-skill-group]")).to_have_count(0)
            for metadata_label in ("Technique", "Listed in your CV", "Tool", "Database", "Explore and confirm", "Supporting context", "Related experience", "Also called:"):
                expect(skills).not_to_contain_text(metadata_label)
            assert_compact_skills()
            assert skills.locator("img").count() == 0
            assert page.evaluate("window.bad") is None

            page.get_by_role("checkbox", name="Confirm Workflow orchestration", exact=True).check()
            expect(page.get_by_role("checkbox", name="Confirm Workflow orchestration", exact=True)).to_be_checked()
            expect(page.get_by_role("button", name="Remove Workflow orchestration", exact=True)).to_be_visible()
            saved = next(item for item in writes[-1]["items"] if item["id"] == "workflow")
            assert saved["confirmed"] is True and saved["evidence"] == "Orchestrated scheduled data pipelines using Apache Airflow."
            assert saved["aliases"] == ["Workflow-orchestration"]
            assert saved["origin"] == "resume" and saved["support"] == "supported" and saved["kind"] == "technique"
            assert saved["rationale"] == "A reusable data engineering technique demonstrated in your project."
            page.get_by_role("checkbox", name="Confirm Workflow orchestration", exact=True).uncheck()
            expect(page.get_by_role("checkbox", name="Confirm Workflow orchestration", exact=True)).not_to_be_checked()
            expect(page.get_by_role("button", name="Dismiss Workflow orchestration", exact=True)).to_be_visible()
            assert next(item for item in writes[-1]["items"] if item["id"] == "workflow")["confirmed"] is False
            page.get_by_role("checkbox", name="Confirm PostgreSQL", exact=True).check()
            expect(page.get_by_role("checkbox", name="Confirm PostgreSQL", exact=True)).to_be_checked()
            assert next(item for item in writes[-1]["items"] if item["id"] == "postgres")["support"] == "related"

            page.get_by_role("button", name="Dismiss SQL querying", exact=True).click()
            expect(card("sql")).to_have_count(0)
            assert all(item["id"] != "sql" for item in writes[-1]["items"])
            page.get_by_label("Add a technical skill").fill("Docker")
            page.get_by_role("button", name="Add skill", exact=True).click()
            expect(page.get_by_role("checkbox", name="Confirm Docker", exact=True)).to_be_checked()
            page.get_by_role("button", name="Remove Docker", exact=True).click()
            expect(page.get_by_role("checkbox", name="Confirm Docker", exact=True)).to_have_count(0)
            assert all(item["name"] != "Docker" for item in writes[-1]["items"])
            page.get_by_role("button", name="Suggest skills", exact=True).click()
            expect(page.get_by_role("button", name="Suggest skills", exact=True)).to_be_enabled()
            assert suggestions[-1] == {}

            page.get_by_role("button", name="Import skills from CV", exact=True).click()
            form = page.locator("#import-form")
            expect(form).to_have_attribute("data-mode", "skills")
            expect(form).to_contain_text("adds confirmed skills and keeps your existing experience entries")
            expect(page.locator("#modal")).to_contain_text("Extracted skills are confirmed automatically")
            picker = form.locator("input[type=file]")
            picker.set_input_files({"name": "fixture.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\nSynthetic skills fixture"})
            submit = form.locator("button[type=submit]")
            with page.expect_request(lambda request: urlsplit(request.url).path == "/api/import/skills"):
                submit.click()
            expect(form.locator("#import-status")).to_contain_text("Extracting technical skills")
            expect(picker).to_be_disabled()
            pending_imports.pop().fulfill(status=503, json={"detail": "Please retry skills extraction."})
            expect(form.locator("#import-error")).to_contain_text("Please retry skills extraction.")
            expect(form.locator("#import-error")).to_be_visible()
            expect(submit).to_be_enabled()
            picker.set_input_files({"name": "fixture-retry.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\nSynthetic skills retry"})
            with page.expect_request(lambda request: urlsplit(request.url).path == "/api/import/skills"):
                submit.click()
            expect(submit).to_be_disabled()
            assert b'filename="fixture-retry.pdf"' in imports[-1].post_data_buffer
            workspace["skills"].append({"id": "indexing", "name": "Vector indexing", "kind": "technique", "support": "supported", "origin": "resume", "confirmed": True, "evidence": "Skills: vector indexing."})
            pending_imports.pop().fulfill(json={"count": 1, "skill_count": 1, "message": "CV skills imported and confirmed."})
            expect(page.locator("#modal")).not_to_be_visible()
            expect(card("indexing")).to_contain_text("Vector indexing")
            expect(card("indexing")).not_to_contain_text("Skills: vector indexing.")
            expect(page.get_by_role("checkbox", name="Confirm Vector indexing", exact=True)).to_be_checked()
            page.get_by_role("checkbox", name="Confirm Vector indexing", exact=True).uncheck()
            expect(page.get_by_role("checkbox", name="Confirm Vector indexing", exact=True)).not_to_be_checked()
            assert next(item for item in writes[-1]["items"] if item["id"] == "indexing")["confirmed"] is False
            assert workspace["items"] == original_items
            assert len(imports) == 2

            navigate("jobs")
            page.get_by_role("button", name="View opportunity", exact=True).click()
            page.get_by_role("button", name="Suggest relevant skills", exact=True).click()
            expect(page.locator("#job-skills-status")).to_be_visible()
            expect(page.locator("#job-skills-status")).to_contain_text("Retry shortly")
            page.get_by_role("button", name="Suggest relevant skills", exact=True).click()
            expect(page.locator("#modal")).not_to_be_visible()
            expect(card("data-quality")).to_contain_text("Data quality checks")
            expect(skills).not_to_contain_text("Relevant to the saved Data engineer opportunity")
            expect(page.get_by_role("checkbox", name="Confirm Data quality checks", exact=True)).not_to_be_checked()
            assert suggestions[-1] == {"job_id": "fixture-job"}
            assert page.url.endswith("#profile?skills")

            while page.get_by_role("button", name="Dismiss notification", exact=True).count():
                page.get_by_role("button", name="Dismiss notification", exact=True).first.click()
            assert_compact_skills()
            if artifacts:
                skills.screenshot(path=str(artifacts / "skills-desktop.png"))

            # Skills are shown in context groups, in the order the AI chose.
            skills.get_by_role("button", name="Group by context", exact=True).click()
            expect(skills.locator(".skill-group h3")).to_have_count(3)
            assert [text.split("\n")[0].rstrip("0123456789") for text in skills.locator(".skill-group h3").all_inner_texts()] == \
                ["Programming Languages", "Data Engineering", "Databases"]
            languages = skills.locator(".skill-group").filter(has=page.get_by_role("heading", name="Programming Languages"))
            expect(languages.locator("[data-skill-id]")).to_have_count(2)
            expect(languages).to_contain_text("Python")
            expect(skills.locator(".skill-group").filter(has_text="Databases")).to_contain_text("PostgreSQL")
            assert_compact_skills_in_groups = skills.locator(".skill-group .chip-list").count() == 3
            assert assert_compact_skills_in_groups
            if artifacts:
                skills.screenshot(path=str(artifacts / "skills-grouped.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            long_skill = "Designing and maintaining reliable data systems for large international research collaborations"
            page.get_by_label("Add a technical skill").fill(long_skill)
            page.get_by_role("button", name="Add skill", exact=True).click()
            expect(page.get_by_role("checkbox", name=f"Confirm {long_skill}", exact=True)).to_be_checked()
            skills.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
            if artifacts:
                skills.screenshot(path=str(artifacts / "skills-mobile.png"))
            assert not errors, errors
        except Exception:
            if artifacts:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
            raise
        finally:
            browser.close()
    print("Skills browser checks passed: compact chips, hidden source details, legacy skills, confirmation/dismissal, add/remove, metadata preservation, safe text, confirmed skills-only PDF import/retry, unconfirmed job suggestions, context groups, and mobile layout.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    smoke(parser.parse_args().artifacts)
