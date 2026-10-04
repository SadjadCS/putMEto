"""Offline browser checks for resume readability, long fields, and printing.

Run: .venv/bin/python tests/browser_resume_smoke.py --artifacts artifacts/resume-browser
Uses a synthetic resume; never reads or writes the user's workspace data.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import sys

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.resumes import render_html, render_pdf


def smoke(artifacts: Path | None = None):
    from playwright.sync_api import expect, sync_playwright

    resume = json.loads((ROOT / "tests/fixtures/resume_layout.json").read_text())
    errors = []
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "resume-preview.html").write_text(render_html(resume))
        (artifacts / "resume-export.pdf").write_bytes(render_pdf(resume))

    chrome = Path(os.environ.get("PUTMETO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.is_file() else {"channel": "chrome"}))
        context = browser.new_context(viewport={"width": 1440, "height": 1120}, reduced_motion="reduce")
        context.route("**/*", lambda route: route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def assert_text_fits():
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal page overflow"
            problems = page.locator("main").evaluate("""main => {
                const bounds = main.getBoundingClientRect();
                const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
                const problems = [];
                const textRects = [];
                for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                    if (!node.textContent.trim()) continue;
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    for (const rect of range.getClientRects()) {
                        if (!rect.width || !rect.height) continue;
                        if (rect.width && (rect.left < bounds.left - 1 || rect.right > bounds.right + 1)) {
                            problems.push("Outside page: " + node.textContent.trim().slice(0, 100));
                        }
                        textRects.push({rect, text: node.textContent.trim().slice(0, 70)});
                    }
                }
                for (let i = 0; i < textRects.length; i++) {
                    for (let j = i + 1; j < textRects.length; j++) {
                        const a = textRects[i], b = textRects[j];
                        const width = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
                        const height = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
                        if (width > 2 && height > 2) problems.push("Overlapping text: " + a.text + " / " + b.text);
                    }
                }
                return problems;
            }""")
            assert not problems, problems

        try:
            page.set_content(render_html(resume))
            expect(page.get_by_role("heading", name="Alex Morgan", exact=True)).to_be_visible()
            for item in resume["items"]:
                expect(page.locator("main")).to_contain_text(item["title"])
            assert_text_fits()
            if artifacts:
                page.screenshot(path=str(artifacts / "resume-desktop.png"), full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            assert_text_fits()
            if artifacts:
                page.screenshot(path=str(artifacts / "resume-mobile.png"), full_page=True)

            extreme = deepcopy(resume)
            extreme["profile"]["name"] = "Alexandria Morgan International Research"
            extreme["profile"]["website"] = "https://example.com/" + "long-path-component-" * 18
            extreme["profile"]["email"] = "researchcollaboration" * 5 + "@example.com"
            extreme["items"][0]["title"] = "Principal Engineer for International Research and Information Systems " * 3
            extreme["items"][0]["organization"] = "InternationalResearchPartnership" * 7
            extreme["items"][0]["start"] = "September 2018"
            extreme["items"][0]["end"] = "September 2026 (current appointment)"
            extreme["skills"].append("InformationSystemsArchitecture" * 8)
            page.set_content(render_html(extreme))
            for width in (1440, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert_text_fits()
            if artifacts:
                page.screenshot(path=str(artifacts / "resume-long-fields-mobile.png"), full_page=True)

            hostile = deepcopy(resume)
            hostile["items"][0]["title"] = '<img src=x onerror="window.bad=true">'
            hostile["profile"]["website"] = 'javascript:window.bad=true'
            page.set_content(render_html(hostile))
            expect(page.locator("main")).to_contain_text(hostile["items"][0]["title"])
            expect(page.locator("main img, main script, main iframe")).to_have_count(0)
            expect(page.locator('main a[href^="javascript:"]')).to_have_count(0)
            assert page.evaluate("window.bad") is None

            page.set_viewport_size({"width": 1440, "height": 1120})
            page.set_content(render_html(resume))
            page.emulate_media(media="print")
            assert_text_fits()
            browser_pdf = page.pdf(prefer_css_page_size=True, print_background=True)
            document = PdfReader(BytesIO(browser_pdf))
            text = " ".join(" ".join(pdf_page.extract_text() for pdf_page in document.pages).split())
            for value in [resume["profile"]["name"], *(item["title"] for item in resume["items"]), *resume["skills"]]:
                assert value in text, f"Printed resume lost content: {value}"
            assert "Print / save as PDF" not in text
            assert "Download PDF" not in text
            if artifacts:
                (artifacts / "resume-browser-print.pdf").write_bytes(browser_pdf)
                page.screenshot(path=str(artifacts / "resume-print.png"), full_page=True)
            assert not errors, errors
        except Exception:
            if artifacts:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
            raise
        finally:
            browser.close()
    print("Resume browser checks passed: desktop/mobile content, long-field wrapping, safe user text, and complete print output.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    smoke(parser.parse_args().artifacts)
