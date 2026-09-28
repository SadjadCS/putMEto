"""Offline Chrome smoke test for skill review, CV recovery, and job suggestions.

Run: .venv/bin/python tests/browser_skills_smoke.py --artifacts artifacts/skills-browser
Every request is intercepted. No real CV, workspace data, AI, or job site is used.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from io import BytesIO
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db import default_state


def smoke(artifacts: Path | None = None) -> None:
    from playwright.sync_api import expect, sync_playwright

    workspace = default_state()
    workspace["settings"].pop("api_key", None)
    workspace["settings"]["api_key_set"] = False
    workspace["items"] = [{
        "id": "existing-experience", "kind": "experience", "title": "Research engineer",
        "organization": "Fixture University", "original": "Fine-tuned Llama 2 with QLoRA.",
        "enhanced": "Fine-tuned Llama 2 with QLoRA.", "confirmed": True,
    }]
    workspace["skills"] = [
        {"id": "legacy-python", "name": "Python", "confirmed": True},
        {"id": "legacy-r", "name": "R", "confirmed": False},
        {"id": "fine", "name": "Fine-tuning", "kind": "technique", "support": "supported", "origin": "resume",
         "confirmed": False, "evidence": "Fine-tuned Llama 2 with QLoRA.",
         "rationale": "A reusable modeling technique demonstrated in your project.", "aliases": ["LLM fine-tuning"]},
        {"id": "llama", "name": "Llama 2", "kind": "tool", "support": "supported", "origin": "resume",
         "confirmed": False, "evidence": "Fine-tuned Llama 2 with QLoRA."},
        {"id": "sql", "name": "SQL querying", "kind": "technique", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": "Review this option if you have queried relational data."},
        {"id": "postgres", "name": "PostgreSQL", "kind": "database", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": "A database option to confirm if you have used it."},
        {"id": "vectors", "name": "Vector databases", "kind": "database", "support": "related", "origin": "suggestion",
         "confirmed": False, "rationale": '<img src=x onerror="window.bad=true"> Review your actual experience.'},
    ]
    workspace["jobs"] = [{"id": "fixture-job", "title": "ML engineer", "company": "Fixture Company",
                           "location": "Europe", "status": "saved", "match_score": 0,
                           "description": "Work on retrieval and fine-tuning.", "source": "Fixture",
                           "url": "https://example.com/jobs/ml"}]
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
                workspace["skills"].append({"id": "rag", "name": "Retrieval-augmented generation",
                                             "kind": "technique", "support": "related", "origin": "suggestion",
                                             "confirmed": False, "rationale": "Relevant to the saved ML engineer opportunity."})
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

        try:
            page.goto(origin + "/#profile")
            expect(skills.locator('[data-skill-group="confirmed"]')).to_contain_text("Python")
            expect(skills.locator('[data-skill-group="review"]')).to_contain_text("R")
            expect(card("fine")).to_contain_text("Technique")
            expect(card("fine")).to_contain_text("Listed in your CV")
            expect(card("fine")).to_contain_text("Fine-tuned Llama 2 with QLoRA.")
            expect(card("fine")).to_contain_text("LLM fine-tuning")
            expect(card("llama")).to_contain_text("Tool")
            expect(card("postgres")).to_contain_text("Database")
            expect(card("vectors")).to_contain_text("Explore and confirm")
            assert skills.locator("img").count() == 0
            assert page.evaluate("window.bad") is None

            page.get_by_role("checkbox", name="Confirm Fine-tuning", exact=True).check()
            expect(skills.locator('[data-skill-group="confirmed"]')).to_contain_text("Fine-tuning")
            saved = next(item for item in writes[-1]["items"] if item["id"] == "fine")
            assert saved["confirmed"] is True and saved["evidence"] == "Fine-tuned Llama 2 with QLoRA."
            assert saved["aliases"] == ["LLM fine-tuning"]
            page.get_by_role("checkbox", name="Confirm Fine-tuning", exact=True).uncheck()
            expect(skills.locator('[data-skill-group="supported"]')).to_contain_text("Fine-tuning")
            page.get_by_role("checkbox", name="Confirm PostgreSQL", exact=True).check()
            expect(skills.locator('[data-skill-group="confirmed"]')).to_contain_text("PostgreSQL")
            assert next(item for item in writes[-1]["items"] if item["id"] == "postgres")["support"] == "related"

            page.get_by_role("button", name="Dismiss SQL querying", exact=True).click()
            expect(card("sql")).to_have_count(0)
            assert all(item["id"] != "sql" for item in writes[-1]["items"])
            page.get_by_label("Add a technical skill").fill("Docker")
            page.get_by_role("button", name="Add skill", exact=True).click()
            expect(page.get_by_role("checkbox", name="Confirm Docker", exact=True)).to_be_checked()
            page.get_by_role("button", name="Suggest skills", exact=True).click()
            expect(page.get_by_role("button", name="Suggest skills", exact=True)).to_be_enabled()
            assert suggestions[-1] == {}

            page.get_by_role("button", name="Import skills from CV", exact=True).click()
            form = page.locator("#import-form")
            expect(form).to_have_attribute("data-mode", "skills")
            expect(form).to_contain_text("keeps your existing experience entries")
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
            document = BytesIO()
            with ZipFile(document, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Skills: vector indexing.</w:t></w:r></w:p></w:body></w:document>')
            picker.set_input_files({"name": "fixture.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "buffer": document.getvalue()})
            with page.expect_request(lambda request: urlsplit(request.url).path == "/api/import/skills"):
                submit.click()
            expect(submit).to_be_disabled()
            assert b'filename="fixture.docx"' in imports[-1].post_data_buffer
            workspace["skills"].append({"id": "indexing", "name": "Vector indexing", "kind": "technique", "support": "supported", "origin": "resume", "confirmed": False, "evidence": "Skills: vector indexing."})
            pending_imports.pop().fulfill(json={"count": 1, "skill_count": 1, "message": "Imported a skill for review."})
            expect(page.locator("#modal")).not_to_be_visible()
            expect(card("indexing")).to_contain_text("Listed in your CV")
            expect(page.get_by_role("checkbox", name="Confirm Vector indexing", exact=True)).not_to_be_checked()
            assert workspace["items"] == original_items
            assert len(imports) == 2

            navigate("jobs")
            page.get_by_role("button", name="View opportunity", exact=True).click()
            page.get_by_role("button", name="Suggest relevant skills", exact=True).click()
            expect(page.locator("#job-skills-status")).to_be_visible()
            expect(page.locator("#job-skills-status")).to_contain_text("Retry shortly")
            page.get_by_role("button", name="Suggest relevant skills", exact=True).click()
            expect(page.locator("#modal")).not_to_be_visible()
            expect(card("rag")).to_contain_text("Relevant to the saved ML engineer opportunity")
            assert suggestions[-1] == {"job_id": "fixture-job"}
            assert page.url.endswith("#profile?skills")

            while page.get_by_role("button", name="Dismiss notification", exact=True).count():
                page.get_by_role("button", name="Dismiss notification", exact=True).first.click()
            if artifacts:
                skills.screenshot(path=str(artifacts / "skills-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
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
    print("Skills browser checks passed: grouped evidence, legacy skills, confirmation/dismissal, metadata preservation, safe text, skills-only PDF/DOCX import/retry, job suggestions, and mobile layout.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    smoke(parser.parse_args().artifacts)
