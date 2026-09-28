"""Optional real Camoufox checks; run with PUTMETO_BROWSER_SMOKE=1.

Only example.com is visited. The application form is synthetic and fulfilled
inside the browser; no applicant information is sent to an external service.
"""

import asyncio
import os

import pytest

from backend import browser


pytestmark = pytest.mark.skipif(os.environ.get("PUTMETO_BROWSER_SMOKE") != "1", reason="Optional installed-browser smoke test")


def test_real_camoufox_navigation_autofill_and_cleanup(monkeypatch):
    async def check():
        manager, instance, context = await browser._launch(headless=True)
        try:
            page = await context.new_page()
            await page.goto("https://example.com", wait_until="domcontentloaded", timeout=45_000)
            assert await page.title() == "Example Domain"
            assert await page.locator("h1").inner_text() == "Example Domain"
        finally:
            await manager.__aexit__(None, None, None)
        assert not instance.is_connected()

        real_launch = browser._launch
        requests = []

        async def synthetic_launch(headless):
            # Exercise the real start_application code without creating a GUI
            # or uploading the synthetic CV to a third-party application form.
            result = await real_launch(headless=True)
            _, _, application_context = result

            async def form(route):
                requests.append((route.request.method, route.request.url))
                await route.fulfill(status=200, content_type="text/html", body="""
                    <!doctype html><title>Synthetic application</title>
                    <form method="post" action="https://example.com/putmeto-test-submit">
                      <input name="first_name"><input name="last_name">
                      <input name="email" type="email"><input name="phone" type="tel">
                      <input name="website"><input type="file" name="resume">
                      <button type="submit">Submit application</button>
                    </form>
                """)

            await application_context.route("https://example.com/putmeto-test-*", form)
            return result

        monkeypatch.setattr(browser, "_launch", synthetic_launch)
        try:
            result = await browser.start_application(
                "synthetic-test", "https://example.com/putmeto-test-form",
                {"name": "Test Applicant", "email": "test@example.invalid", "phone": "555-0100", "website": "https://example.invalid"},
                b"%PDF-1.4\n% synthetic test fixture\n%%EOF",
            )
            assert result == {"filled_fields": 5, "resume_attached": True}
            assert browser.browser_status()["active_sessions"] == 1
            application_browser = browser._sessions["synthetic-test"]["browser"]
            page = application_browser.contexts[0].pages[0]
            assert await page.locator('[name="first_name"]').input_value() == "Test"
            assert await page.locator('[name="last_name"]').input_value() == "Applicant"
            assert await page.locator('[name="email"]').input_value() == "test@example.invalid"
            assert (await page.locator('[name="resume"]').input_value()).endswith("resume.pdf")
            assert requests == [("GET", "https://example.com/putmeto-test-form")]
            await page.close()
            for _ in range(50):
                if not browser._sessions and not application_browser.is_connected():
                    break
                await asyncio.sleep(0.1)
            assert not browser._sessions
            assert not application_browser.is_connected()
        finally:
            await browser.close_browsers()

    asyncio.run(check())
