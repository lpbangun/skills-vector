from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PreviewStaticTests(unittest.TestCase):
    def test_committed_preview_has_three_pilot_occupations(self) -> None:
        occupations = json.loads((ROOT / "preview/release/occupations.json").read_text(encoding="utf-8"))
        ids = {item["occupation_id"] for item in occupations}
        self.assertEqual(
            ids,
            {"occ_founding_engineer", "occ_product_manager", "occ_growth_operator"},
        )
        preview_note = (ROOT / "preview/release/PREVIEW.md").read_text(encoding="utf-8").casefold()
        self.assertIn("fixture", preview_note)

    def test_assemble_script_copies_static_desk(self) -> None:
        subprocess.run(["bash", str(ROOT / "scripts/assemble_vercel.sh")], check=True, cwd=ROOT)
        out = ROOT / ".vercel-out"
        self.assertTrue((out / "index.html").is_file())
        self.assertTrue((out / "desk.html").is_file())
        self.assertTrue((out / "design-system/tokens.css").is_file())
        self.assertTrue((out / "release/occupations.json").is_file())
