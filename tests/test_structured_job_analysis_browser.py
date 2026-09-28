from __future__ import annotations

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Playwright is an optional development dependency.
    sync_playwright = None

from skills_vector.structured_job_analysis import (
    DEFAULT_BASE_CORPUS_DIR,
    DEFAULT_BENCHMARK_DIR,
    build_release,
    load_frozen_corpus,
    render_html,
    render_markdown,
)


@unittest.skipUnless(
    sync_playwright is not None
    and (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")),
    "set PLAYWRIGHT_BROWSERS_PATH or PLAYWRIGHT_CHROMIUM_EXECUTABLE to run the local Chromium responsive check",
)
class StructuredJobAnalysisBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        corpus = load_frozen_corpus(DEFAULT_BENCHMARK_DIR, DEFAULT_BASE_CORPUS_DIR)
        release = build_release(corpus, "a" * 40)
        cls.document = render_html(render_markdown(release), release)

    def test_guide_has_no_page_overflow_on_mobile_or_desktop(self) -> None:
        with sync_playwright() as playwright:
            launch_options = {"headless": True, "args": ["--no-sandbox"]}
            executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            if executable:
                launch_options["executable_path"] = executable
            browser = playwright.chromium.launch(**launch_options)
            try:
                external_requests: list[str] = []

                def record_external_request(request) -> None:
                    if request.url.startswith(("http://", "https://")):
                        external_requests.append(request.url)

                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on("request", record_external_request)
                page.set_content(self.document, wait_until="load")
                mobile = page.evaluate(
                    """() => ({
                        viewport: window.innerWidth,
                        page: document.documentElement.scrollWidth,
                        title: document.title,
                        hashCount: [...document.querySelectorAll('code')]
                            .filter(node => /^[0-9a-f]{64}$/.test(node.textContent.trim())).length,
                        tableWrap: (() => {
                            const node = document.querySelector('.table-wrap');
                            const rect = node.getBoundingClientRect();
                            return {left: rect.left, right: rect.right, client: node.clientWidth, scroll: node.scrollWidth};
                        })()
                    })"""
                )
                self.assertEqual(mobile["title"], "HR Generalist / People Operations — Structured Job Analysis")
                self.assertIn("Prioritized skill practice", page.locator("article").inner_text())
                self.assertIn("Publication status: offline guide", page.locator("article").inner_text())
                self.assertGreater(mobile["hashCount"], 0)
                self.assertLessEqual(mobile["page"], mobile["viewport"])
                self.assertGreaterEqual(mobile["tableWrap"]["left"], 0)
                self.assertLessEqual(mobile["tableWrap"]["right"], mobile["viewport"])
                self.assertGreaterEqual(mobile["tableWrap"]["scroll"], mobile["tableWrap"]["client"])
                self.assertEqual(external_requests, [])

                page.set_viewport_size({"width": 1280, "height": 900})
                desktop = page.evaluate(
                    """() => ({viewport: window.innerWidth, page: document.documentElement.scrollWidth})"""
                )
                self.assertLessEqual(desktop["page"], desktop["viewport"])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
