#!/usr/bin/env python3
"""Offline browser gate: uv run --with playwright python scripts/check_role_atlas.py OUT --role-atlas-fixtures --compare-reference."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from skills_vector.market.role_reads import add_role_atlas_options
from skills_vector.market.server import build_server

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
TABS = ("map", "levels", "industries", "gap", "agent")
CASES = ((1280, "light"), (1280, "dark"), (390, "light"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def local_server(args):
    if args.url:
        yield args.url.rstrip("/")
        return
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = build_server(ROOT, ROOT / "preview/release", port=port,
                              role_atlas_root=args.role_atlas_root, role_atlas_fixtures=args.role_atlas_fixtures)
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            require(server.started, "local server failed to start")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            require(not thread.is_alive(), "local server failed to stop")


def assert_view(page, label: str) -> None:
    metrics = page.evaluate("""() => ({width: innerWidth, scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
        targets: [...document.querySelectorAll('button, summary, .checks label, a.citation, footer a, #graph [role=button]')]
          .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden')
          .map(node => ({label: node.textContent.trim().slice(0, 90), width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height}))})""")
    require(metrics["scroll"] <= metrics["width"], f"{label}: horizontal page overflow {metrics}")
    for target in metrics["targets"]:
        require(target["height"] >= 43.9 and target["width"] >= 43.9, f"{label}: target below 44px: {target}")


def current_record(page) -> dict:
    return json.loads(page.locator("#json").get_attribute("data-raw"))


def snapshot(page, output: Path, label: str) -> None:
    page.locator("h1").click(position={"x": 1, "y": 1})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(output / (label + ".png")), full_page=True, animations="disabled")


def exercise_map(page, record: dict, output: Path, role: str) -> None:
    skills = {skill["id"]: skill for skill in record["skill_map"]["skills"]}
    rows = page.locator("#map .srow")
    require(rows.count() == len(skills), "map must show every API skill")
    for index in range(rows.count()):
        row = rows.nth(index)
        skill = skills[row.get_attribute("data-skill")]
        require(skill["label"] in row.get_attribute("aria-label"), "map row lacks its full accessible label")
        require("Evidence:" in row.get_attribute("aria-label"), "map row lacks spoken evidence families")
        row.click()
        require(page.locator("#skillPanel h3").inner_text() == skill["label"], "skill detail mismatch")
        if skill["forecast"]:
            require("Base rate:" in page.locator("#skillPanel").inner_text(), "missing forecast base rate")
    for level in record["ladder"]["levels"]:
        number = level["level"]
        page.locator(f'#mapLevel [data-mlevel="{number}"]').click()
        expected = {key for key, skill in skills.items() if skill["share_by_level"][number - 1] >= 15
                    or (skill["status"] == "fading" and skill["share_by_level"][number - 1] > 0)}
        require(set(page.locator("#map .srow").evaluate_all("nodes => nodes.map(n => n.dataset.skill)")) == expected,
                "needed-at filter does not match API shares")
    page.locator('#mapLevel [data-mlevel="0"]').click()
    page.keyboard.press("Tab")
    page.locator("#map .srow").first.focus()
    require(page.locator("#map .srow").first.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'"), "missing visible focus")
    page.locator('[data-mode="conn"]').click()
    require(page.locator("#connWrap").is_visible(), "connections did not open")
    require(page.locator("#graph [data-node]").count() == len(skills), "graph lost API nodes")
    require(page.locator("#graph .g-edge").count() == len(record["skill_map"]["asked_together"]), "wrong qualified edges")
    snapshot(page, output, role + "-connections")
    page.locator("#graph [data-node]").first.focus()
    page.locator("#graph [data-node]").first.press("Enter")
    require(page.locator("#graphPanel #clearNode").is_visible(), "graph keyboard selection failed")
    selected = page.locator("#graph [data-node]").first.get_attribute("data-node")
    page.locator("#clearNode").click()
    require(page.evaluate("document.activeElement.dataset.node") == selected, "graph summary lost keyboard focus")
    disputed = next((skill for skill in skills.values() if skill["disputed"]), None)
    if disputed:
        ids = page.locator("#graph [data-node]").evaluate_all("nodes => nodes.map(node => node.dataset.node)")
        page.locator("#graph [data-node]").nth(ids.index(disputed["id"])).click()
        require(disputed["disputed"] in page.locator("#graphPanel").inner_text(), "graph hid a dispute")
        require(page.locator("#graph .g-flag").count() > 0, "dispute relied on color alone")
        page.locator("#clearNode").click()
    page.locator('[data-lens="top"]').click()
    require(page.locator("#graph .g-ring").count() == len(record["skill_map"]["top_level_adds"]["skills"]), "wrong top-level highlights")
    page.locator('[data-edges="builds"]').click()
    require(page.locator("#graph .g-edge.build").count() == len(record["skill_map"]["builds_on"]), "wrong builds-on edges")
    page.locator('[data-lens="level"]').click()
    page.locator('[data-edges="co"]').click()
    assert_view(page, role + " connections")
    page.locator('[data-mode="list"]').click()


def exercise_tab(page, tab: str, record: dict, requests: list[str]) -> None:
    if tab == "levels":
        for level in record["ladder"]["levels"]:
            page.locator(f'#stairs [data-level="{level["level"]}"]').click()
            require(page.locator("#sheet h3").inner_text() == level["name"], "level title mismatch")
            require(level["owns"] in page.locator("#sheet").inner_text(), "missing level ownership")
        target = record["ladder"]["levels"][min(2, len(record["ladder"]["levels"]) - 1)]["level"]
        page.locator(f'#stairs [data-level="{target}"]').click()
    elif tab == "industries":
        for index, sector in enumerate(record["industries"]):
            page.locator("#industryPick button").nth(index).click()
            require(page.locator("#indSheet h3").inner_text() == sector["label"], "sector title mismatch")
            if sector["profile"] is None:
                require(sector["reason"] in page.locator("#indSheet").inner_text(), "thin sector caveat missing")
        if record["industries"]:
            page.locator("#industryPick button").first.click()
    elif tab == "gap":
        before_requests = len(requests)
        before = page.locator("#next li").count()
        checkbox = page.locator("#checks input").first
        checked = checkbox.is_checked()
        checkbox.set_checked(not checked)
        require(page.locator("#next li").count() != before, "gap comparison did not update locally")
        page.locator("#checks input").first.set_checked(checked)
        require(len(requests) == before_requests, "gap interaction sent a network request")
        prefs = page.evaluate("JSON.parse(localStorage.getItem('sv-role-atlas') || '{}')")
        require("have" not in prefs, "personal gap evidence was persisted")
    elif tab == "agent":
        require(json.loads(page.locator("#json").inner_text()) == record, "agent JSON differs from role/2 API record")
        require(record["release_id"] in page.locator("#agentPath").inner_text(), "agent URL is not pinned")


def keyboard_tabs(page) -> None:
    page.locator("#t-map").focus()
    for key, target in (("ArrowLeft", "agent"), ("Home", "map"), ("End", "agent"), ("ArrowRight", "map")):
        page.keyboard.press(key)
        require(page.locator(f"#t-{target}").get_attribute("aria-selected") == "true", f"tabs {key} failed")
        require(page.evaluate("document.activeElement.id") == "t-" + target, "tab focus did not follow selection")


def check_case(browser, base: str, out: Path, width: int, scheme: str, reference: bool) -> dict:
    output = out / ("reference" if reference else "app") / f"{width}-{scheme}"
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    requests: list[str] = []
    context = browser.new_context(viewport={"width": width, "height": 900 if width == 1280 else 844},
                                  color_scheme=scheme, reduced_motion="reduce", service_workers="block")
    page = context.new_page()
    page.on("console", lambda message: errors.append("console: " + message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append("page: " + str(error)))
    page.on("request", lambda request: requests.append(request.url))
    origin = urlsplit(base)
    reference_html = (ROOT / "design-concepts/role-atlas.html").read_text(encoding="utf-8")

    def route(request_route):
        url = request_route.request.url
        if urlsplit(url).netloc == origin.netloc:
            if urlsplit(url).path == "/__role_atlas_reference":
                request_route.fulfill(body='<meta charset="utf-8"><link rel="icon" href="data:,">' + reference_html, content_type="text/html; charset=utf-8")
            else:
                request_route.continue_()
        elif url.startswith("https://fonts.googleapis.com/"):
            # Keep the unmodified reference's font metrics, without any external
            # network call: its Google stylesheet is replaced by the vendored one.
            request_route.fulfill(body=(ROOT / "preview/role-atlas-assets/fonts.css").read_text().replace("url(/role-atlas-assets/", "url(" + base + "/role-atlas-assets/"), content_type="text/css; charset=utf-8")
        elif url.startswith("data:"):
            request_route.continue_()
        else:
            errors.append("unexpected external request: " + url)
            request_route.abort()
    page.route("**/*", route)
    try:
        page.goto(base + ("/__role_atlas_reference" if reference else "/role-atlas.html"), wait_until="networkidle")
        if not reference:
            page.wait_for_function("['true', 'error'].includes(document.querySelector('#app').dataset.ready)")
            require(page.locator("#app").get_attribute("data-ready") == "true", page.locator("#loadMessage").inner_text())
        page.evaluate("document.fonts.ready")
        fonts = page.evaluate("[...document.fonts].map(face => ({family: face.family.replace(/[\\\"']/g, ''), status: face.status}))")
        for family in ("IBM Plex Sans", "IBM Plex Mono", "STIX Two Text"):
            require(any(face["family"] == family and face["status"] == "loaded" for face in fonts), "reference typography failed to load: " + family)
        require(not any(face["status"] == "error" for face in fonts), "a local font failed to decode")
        roles = page.locator("#rolePick button").evaluate_all("nodes => nodes.map(node => node.dataset.role)")
        require(bool(roles), "catalog has no role controls")
        screenshots = 0
        for index, role in enumerate(roles):
            page.locator("#rolePick button").nth(index).click()
            if not reference:
                record = current_record(page)
                require(record["id"] == "role." + role, "wrong selected API role")
                require(page.locator("#fixtureCallout").is_visible() == (record["data_kind"] == "fixture"), "fixture caveat visibility mismatch")
                require(page.locator("#experimentalCaveat").count() == (1 if record["publication"] == "experimental" else 0), "experimental caveat mismatch")
                keyboard_tabs(page)
            for tab in TABS:
                page.locator("#t-" + tab).click()
                if not reference:
                    require(page.locator('#tabs [aria-selected="true"]').count() == 1, "multiple selected tabs")
                    require(page.locator('#tabs [tabindex="0"]').count() == 1, "tabs do not use roving focus")
                    require(page.locator('[role="tabpanel"]:not([hidden])').count() == 1, "multiple visible tab panels")
                    exercise_tab(page, tab, record, requests)
                    assert_view(page, role + " " + tab)
                snapshot(page, output, role + "-" + tab)
                screenshots += 1
                if tab == "map":
                    if reference:
                        page.locator('[data-mode="conn"]').click()
                        snapshot(page, output, role + "-connections")
                        page.locator('[data-mode="list"]').click()
                    else:
                        exercise_map(page, record, output, role)
                    screenshots += 1
            if not reference:
                page.locator("#copyJson").click()
                page.wait_for_function("document.querySelector('#copyJson').textContent !== 'Copy JSON'")
                for summary in page.locator("details.more summary").all():
                    summary.click()
                    assert_view(page, role + " method details")
                    summary.click()
                require(not errors, "\n".join(errors))
        if not reference:
            page.reload(wait_until="networkidle")
            page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
            require(page.locator("#t-agent").get_attribute("aria-selected") == "true", "selected tab did not persist")
            page.goto(base + "/", wait_until="networkidle")
            require(page.title(), "existing preview failed to load")
        require(not errors, "\n".join(errors))
        return {"width": width, "scheme": scheme, "reference": reference, "roles": roles, "screenshots": screenshots, "errors": errors}
    finally:
        context.close()


def check_failure_states(browser, base: str) -> None:
    with browser.new_context() as context:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.route("**/v1/roles", lambda route: route.fulfill(json={"status": "role_catalog_unavailable", "detail": "Test: no selected catalog"}))
        page.goto(base + "/role-atlas.html")
        page.wait_for_function("document.querySelector('#app').dataset.ready === 'error'")
        require(page.locator("#atlasContent").is_hidden(), "error state exposed stale data")
        require(page.locator("#fixtureCallout").is_hidden(), "error state exposed a fixture fallback")
        require("no selected catalog" in page.locator("#loadMessage").inner_text(), "missing unavailable explanation")
        page.unroute("**/v1/roles")
        page.locator("#retryLoad").click()
        page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")

        for field in ("release_id", "schema", "data_kind"):
            def mismatch(route):
                payload = route.fetch().json()
                raw = payload["role"]
                raw[field] = {
                    "release_id": raw["release_id"] + ".mismatch",
                    "schema": "skills-vector.role/1",
                    "data_kind": "research" if raw["data_kind"] == "fixture" else "fixture",
                }[field]
                route.fulfill(json=payload)
            page.route("**/v1/roles/*", mismatch)
            page.reload()
            page.wait_for_function("document.querySelector('#app').dataset.ready === 'error'")
            require("do not match" in page.locator("#loadMessage").inner_text(), f"mismatched {field} was silently accepted")
            page.unroute("**/v1/roles/*")

        def escape_probe(route):
            payload = route.fetch().json()
            payload.pop("presentation", None)
            payload["role"]["label"] = '<img src=x onerror="window.atlasInjected=true">'
            payload["role"]["skill_map"]["skills"][0]["label"] = '<svg onload="window.atlasInjected=true">'
            route.fulfill(json=payload)
        page.route("**/v1/roles/*", escape_probe)
        page.reload()
        page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
        require(page.locator("#roleHead h3").inner_text().startswith("<img"), "API label was not rendered as text")
        require(page.locator("#roleHead img, #map svg").count() == 0, "API markup was injected into the DOM")
        require(page.evaluate("window.atlasInjected !== true"), "untrusted API text executed")
        page.unroute("**/v1/roles/*")
        require(not errors, "\n".join(errors))
    with browser.new_context() as context:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new Error('storage disabled'); }});")
        page.goto(base + "/role-atlas.html")
        page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
        require(page.locator("#rolePick button").count() > 0, "optional storage blocked browsing")
        require(not errors, "\n".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshots", type=Path)
    parser.add_argument("--url", help="existing local server; otherwise start the shared ASGI app")
    parser.add_argument("--executable-path", type=Path, default=CHROMIUM)
    parser.add_argument("--compare-reference", action="store_true")
    add_role_atlas_options(parser)
    args = parser.parse_args()
    require(args.executable_path.is_file(), f"Chromium is missing: {args.executable_path}")
    args.screenshots.mkdir(parents=True, exist_ok=True)
    results = []
    with local_server(args) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(args.executable_path), headless=True, args=["--no-sandbox"])
        try:
            for width, scheme in CASES:
                results.append(check_case(browser, base, args.screenshots, width, scheme, False))
                if args.compare_reference:
                    results.append(check_case(browser, base, args.screenshots, width, scheme, True))
            check_failure_states(browser, base)
        finally:
            browser.close()
    (args.screenshots / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
