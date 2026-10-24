"""Offline static-serving and assembly regression checks for the API-backed page.

The full interactive browser gate is scripts/check_role_atlas.py; it is run with
an explicitly selected catalog and the operator's local Chromium, not skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from starlette.testclient import TestClient

from skills_vector.market.core import CatalogStore
from skills_vector.market.role_atlas import FIXTURE_ROOT
from skills_vector.market.web import create_market_app

ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs) -> None:
        self.elements.append((tag, dict(attrs)))


class RoleAtlasWebTests(unittest.TestCase):
    def test_local_routes_serve_the_same_files_and_preserve_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_market_app(CatalogStore(Path(directory)), worktree=ROOT, serve_static=True)) as client:
                for route, filename, media in (
                    ("/role-atlas", "role-atlas.html", "text/html"),
                    ("/role-atlas.html", "role-atlas.html", "text/html"),
                    ("/role-atlas.css", "role-atlas.css", "text/css"),
                    ("/role-atlas.js", "role-atlas.js", "javascript"),
                ):
                    with self.subTest(route=route):
                        response = client.get(route)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.content, (ROOT / "preview" / filename).read_bytes())
                        self.assertIn(media, response.headers["content-type"])
                self.assertEqual(client.get("/").content, (ROOT / "preview/index.html").read_bytes())
                self.assertEqual(client.get("/v1/roles").status_code, 503)
                for path in ("/role-atlas-assets/fonts.css", "/role-atlas-assets/ibm-plex-sans-400.ttf",
                             "/role-atlas-assets/ibmplexsans-OFL.txt"):
                    self.assertEqual(client.get(path).status_code, 200)
                self.assertEqual(client.get("/role-atlas-assets/%2e%2e/%2e%2e/DESIGN.md").status_code, 404)
                self.assertEqual(client.get("/role-atlas-assets/missing.ttf").status_code, 404)
            with TestClient(create_market_app(CatalogStore(Path(directory)))) as client:
                self.assertEqual(client.get("/role-atlas.html").status_code, 404)

    def test_accessible_html_skeleton_and_no_embedded_role_data(self) -> None:
        html = (ROOT / "preview/role-atlas.html").read_text(encoding="utf-8")
        script = (ROOT / "preview/role-atlas.js").read_text(encoding="utf-8")
        parser = Elements()
        parser.feed(html)
        ids = {attrs["id"]: attrs for _, attrs in parser.elements if "id" in attrs}
        tabs = [attrs for _, attrs in parser.elements if attrs.get("role") == "tab"]
        self.assertEqual(len(tabs), 5)
        for tab in tabs:
            panel = ids[tab["aria-controls"]]
            self.assertEqual(panel["role"], "tabpanel")
            self.assertEqual(panel["aria-labelledby"], tab["id"])
            self.assertEqual(panel["tabindex"], "0")
        self.assertIn("hidden", ids["fixtureCallout"])
        self.assertIn("hidden", ids["atlasContent"])
        self.assertIn('lang="en"', html)
        self.assertTrue(any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.elements))
        self.assertIn('fetchJSON("/v1/roles"', script)
        self.assertIn('payload.role.release_id !== catalogRelease', script)
        self.assertIn('ensureOverview(state.role)', script)
        self.assertNotIn('Promise.all(index.roles', script)
        self.assertIn('raw.data_kind !== "fixture"', script)
        self.assertIn('raw.publication === "experimental"', script)
        self.assertIn('return role().raw;', script)
        for path in FIXTURE_ROOT.glob("*.json"):
            record = json.loads(path.read_text())
            self.assertNotIn(record["label"], html + script)
            self.assertNotIn(record["release_id"], html + script)
            self.assertNotIn(record["id"], html + script)
        self.assertNotIn("132", html)
        self.assertNotIn("0.19", html)
        self.assertNotIn("rel_example", script)
        self.assertNotIn("skills-vector.role/1", script)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertIn('id="ledgerBrier"', html)
        self.assertIn('id="ledgerCalibration"', html)
        self.assertIn("envelopes[state.role].track_record", script)
        self.assertIn("report.brier_score === null", script)
        self.assertIn("No scored research forecasts", script)

    def test_vercel_assembly_includes_page_assets_and_untouched_legacy_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/assemble_vercel.sh", root / "scripts/assemble_vercel.sh")
            shutil.copytree(ROOT / "design-concepts/design-system", root / "design-concepts/design-system")
            (root / "preview/release/.staging").mkdir(parents=True)
            (root / "preview/release/.staging/not-public").write_text("staging", encoding="utf-8")
            pointer = '{"current": null, "releases": []}\n'
            (root / "preview/release/current.json").write_text(pointer, encoding="utf-8")
            for filename in ("index.html", "role-atlas.html", "role-atlas.css", "role-atlas.js"):
                shutil.copy2(ROOT / "preview" / filename, root / "preview" / filename)
            shutil.copytree(ROOT / "preview/role-atlas-assets", root / "preview/role-atlas-assets")
            result = subprocess.run(["bash", str(root / "scripts/assemble_vercel.sh")], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = root / ".vercel-out"
            for filename in ("index.html", "role-atlas.html", "role-atlas.css", "role-atlas.js"):
                self.assertEqual((out / filename).read_bytes(), (ROOT / "preview" / filename).read_bytes())
            self.assertEqual((out / "release/current.json").read_text(), pointer)
            self.assertEqual((root / "preview/release/current.json").read_text(), pointer)
            self.assertFalse((out / "release/.staging").exists())
            self.assertFalse((out / "role-atlas-fixtures").exists())
            for source in (ROOT / "preview/role-atlas-assets").iterdir():
                self.assertEqual((out / "role-atlas-assets" / source.name).read_bytes(), source.read_bytes())
            self.assertIn("assembled", result.stdout)


if __name__ == "__main__":
    unittest.main()
