import os
from pathlib import Path

import pytest

LIVE_URL = os.getenv("LIVE_DEMO_URL")


@pytest.mark.skipif(not LIVE_URL, reason="Set LIVE_DEMO_URL for the real Azure browser check.")
def test_real_chat_kql_and_trace_in_edge():
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            with page.expect_response("**/api/config") as configuration:
                page.goto(LIVE_URL)
            configured = configuration.value.json()
            configured_traces_url = configured["foundry_traces_url"]
            expect(page.locator("#model-badge")).to_have_text("gpt-5-mini")
            expect(page.locator("#foundry-link")).to_be_visible()
            expect(page.locator("#foundry-link")).to_have_attribute("href", configured_traces_url)
            if configured["public_demo"]:
                expect(page.locator("#public-notice")).to_be_visible()
                expect(page.locator("#approve-button")).to_be_disabled()
                assert (
                    page.request.post(
                        f"{LIVE_URL}/api/reference/approve",
                        data={},
                        headers={"X-Demo-Request": "1"},
                    ).status
                    == 403
                )
                assert (
                    page.request.post(
                        f"{LIVE_URL}/api/chat",
                        data={"question": "Count users"},
                        headers={"X-Demo-Request": "1", "Origin": "https://another.example"},
                    ).status
                    == 403
                )
                assert (
                    page.request.post(
                        f"{LIVE_URL}/api/chat",
                        data="x" * 8193,
                        headers={"X-Demo-Request": "1"},
                    ).status
                    == 413
                )
            page.locator("#ask-button").click()
            expect(page.locator("#answer-text")).to_have_text(
                "4 users are affected by DEMO-2026-0001.", timeout=90000
            )
            expect(page.locator("#candidate-kql")).to_contain_text("distinct UserId")
            expect(page.locator("#spans")).to_contain_text("validate_intent")
            expect(page.locator("#spans")).to_contain_text("execute_query candidate")
            expect(page.locator("#trace-id")).to_contain_text("Trace ")
            if os.getenv("LIVE_DEMO_PUBLISH") == "1":
                expect(page.locator("#compare-button")).to_be_enabled()
                page.locator("#compare-button").click()
                expect(page.locator("#cloud-status")).to_contain_text(
                    "Foundry evaluation: completed", timeout=240000
                )
                expect(page.locator("#comparison-cards .count")).to_have_text(["4", "8"])
                expect(page.locator("#comparison-cards .badge")).to_have_text(["PASS", "FAIL"])
                expect(page.locator("#cloud-status")).to_contain_text("1 passed / 1 failed")
            original_theme = page.locator("html").get_attribute("data-theme")
            page.locator("#theme-button").click()
            assert page.locator("html").get_attribute("data-theme") != original_theme
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            screenshot = os.getenv("DEMO_SCREENSHOT_PATH")
            if screenshot:
                page.set_viewport_size({"width": 1440, "height": 1100})
                page.screenshot(path=str(Path(screenshot)), full_page=True)
            assert not errors
        finally:
            browser.close()
