"""Offline browser regression check for CV import acceptance, failures, and retries.

Run: .venv/bin/python tests/browser_import_smoke.py
Optional: --artifacts artifacts/import-browser
Requires Playwright and Chrome (PUTMETO_CHROME can override its executable).
Every browser request is intercepted; no server, real resume, AI, or LinkedIn is used.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import re
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
    pending_imports = []
    import_requests = []
    master_status = {"state": "idle", "message": "", "model": "", "effort": "", "started_at": "", "running": False, "has_cv": False, "built": None}
    errors = []
    origin = "http://putmeto.test"
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)

    def route_request(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.url.split("/", 3)[:3] != origin.split("/", 3)[:3]:
            errors.append(f"Unexpected external request: {request.url}")
            route.abort()
        elif path == "/api/state":
            route.fulfill(json=workspace)
        elif path == "/api/job-matching":
            route.fulfill(json={"state": "idle", "message": "", "done": 0, "total": 0, "model": "", "effort": "", "running": False, "waiting": 0})
        elif path == "/api/goldmove":
            route.fulfill(json={"state": "idle", "message": "", "done": 0, "total": 0, "running": False, "waiting": 0,
                                "eligible": 0, "checked": 0, "dismissed": 0, "candidates": []})
        elif path == "/api/skill-groups":
            route.fulfill(json={"state": "idle", "message": "", "model": "", "effort": "", "running": False, "waiting": 0})
        elif path == "/api/master-resume":
            route.fulfill(json=master_status)
        elif path == "/api/import" and request.method == "POST":
            import_requests.append(request)
            # Deliberately hold the response so progress and disabled controls
            # can be inspected without timing assumptions or real AI latency.
            pending_imports.append(route)
        elif path.startswith("/api/"):
            errors.append(f"Unexpected API request: {request.method} {path}")
            route.fulfill(status=404, json={"detail": "Unknown fixture endpoint"})
        else:
            filename = ROOT / "static" / (path.rsplit("/", 1)[-1] or "index.html")
            if filename.is_file():
                route.fulfill(
                    body=filename.read_bytes(),
                    content_type=mimetypes.guess_type(filename.name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)

    chrome = Path(os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            **({"executable_path": str(chrome)} if chrome.is_file() else {"channel": "chrome"}),
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        form = page.locator("#import-form")
        picker = form.locator("input[type=file]")
        submit = form.locator("button[type=submit]")
        error = form.locator("#import-error")
        status = form.locator("#import-status")

        def choose(name: str, content: bytes, mime_type: str = "application/pdf") -> None:
            picker.set_input_files({"name": name, "mimeType": mime_type, "buffer": content})

        def assert_inline_error(message: str | re.Pattern) -> None:
            expect(error).to_be_visible()
            expect(error).to_contain_text(message)
            expect(submit).to_be_enabled()
            expect(picker).to_be_enabled()
            assert page.locator("#modal").evaluate("dialog => dialog.open")
            error.scroll_into_view_if_needed()
            assert error.evaluate("""element => {
                const bounds = element.getBoundingClientRect();
                const hit = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
                return hit === element || element.contains(hit);
            }"""), "The upload error is obscured by another layer."

        def begin_import() -> None:
            with page.expect_request(lambda request: urlsplit(request.url).path == "/api/import"):
                submit.click()
            expect(status).to_be_visible()
            expect(status).to_contain_text(re.compile(r"read|extract|analy|upload|process", re.I))
            expect(submit).to_be_disabled()
            expect(picker).to_be_disabled()
            expect(error).to_be_hidden()
            assert len(pending_imports) == 1

        def finish_import(status_code: int, body: dict) -> None:
            pending_imports.pop().fulfill(status=status_code, json=body)

        try:
            page.goto(origin + "/#profile")
            page.get_by_role("button", name="Import your CV", exact=True).click()
            expect(page.locator("#modal")).to_contain_text("All extracted information is accepted by default")

            choose("resume.doc", b"Synthetic legacy Word fixture", "application/msword")
            submit.click()
            assert_inline_error("save or download it as a PDF")
            assert not import_requests, "Word files reached the import API."

            choose("resume.exe", b"Synthetic unsupported file", "application/octet-stream")
            submit.click()
            assert_inline_error(re.compile(r"PDF|plain text|supported|file type", re.I))
            assert not import_requests, "Unsupported files reached the import API."

            choose("large.pdf", b"x" * (10 * 1024 * 1024 + 1))
            submit.click()
            assert_inline_error(re.compile(r"10\s*MB|too large|smaller", re.I))
            assert not import_requests, "Oversized files reached the import API."

            # A browser may label a valid extension with a generic MIME type.
            choose("RESUME.PDF", b"%PDF-1.4\nSynthetic fixture only", "application/octet-stream")
            begin_import()
            failure = "The selected file cannot be read. Choose a supported resume file."
            finish_import(415, {"detail": failure})
            assert_inline_error(failure)
            assert picker.evaluate("input => input.files[0].name") == "RESUME.PDF"
            if artifacts:
                page.screenshot(path=str(artifacts / "upload-error-desktop.png"), full_page=True)

            # Retry the retained file; an AI error must also remain visible in
            # the dialog and leave the user able to retry without reopening it.
            begin_import()
            finish_import(503, {"detail": "AI extraction is temporarily unavailable. Please try again."})
            assert_inline_error("AI extraction is temporarily unavailable. Please try again.")
            assert len(import_requests) == 2

            choose("resume.pdf", b"%PDF-1.4\nSynthetic fixture only")
            begin_import()
            workspace["profile"]["name"] = "Fixture Applicant"
            workspace["items"] = [{
                "id": "fixture-experience", "kind": "experience", "title": "Research assistant",
                "organization": "Fixture University", "start": "2024", "end": "2025",
                "original": "Maintained sample records.", "enhanced": "Maintained research sample records.",
                "confirmed": True,
            }]
            workspace["skills"] = [{"id": "fixture-skill", "name": "Data management", "confirmed": True, "origin": "resume", "support": "supported"}]
            master_status.update(state="working", running=True, has_cv=True, model="gpt-6-astra", effort="ultra",
                                 message="Building your master resume with gpt-6-astra at ultra reasoning. This can take several minutes; you can keep working.")
            finish_import(200, {"count": 1, "skill_count": 1, "master_resume": True, "message": "CV imported and confirmed. You can edit your profile anytime."})
            expect(page.locator("#modal")).not_to_be_visible()
            expect(page.locator("#profile-form [name=name]")).to_have_value("Fixture Applicant")
            expect(page.locator(".item-card")).to_contain_text("Research assistant")
            panel = page.locator("#master-resume")
            expect(panel).to_contain_text("Building your master resume")
            expect(panel).to_contain_text("gpt-6-astra at ultra reasoning")
            expect(panel.get_by_role("button")).to_have_count(0)
            # When the build finishes, the page shows it and reloads the organized entries by itself.
            workspace["items"][0].update(original="Maintained sample records for the master resume.", enhanced="", source="cv")
            master_status.update(state="done", running=False, message="Your master resume is ready.",
                                 built={"built_at": "2026-09-30T14:05:00+00:00", "model": "gpt-6-astra", "effort": "ultra"})
            expect(panel).to_contain_text("Built from your CV on", timeout=15000)
            expect(panel).to_contain_text("with gpt-6-astra at ultra reasoning, in your own words.")
            expect(panel.get_by_role("button", name="Rebuild", exact=True)).to_be_visible()
            expect(page.locator(".item-card")).to_contain_text("Maintained sample records for the master resume.")
            expect(page.locator(".item-card")).to_contain_text("Confirmed")
            expect(page.locator(".item-card")).to_contain_text("Ready to include in your resume")
            expect(page.get_by_role("button", name="Review & confirm", exact=True)).to_have_count(0)
            expect(page.locator('[data-skill-id="fixture-skill"]')).to_contain_text("Data management")
            expect(page.locator("#technical-skills [data-skill-group]")).to_have_count(0)
            expect(page.locator("#technical-skills")).not_to_contain_text("Listed in your CV")
            expect(page.get_by_role("checkbox", name="Confirm Data management", exact=True)).to_be_checked()
            page.get_by_role("button", name="Edit", exact=True).click()
            expect(page.locator("#item-form [name=original]")).to_have_value("Maintained sample records for the master resume.")
            page.locator('#modal [data-action="close-modal"]').click()
            assert len(import_requests) == 3

            # Word CVs are rejected before upload with guidance to save them as PDF.
            page.get_by_role("button", name="Import your CV", exact=True).click()
            document = BytesIO()
            with ZipFile(document, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic Word CV</w:t></w:r></w:p></w:body></w:document>')
            choose("RESUME.DOCX", document.getvalue(), "application/octet-stream")
            submit.click()
            assert_inline_error("save or download it as a PDF")
            choose("resume.txt", b"Fixture Applicant\nResearch assistant", "text/plain")
            submit.click()
            assert_inline_error("save or download it as a PDF")
            assert len(import_requests) == 3, "Only PDFs reach the import API."
            expect(picker).to_have_attribute("accept", ".pdf,application/pdf")
            page.locator('#modal [data-action="close-modal"]').click()

            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="Import your CV", exact=True).click()
            choose("resume.pdf", b"%PDF-1.4\nSynthetic mobile fixture")
            begin_import()
            finish_import(415, {"detail": failure})
            assert_inline_error(failure)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
            if artifacts:
                page.screenshot(path=str(artifacts / "upload-error-mobile.png"), full_page=True)
            assert not errors, errors
        except Exception:
            if artifacts:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
                (artifacts / "errors.json").write_text(json.dumps(errors, indent=2))
            raise
        finally:
            browser.close()
    print("CV import browser checks passed: PDF-only file validation with guidance for Word and text files, visible inline errors, progress, retry, automatic confirmation, optional editing, successful profile refresh, master resume progress and completion, and mobile layout.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    smoke(parser.parse_args().artifacts)
