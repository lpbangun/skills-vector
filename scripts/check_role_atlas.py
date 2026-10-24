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
                              role_atlas_root=args.role_atlas_root, role_atlas_fixtures=args.role_atlas_fixtures, role_atlas_ledger=args.role_atlas_ledger)
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


def assert_track_record(page, base: str, role: str, record: dict) -> None:
    report = page.request.get(base + "/v1/track-record?role=" + role + "&release=" + record["release_id"]).json()
    require(report["status"] == "ok" and report["release_id"] == record["release_id"], "track record lost release pin")
    stats = report["track_record"]
    require(page.locator("#ledgerTracked").inner_text() == (str(stats["tracked_count"]) if stats["ledger_selected"] else "—"), "ledger count differs from API")
    require(page.locator("#ledgerResolved").inner_text() == (str(stats["resolved_count"]) if stats["ledger_selected"] else "—"), "resolved count differs from API")
    expected = "—" if stats["brier_score"] is None else f'{stats["brier_score"]:.3f}'
    require(page.locator("#ledgerBrier").inner_text() == expected, "Brier differs from API")
    require(stats["backtest"]["status"] in page.locator("#ledgerBacktest").inner_text(), "backtest status missing")
    if stats["scored_count"] == 0:
        require(stats["brier_score"] is None and stats["calibration_bins"] == [], "empty ledger fabricated scores")
        require(page.locator("#ledgerCalibration .cal-row").count() == 0 and "No scored research forecasts" in page.locator("#ledgerCalibration").inner_text(), "empty calibration state missing")
    else:
        require(page.locator("#ledgerCalibration .cal-row").count() == sum(bin["n"] > 0 for bin in stats["calibration_bins"]), "calibration bins differ from API")
    if record["data_kind"] == "fixture":
        require(stats["scored_count"] == 0, "fixture forecasts counted as research track record")


def assert_detail_fit(page) -> None:
    overflow = page.locator("#skillPanel").evaluate("""panel => {
        const p = panel.getBoundingClientRect(), bad = [];
        const check = (r, label) => { if (r.width && (r.left < p.left - 0.5 || r.right > p.right + 0.5)) bad.push(label); };
        panel.querySelectorAll("*").forEach(node => check(node.getBoundingClientRect(), node.tagName));
        const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) { const range = document.createRange(); range.selectNodeContents(walker.currentNode);
            [...range.getClientRects()].forEach(rect => check(rect, walker.currentNode.textContent.slice(0, 80))); }
        return bad;
    }""")
    require(not overflow, f"detail panel overflow: {overflow}")


def assert_gap_rows_fit(page) -> None:
    bad = page.locator("#checks").evaluate("""root => {
        const rows = [...root.querySelectorAll("label")], bad = [];
        rows.forEach((row,i) => { const r=row.getBoundingClientRect();
            if(i && r.top < rows[i-1].getBoundingClientRect().bottom-.5) bad.push("overlapping rows");
            [...row.children].forEach(child => { const c=child.getBoundingClientRect();
                if(c.top<r.top-.5 || c.bottom>r.bottom+.5) bad.push("content outside label"); }); });
        return bad;
    }""")
    require(not bad, f"gap rows overlap/clip: {bad[:5]}")


def timing_label(source: dict) -> str:
    return source["timing"] + (" · rated " + source["as_of"][:4] if source.get("as_of") else "")


def assert_graph_fit(page) -> None:
    page.wait_for_function("""() => { const graph = document.querySelector('#graph');
        return document.querySelector('#graphScrollCue').hidden === (graph.scrollWidth <= graph.clientWidth); }""")
    metrics = page.locator("#graph").evaluate("""graph => {
        const rect = graph.getBoundingClientRect();
        const box = node => { const r = node.getBoundingClientRect(); return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width, height:r.height}; };
        return {viewport:innerWidth, scroll:graph.scrollWidth, client:graph.clientWidth,
          visible:{left:rect.left, right:rect.left+graph.clientWidth, top:rect.top, bottom:rect.top+graph.clientHeight},
          nodes:[...graph.querySelectorAll('[data-node], .g-lvname')].map(node => ({id:node.dataset.node || node.textContent, ...box(node)})),
          hits:[...graph.querySelectorAll('.g-hit')].map(box)}; }""")
    require(page.locator("#graphScrollCue").is_visible() == (metrics["scroll"] > metrics["client"]), "graph scroll cue visibility mismatch")
    for hit in metrics["hits"]:
        require(hit["width"] >= 43.9 and hit["height"] >= 43.9, f"graph hit target below 44px: {hit}")
    if metrics["viewport"] == 1280:
        require(metrics["scroll"] <= metrics["client"], f"desktop graph scrolls: {metrics}")
        visible = metrics["visible"]
        for node in metrics["nodes"]:
            require(node["left"] >= visible["left"] - 0.5 and node["right"] <= visible["right"] + 0.5
                    and node["top"] >= visible["top"] - 0.5 and node["bottom"] <= visible["bottom"] + 0.5,
                    f"node/heading outside visible graph: {node}; container: {visible}")


def reject_graph_fit(page, reason: str) -> None:
    try:
        assert_graph_fit(page)
    except AssertionError as error:
        require(reason in str(error), f"wrong graph-fit rejection: {error}")
    else:
        raise AssertionError("graph-fit gate accepted a deliberately clipped graph")


def enforce_budget(name: str, value: float, maximum: float) -> None:
    require(isinstance(value, (int, float)) and 0 <= value <= maximum,
            f"budget exceeded: {name}={value}, limit={maximum}")


def light_budgets(page, base: str, requests: list[str]) -> dict:
    # Called before any full-record test fetch or tab visit; no server warm-up.
    ms = page.evaluate("window.__gateInteractive")
    initial = [u for u in requests if '/v1/roles/' in u]
    require(len(initial) == 1 and 'view=overview' in initial[0], "initial page eagerly fetched full/other roles")
    require(page.locator('#json').get_attribute('data-raw') is None and page.locator('#graph [data-node]').count() == 0,
            "hidden JSON/graph was eagerly rendered")
    response = page.request.get(initial[0])
    payload = response.json()
    measured = {"overview_bytes": len(response.body()), "interactive_ms": ms, "tails": []}
    print('BUDGET initial ' + json.dumps(measured), flush=True)
    enforce_budget('overview bytes', measured['overview_bytes'], 400_000)
    enforce_budget('interactive milliseconds', ms, 2_000)
    for group in page.locator('#map details.skill-more').all():
        status = group.get_attribute('data-status')
        group.evaluate("""group => {
            window.__gateTailMs=null;
            group.querySelector('summary').addEventListener('click',()=>window.__gateTailStart=performance.now(),{once:true,capture:true});
            group.addEventListener('toggle',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>window.__gateTailMs=performance.now()-window.__gateTailStart)),{once:true});
        }""")
        group.locator('summary').click()
        page.wait_for_function('window.__gateTailMs !== null')
        elapsed = page.evaluate('window.__gateTailMs')
        measured['tails'].append({"status": status, "mode": "lazy list", "milliseconds": elapsed})
        print('BUDGET tail ' + json.dumps(measured['tails'][-1]), flush=True)
        enforce_budget('tail expansion milliseconds', elapsed, 1_000)
        tail = next(g for g in payload['overview']['tails'] if g['status'] == status)
        actual = group.locator('.srow').evaluate_all('nodes=>nodes.map(n=>({id:n.dataset.skill,label:n.firstElementChild.textContent}))')
        require(sorted(actual,key=lambda s:s['id']) == sorted(tail['skills'],key=lambda s:s['id']), 'lazy tail ids/labels lost')
        require(not any('view=full' in u for u in requests), 'expanding labels fetched full role')
        pending = group.locator('[data-detail="pending"]')
        if pending.count():
            sid = pending.first.get_attribute('data-skill')
            pending.first.click()
            page.wait_for_function("id=>!Array.from(document.querySelectorAll('#map [data-skill]')).find(n=>n.dataset.skill===id).hasAttribute('data-detail')", arg=sid)
            require(any('/skills/' in u and 'release=' in u for u in requests), 'tail detail was not fetched release-pinned')
            expected = page.request.get(base + '/v1/roles/' + payload['role_id'] + '/skills/' + sid + '?release=' + payload['release_id']).json()['skill']
            require(page.locator('#skillPanel h3').inner_text() == expected['label'], 'lazy skill detail mismatch')
            assert_detail_fit(page)
        group.locator('summary').click()
    return measured


def current_record(page) -> dict:
    previous = page.locator("#tabs [aria-selected=true]").get_attribute("data-tab")
    page.locator("#t-agent").click()
    page.wait_for_function("document.querySelector('#app').dataset.tabReady === 'agent'")
    record = json.loads(page.locator("#json").get_attribute("data-raw"))
    page.locator("#t-" + previous).click()
    page.wait_for_function("tab=>document.querySelector('#app').dataset.tabReady === tab", arg=previous)
    return record


def snapshot(page, output: Path, label: str) -> None:
    page.locator("h1").click(position={"x": 1, "y": 1})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(output / (label + ".png")), full_page=True, animations="disabled")


def exercise_map(page, record: dict, output: Path, role: str) -> None:
    skills = {skill["id"]: skill for skill in record["skill_map"]["skills"]}
    rows = page.locator("#map .srow")
    # Collapsed tails are not built in the DOM until requested.
    floor = 3 if len(skills) > 100 else 0
    default_ids = {key for key, skill in skills.items() if not floor or skill["windows"][-1]["employers"] >= floor}
    require(rows.count() == len(default_ids), "collapsed map eagerly built tail rows")
    require(set(page.locator("#map .srow:visible").evaluate_all("nodes => nodes.map(n => n.dataset.skill)")) == default_ids, "wrong overview rows")
    require(("Overview floor: 3 employers" if floor else "All skills shown") in page.locator("#overviewPolicy").inner_text(), "display floor not stated")
    for status in ("core", "rising", "early", "fading"):
        hidden = [skill for skill in skills.values() if floor and skill["status"] == status and skill["windows"][-1]["employers"] < floor]
        group = page.locator(f'#map details[data-status="{status}"]')
        require(group.count() == bool(hidden), "missing status collapse group")
        if hidden:
            require(f"{len(hidden)} more, asked by fewer than {floor} employers" in group.locator("summary").inner_text(), "wrong collapsed count")
            group.locator("summary").click()
            page.wait_for_function("status=>document.querySelector('#map details[data-status='+status+']').dataset.loaded==='true'", arg=status)
    require(page.locator("#map .srow:visible").count() == len(skills), "expanded map lost rows")
    page.evaluate("() => { const r=JSON.parse(document.querySelector('#json').dataset.raw); window.__atlasGateSkills=Object.fromEntries(r.skill_map.skills.map(s=>[s.id,s])); }")
    for start in range(0, len(skills), 32):
        page.evaluate("""start => {
            const check=(ok,reason)=>{if(!ok)throw Error(reason);};
            [...document.querySelectorAll('#map .srow')].slice(start,start+32).forEach(row=>{
                const skill=window.__atlasGateSkills[row.dataset.skill],spoken=row.getAttribute('aria-label');
                check(spoken.includes(skill.label)&&spoken.includes('Evidence:'),'incomplete accessible label: '+skill.id);
                row.click();
                const panel=document.querySelector('#skillPanel'),forecast=skill.forecast,cell=row.querySelector('.call');
                check(panel.querySelector('h3').textContent===skill.label,'skill detail mismatch: '+skill.id);
                if(forecast&&forecast.basis==='none'){
                    check(cell.textContent==='No forecast yet'&&!cell.textContent.includes('%'),'unavailable forecast cell');
                    check(!panel.querySelector('.forecast .p'),'unavailable forecast percentage');
                    check(panel.querySelector('.forecast').textContent.includes(forecast.reason)&&panel.querySelector('.forecast').textContent.includes(forecast.claim),'none-basis detail lost reason/claim');
                } else if(forecast) check(panel.textContent.includes('Base rate:')&&cell.textContent.includes('%'),'numeric forecast missing');
                const style=getComputedStyle(cell);
                check(cell.getBoundingClientRect().height<=parseFloat(style.lineHeight)+1&&cell.scrollWidth<=cell.clientWidth+1,'forecast cell wraps: '+skill.id);
                const box=panel.getBoundingClientRect();
                const fit=r=>check(!r.width||(r.left>=box.left-.5&&r.right<=box.right+.5),'detail panel overflow: '+skill.id);
                panel.querySelectorAll('*').forEach(n=>fit(n.getBoundingClientRect()));
                const walker=document.createTreeWalker(panel,NodeFilter.SHOW_TEXT);
                while(walker.nextNode()){const range=document.createRange();range.selectNodeContents(walker.currentNode);[...range.getClientRects()].forEach(fit);}
                const expected=skill.evidence.map(s=>(s.timing+(s.as_of?' · rated '+s.as_of.slice(0,4):'')).toLowerCase());
                check(JSON.stringify([...panel.querySelectorAll('.sig .tm')].map(n=>n.textContent.toLowerCase()))===JSON.stringify(expected),'source timing/as-of mismatch');
                if(skill.status==='rising'){
                    const n=skill.evidence.filter(s=>['O','M'].includes(s.family)&&s.direction==='up').length;
                    check(panel.textContent.includes(n+' independent source'+(n===1?' points':'s point')+' up.'),'source-count grammar');
                }
            });
        }""", start)
        if len(skills) > 100 and start % 256 == 0:
            print(f"{role}: checked details {min(start + 32, len(skills))}/{len(skills)}", flush=True)
    for level in record["ladder"]["levels"]:
        number = level["level"]
        page.locator(f'#mapLevel [data-mlevel="{number}"]').click()
        expected = {key for key, skill in skills.items() if skill["share_by_level"][number - 1] is not None and (skill["share_by_level"][number - 1] >= 15
                    or (skill["status"] == "fading" and skill["share_by_level"][number - 1] > 0))}
        require(set(page.locator("#map .srow").evaluate_all("nodes => nodes.map(n => n.dataset.skill)")) == expected,
                "needed-at filter does not match API shares")
    compared = record["skill_map"]["top_level_adds"]
    if any(level["evidence"]["postings"] == 0 for level in record["ladder"]["levels"] if level["level"] in (compared["top_level"], compared["core_level"])):
        assigned = sum(level["evidence"]["postings"] for level in record["ladder"]["levels"])
        expected = f"Not enough level-assigned postings to compare levels: {assigned} of {record['demand_window']['postings']} postings have a level."
        require(expected in page.locator("#topLine").inner_text(), "missing comparison used placeholder titles")
    page.locator('#mapLevel [data-mlevel="0"]').click()
    for summary in page.locator("#map details[open] > summary").all(): summary.click()
    page.keyboard.press("Tab")
    if default_ids:
        page.locator("#map .srow:visible").first.focus()
        require(page.locator("#map .srow:visible").first.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'"), "missing visible focus")
    elif not skills:
        require(record["demand_window"]["postings"] == 0 and "No admitted postings" in page.locator("#skillPanel").inner_text(), "empty role must state missing admission/evidence")
    page.locator('[data-mode="conn"]').click()
    require(page.locator("#connWrap").is_visible(), "connections did not open")
    require(set(page.locator("#graph [data-node]").evaluate_all("nodes => nodes.map(n=>n.dataset.node)")) == default_ids, "graph overview rows mismatch")
    for button in page.locator("#graphOverview button").all(): button.click()
    require(page.locator("#graph [data-node]").count() == len(skills), "expanded graph lost API nodes")
    require(page.locator("#graph .g-edge").count() == len(record["skill_map"]["asked_together"]), "wrong qualified edges")
    for node in page.locator("#graph [data-node]").evaluate_all("nodes=>nodes.map(n=>({id:n.dataset.node,label:n.querySelector('.g-label').textContent}))"):
        require(node["label"] == skills[node["id"]]["short_label"], "graph truncated a label")
    for button in page.locator("#graphOverview button").all(): button.click()
    require(set(page.locator("#graph [data-node]").evaluate_all("nodes=>nodes.map(n=>n.dataset.node)")) == default_ids, "graph did not collapse back")
    assert_graph_fit(page)
    if page.viewport_size["width"] == 390:
        page.locator("#graph").evaluate("graph => graph.scrollLeft = 0")
        page.locator("#graph").focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_function("document.querySelector('#graph').scrollLeft > 0")
        page.locator("#graph").evaluate("graph => graph.scrollLeft = 0")
    snapshot(page, output, role + "-connections")
    if default_ids:
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
    require(page.locator("#graph .g-ring").count() == len(set(record["skill_map"]["top_level_adds"]["skills"]) & default_ids), "wrong top-level highlights")
    top = record["skill_map"]["top_level_adds"]
    if any(skill["share_by_level"][top["top_level"] - 1] is None or skill["share_by_level"][top["core_level"] - 1] is None for skill in skills.values()):
        compared = [level for level in record["ladder"]["levels"] if level["level"] in (top["top_level"], top["core_level"])]
        expected = "Not enough level-assigned postings" if any(level["evidence"]["postings"] == 0 for level in compared) else "No measured comparison"
        require(expected in page.locator("#graphPanel").inner_text(), "missing level samples implied a measured comparison")
    page.locator('[data-edges="builds"]').click()
    require(page.locator("#graph .g-edge.build").count() == sum(e["a"] in default_ids and e["b"] in default_ids for e in record["skill_map"]["builds_on"]), "wrong builds-on edges")
    page.locator('[data-lens="level"]').click()
    page.locator('[data-edges="co"]').click()
    assert_graph_fit(page)
    assert_view(page, role + " connections")
    page.locator('[data-mode="list"]').click()


def exercise_tab(page, tab: str, record: dict, requests: list[str]) -> None:
    if tab == "levels":
        require(page.locator("#stairs .nm").first.evaluate("node => getComputedStyle(node).hyphens === 'auto' && node.closest('[lang]').lang === 'en'"), "ladder hyphenation needs English language metadata")
        for level in record["ladder"]["levels"]:
            page.locator(f'#stairs [data-level="{level["level"]}"]').click()
            expected_name = level["name"] if level["typical_titles"] else "L" + str(level["level"])
            require(page.locator("#sheet h3").inner_text() == expected_name, "level title mismatch")
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
        assert_gap_rows_fit(page)
        before_requests = len(requests)
        before = page.locator("#next").inner_text()
        checkbox = page.locator("#checks input").first
        if checkbox.count():
            checked = checkbox.is_checked()
            checkbox.set_checked(not checked)
            require(page.locator("#next").inner_text() != before, "gap comparison did not update locally")
            page.locator("#checks input").first.set_checked(checked)
        else:
            require("No assigned expectations" in page.locator("#checks").inner_text(), "empty gap input hid missing evidence")
            require("Insufficient assigned evidence" in before, "empty gap comparison claimed completeness")
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
    page.add_init_script("""
        const observer=new MutationObserver(()=>{
            if(document.querySelector('#app')?.dataset.ready==='true'){
                observer.disconnect(); requestAnimationFrame(()=>requestAnimationFrame(()=>window.__gateInteractive=performance.now()));
            }
        }); observer.observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['data-ready']});
    """)
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
        budgets = None
        if not reference:
            page.wait_for_function("window.__gateInteractive !== undefined")
            budgets = light_budgets(page, base, requests)
        roles = page.locator("#rolePick button").evaluate_all("nodes => nodes.map(node => node.dataset.role)")
        require(bool(roles), "catalog has no role controls")
        screenshots = 0
        for index, role in enumerate(roles):
            page.locator("#rolePick button").nth(index).click()
            if not reference:
                page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
                record = current_record(page)
                require(record["id"] == "role." + role, "wrong selected API role")
                sample = page.locator("#sampleNotice").inner_text()
                require(f"{record['demand_window']['postings']} postings from {record['demand_window']['employers']} employers" in sample and "tech-heavy, not a market census" in sample, "sample framing/counts missing")
                require(page.locator("#fixtureCallout").is_visible() == (record["data_kind"] == "fixture"), "fixture caveat visibility mismatch")
                require(page.locator("#experimentalCaveat").count() == (1 if record["publication"] == "experimental" else 0), "experimental caveat mismatch")
                head_sources = [source for kind in ("onet_ratings", "bls_projections")
                                for source in record["foundation"]["sources"] if source["source_kind"] == kind]
                if record["data_kind"] == "research":
                    response = page.request.get(base + "/v1/roles/" + role + "?release=" + record["release_id"]).json()
                    require("presentation" not in response, "research selection received fixture layout")
                    require(not record["skill_map"]["skills"] or "level unassigned" in page.locator("#skillPanel").inner_text() or any(s["first_level"] is not None for s in record["skill_map"]["skills"]), "unknown levels were promoted")
                tags = page.locator("#roleHead .tm").all_inner_texts()
                require([tag.casefold() for tag in tags] == [timing_label(source) for source in head_sources] + [record["demand_window"]["timing"]], "role head lost the source rating year")
                assert_track_record(page, base, role, record)
                method = page.request.get(base + "/v1/roles/" + role + "?release=" + record["release_id"]).json()["method"]
                require(page.locator("#sourceTable tr").count() == len(method["sources"]), "canonical source table is incomplete")
                for source in method["sources"]:
                    row = page.locator('#sourceTable tr[data-source-kind="' + source["source_kind"] + '"]')
                    require(row.locator(".tm").inner_text() == source["timing"] and source["good_for"] in row.inner_text(), "canonical source metadata mismatch")
                require(page.locator("#h-q").count() == 0 and "quarterly" in page.locator("#methodWindows").inner_text(), "stale Decisions scaffolding")
                keyboard_tabs(page)
            for tab in TABS:
                page.locator("#t-" + tab).click()
                if not reference:
                    require(page.locator('#tabs [aria-selected="true"]').count() == 1, "multiple selected tabs")
                    require(page.locator('#tabs [tabindex="0"]').count() == 1, "tabs do not use roving focus")
                    require(page.locator('[role="tabpanel"]:not([hidden])').count() == 1, "multiple visible tab panels")
                    page.wait_for_function("tab=>document.querySelector('#app').dataset.tabReady===tab", arg=tab)
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
                for summary in page.locator("details.more:not(.skill-more) summary").all():
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
        return {"width": width, "scheme": scheme, "reference": reference, "roles": roles, "screenshots": screenshots, "errors": errors, "budgets": budgets}
    finally:
        context.close()


def check_failure_states(browser, base: str) -> None:
    with browser.new_context(viewport={"width": 1280, "height": 900}) as context:
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

        page.locator("#t-gap").click()
        page.wait_for_function("document.querySelector('#app').dataset.tabReady==='gap'")
        assert_gap_rows_fit(page)
        style = page.add_style_tag(content="#checks { grid-auto-rows: 1px; gap: 0; }")
        try:
            try:
                assert_gap_rows_fit(page)
            except AssertionError as error:
                require("gap rows overlap/clip" in str(error), "wrong gap-row rejection")
            else:
                raise AssertionError("gap row gate accepted deliberately overlapping labels")
        finally:
            style.evaluate("node => node.remove()")
        assert_gap_rows_fit(page)
        page.locator("#t-map").click()
        style = page.add_style_tag(content="#skillPanel .sig li > span:last-child { white-space: nowrap; }")
        try:
            try:
                assert_detail_fit(page)
            except AssertionError as error:
                require("detail panel overflow" in str(error), "wrong overflow rejection")
            else:
                raise AssertionError("overflow gate accepted unwrapped evidence text")
        finally:
            style.evaluate("node => node.remove()")
        assert_detail_fit(page)
        page.locator('[data-mode="conn"]').click()
        page.evaluate("document.fonts.ready")
        assert_graph_fit(page)
        style = page.add_style_tag(content="#graph svg { min-width: 960px; }")
        try:
            reject_graph_fit(page, "desktop graph scrolls")
        finally:
            style.evaluate("node => node.remove()")
        node = page.locator("#graph [data-node]").first
        node.evaluate("node => node.setAttribute('transform', 'translate(-960 0)')")
        try:
            reject_graph_fit(page, "outside visible graph")
        finally:
            node.evaluate("node => node.removeAttribute('transform')")
        assert_graph_fit(page)
        page.locator('[data-mode="list"]').click()

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
            if payload["role"]["skill_map"]["skills"]:
                payload["role"]["skill_map"]["skills"][0]["label"] = '<svg onload="window.atlasInjected=true">'
            route.fulfill(json=payload)
        page.route("**/v1/roles/*", escape_probe)
        page.reload()
        page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
        require(page.locator("#roleHead h3").inner_text().startswith("<img"), "API label was not rendered as text")
        require(page.locator("#roleHead img, #map svg").count() == 0, "API markup was injected into the DOM")
        require(page.evaluate("window.atlasInjected !== true"), "untrusted API text executed")
        page.unroute("**/v1/roles/*")
        for as_of in ("2018-04-03", None):
            def source_probe(route):
                payload = route.fetch().json()
                def dates(value):
                    if isinstance(value, list):
                        for child in value:
                            dates(child)
                    elif isinstance(value, dict):
                        if value.get("source_kind") == "onet_ratings":
                            value["reading"] = "Test prose says rated 2099; do not parse it."
                            if as_of is None:
                                value.pop("as_of", None)
                            else:
                                value["as_of"] = as_of
                        else:
                            for child in value.values():
                                dates(child)
                dates(payload["role"])
                rising = next((skill for skill in payload["role"]["skill_map"]["skills"] if skill["status"] == "rising"), None)
                if rising:
                    kind = "hiring_lab" if any(source["source_kind"] == "onet_emerging_tasks" for source in rising["evidence"]) else "onet_emerging_tasks"
                    rising["evidence"].append({"source": "Test independent source", "source_kind": kind,
                        "family": "M" if kind == "hiring_lab" else "O", "timing": "coincident" if kind == "hiring_lab" else "leading",
                        "direction": "up", "reading": "Test upward confirmation.", "as_of": None})
                route.fulfill(json=payload)
            page.route("**/v1/roles/*", source_probe)
            page.reload()
            page.wait_for_function("document.querySelector('#app').dataset.ready === 'true'")
            expected = "lagging · rated 2018" if as_of else "lagging"
            require(page.locator("#roleHead .tm").first.inner_text().casefold() == expected, "source date was parsed from prose")
            record = current_record(page)
            require(page.locator('#sourceTable tr[data-source-kind="onet_ratings"] .tm').inner_text().casefold() == "lagging", "canonical Method table inherited a skill rating date")
            rising = next((skill for skill in record["skill_map"]["skills"] if skill["status"] == "rising"), None)
            if rising:
                ids = page.locator("#map .srow").evaluate_all("nodes => nodes.map(node => node.dataset.skill)")
                page.locator("#map .srow").nth(ids.index(rising["id"])).click()
                count = sum(source["family"] in ("O", "M") and source["direction"] == "up" for source in rising["evidence"])
                require(count > 1 and f"{count} independent sources point up." in page.locator("#skillPanel").inner_text(), "plural copy failed")
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
