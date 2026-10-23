"""Offline authored samples in upstream formats; no downloads or metered services."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from skills_vector.market.role_artifacts import canonical, digest, read_json, write_json
from skills_vector.market.role_atlas import FIXTURE_ROOT, RoleCatalogStore, RoleRecord, RoleValidationError
from skills_vector.market.role_signals import import_signals, merge_signals
from skills_vector.market_cli import run

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/role-atlas"
FILES = {"hot-technologies": "Technology Skills.sample.tsv", "emerging-tasks": "Emerging Tasks.sample.tsv",
         "bls-industry": "occupation.sample.csv", "certifications": "certifications.sample.json"}
STAMP = "2026-09-01T12:00:00Z"


def sample_import(kind: str) -> dict:
    return import_signals(kind, SAMPLES / FILES[kind], STAMP, sample=True,
                          soc="13-1071.00" if kind == "certifications" else None)


def xlsx_sample(path: Path, rows: list[list[str]], *, formula: bool = False, merges: tuple[str, ...] = ()) -> None:
    """Authored scalar workbook, matching spreadsheet cells; never an agency file."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    def col(n):
        result = ""
        while n:
            n, rem = divmod(n - 1, 26)
            result = chr(65 + rem) + result
        return result
    xml_rows = []
    for index, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{col(j)}{index}" t="inlineStr"><is><t>{escape(cell)}</t></is></c>' for j, cell in enumerate(row, 1))
        xml_rows.append(f'<row r="{index}">{cells}</row>')
    if formula:
        xml_rows.append('<row><c r="A9"><f>1+1</f><v>2</v></c></row>')
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sample" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        merged = '<mergeCells>' + ''.join(f'<mergeCell ref="{ref}"/>' for ref in merges) + '</mergeCells>'
        book.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml_rows)}</sheetData>{merged}</worksheet>')


class OfficialSignalTests(unittest.TestCase):
    def test_all_importers_have_canonical_provenance_and_deterministic_sample_output(self):
        expected = {"hot-technologies": (2, "coincident", "M", "presence"), "emerging-tasks": (1, "leading", "O", "up"),
                    "bls-industry": (1, "lagging", "context", "presence"), "certifications": (1, "lagging", "O", "presence")}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            for kind, (count, timing, family, direction) in expected.items():
                first, second = sample_import(kind), sample_import(kind)
                self.assertEqual(canonical(first), canonical(second))
                self.assertEqual((first["data_kind"], len(first["records"])), ("sample", count))
                for row in first["records"]:
                    signal = row["signal"]
                    self.assertEqual((signal["retrieved_at"], signal["timing"], signal["family"], signal["direction"]), (STAMP, timing, family, direction))
                    self.assertTrue(signal["source"])
                    self.assertEqual(len(first["input_sha256"]), 64)
        self.assertIn("not a broad-sector aggregate", sample_import("bls-industry")["records"][0]["signal"]["reading"])
        self.assertIn("not evidence of employer requirements", sample_import("certifications")["records"][0]["signal"]["reading"])

    def test_cli_import_each_kind_and_unlabeled_samples_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for kind, filename in FILES.items():
                output = Path(d) / (kind + ".json")
                args = ["signals", "import", kind, "--input", str(SAMPLES / filename), "--output", str(output), "--retrieved-at", STAMP, "--sample"]
                if kind == "certifications":
                    args += ["--soc", "13-1071.00"]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(run(args), 0)
                self.assertEqual(read_json(output), sample_import(kind))
                with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                    run([a for a in args if a != "--sample"])

    def test_real_file_mode_and_bad_upstream_columns_flags_dates_and_counts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Technology Skills.txt"
            good = (SAMPLES / FILES["hot-technologies"]).read_text()
            path.write_text(good)
            result = import_signals("hot-technologies", path, STAMP)
            self.assertEqual(result["data_kind"], "research")
            for text in (good.replace("Hot Technology", "Wrong Header"), good.replace("\tY\tY", "\tmaybe\tY"),
                         good.replace("41-4011.00", "bad-code")):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    import_signals("hot-technologies", path, STAMP)
            path.write_text(good)
            for stamp in ("2026-09-01", "2026-09-01T00:00:00", "2026-02-30T00:00:00Z"):
                with self.assertRaises(ValueError):
                    import_signals("hot-technologies", path, stamp)
            path.write_text((SAMPLES / FILES["emerging-tasks"]).read_text().replace("\tEmerging\t2", "\tCore\t2"))
            with self.assertRaises(ValueError):
                import_signals("emerging-tasks", path, STAMP)
            path.write_text((SAMPLES / FILES["emerging-tasks"]).read_text().replace("\tEmerging\t2", "\tEmerging\t0"))
            with self.assertRaises(ValueError):
                import_signals("emerging-tasks", path, STAMP)

    def test_bls_scalar_xlsx_and_invalid_values(self):
        import csv
        rows = list(csv.reader((SAMPLES / FILES["bls-industry"]).read_text().splitlines()))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "occupation.xlsx"
            xlsx_sample(path, [["Sample table; values in thousands"], *rows])
            imported = import_signals("bls-industry", path, STAMP, sample=True)
            self.assertEqual(imported["records"], sample_import("bls-industry")["records"])
            xlsx_sample(path, rows, formula=True)
            with self.assertRaisesRegex(ValueError, "formulas"):
                import_signals("bls-industry", path, STAMP)
            split = [rows[0][:5] + ["Employment", "Employment", "Employment change", "Percent change", "Percent of industry employment", "Percent of occupation employment"],
                     [""] * 5 + ["2024", "2034", "2024-34", "2024-34", "2024", "2024"], rows[1]]
            xlsx_sample(path, split)
            self.assertEqual(import_signals("bls-industry", path, STAMP)["records"], sample_import("bls-industry")["records"])
            merged = copy.deepcopy(split)
            merged[0][6] = ""
            xlsx_sample(path, merged, merges=("A1:A2", "B1:B2", "C1:C2", "D1:D2", "E1:E2", "F1:G1"))
            self.assertEqual(import_signals("bls-industry", path, STAMP)["records"], sample_import("bls-industry")["records"])
            xlsx_sample(path, split, merges=("F1:G200001",))
            with self.assertRaisesRegex(ValueError, "oversized merged"):
                import_signals("bls-industry", path, STAMP)
            with self.assertRaisesRegex(ValueError, "base year"):
                import_signals("bls-industry", path, STAMP, year=1899)
            path = Path(d) / "occupation.csv"
            for value in ("101", "-1", "NaN", "bad"):
                changed = copy.deepcopy(rows)
                changed[1][-1] = value
                with path.open("w") as handle:
                    csv.writer(handle).writerows(changed)
                with self.assertRaises(ValueError):
                    import_signals("bls-industry", path, STAMP)

    def test_certifications_require_local_saved_response_query_scope_and_names(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "certifications.json"
            good = read_json(SAMPLES / FILES["certifications"])
            path.write_text(json.dumps(good))
            for options in ({}, {"soc": "bad"}, {"soc": "13-1071.00", "naics": "invalid"}):
                with self.assertRaises(ValueError):
                    import_signals("certifications", path, STAMP, **options)
            variant = copy.deepcopy(good)
            variant["CertList"][0]["CertOrg"] = variant["CertList"][0].pop("Organization")
            path.write_text(json.dumps(variant))
            self.assertEqual(import_signals("certifications", path, STAMP, soc="13-1071.00")["records"][0]["label"], good["CertList"][0]["CertName"])
            for key in ("CertId", "CertName", "Organization"):
                bad = copy.deepcopy(good)
                bad["CertList"][0].pop(key)
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    import_signals("certifications", path, STAMP, soc="13-1071.00")
            path.write_text('{"CertList": [], "CertList": []}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON"):
                import_signals("certifications", path, STAMP, soc="13-1071.00")

    def test_merge_all_sources_validates_preserves_labels_counts_and_floors_and_repeats(self):
        bundles = [sample_import(kind) for kind in FILES]
        before = {p.name: p.read_bytes() for p in FIXTURE_ROOT.glob("*.json")}
        with tempfile.TemporaryDirectory() as d:
            one, two = Path(d) / "one", Path(d) / "two"
            result = merge_signals(FIXTURE_ROOT, bundles, one)
            self.assertEqual(result, merge_signals(FIXTURE_ROOT, bundles, two))
            self.assertEqual(result, merge_signals(FIXTURE_ROOT, bundles, one))
            self.assertEqual({str(p.relative_to(one)): p.read_bytes() for p in one.rglob("*") if p.is_file()},
                             {str(p.relative_to(two)): p.read_bytes() for p in two.rglob("*") if p.is_file()})
            for old in RoleCatalogStore.fixtures().list_roles():
                new = RoleCatalogStore(one).role(old["id"])
                RoleRecord.from_dict(new)
                for field in ("demand_window", "industries", "skill_map"):
                    self.assertEqual(new[field], old[field])
                self.assertGreater(len(new["foundation"]["sources"]), len(old["foundation"]["sources"]))
                self.assertTrue(all(s.get("retrieved_at") == STAMP for s in new["foundation"]["sources"][len(old["foundation"]["sources"]):]))
            self.assertEqual(before, {p.name: p.read_bytes() for p in FIXTURE_ROOT.glob("*.json")})

    def test_merge_sample_research_and_bad_bindings_metadata_and_paths_rejected(self):
        bundle = sample_import("hot-technologies")
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "roles"
            with self.assertRaisesRegex(ValueError, "sample"):
                merge_signals(ROOT / "research/role-atlas/m4/roles", [bundle], output)
            signal_id = bundle["records"][0]["id"]
            for bindings in ([{"signal_id": signal_id, "role_id": "role.missing", "skill_id": "skill.missing", "confidence": "close", "rationale": "Sample explicit test."}],
                             [{"signal_id": signal_id, "role_id": "role.account-executive", "skill_id": "skill.prosp", "confidence": "close", "rationale": " "}]):
                with self.assertRaises(ValueError):
                    merge_signals(FIXTURE_ROOT, [bundle], output, bindings=bindings)
            bad = copy.deepcopy(bundle)
            bad["records"][0]["signal"]["timing"] = "leading"
            with self.assertRaisesRegex(ValueError, "id/content"):
                merge_signals(FIXTURE_ROOT, [bad], output)
            with self.assertRaises(ValueError):
                merge_signals(FIXTURE_ROOT, [bundle], ROOT / "preview/release/new-m5")
            link = Path(d) / "escape"
            link.symlink_to(ROOT / "preview/release", target_is_directory=True)
            with self.assertRaises(ValueError):
                merge_signals(FIXTURE_ROOT, [bundle], link / "new-m5")
            for target in (FIXTURE_ROOT, FIXTURE_ROOT / "child"):
                with self.assertRaises(ValueError):
                    merge_signals(FIXTURE_ROOT, [bundle], target)

    def test_explicit_skill_bindings_reuse_status_engine_no_presence_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "input"
            root.mkdir()
            role = RoleCatalogStore.fixtures().role("forward-deployed-engineer")
            skill = next(s for s in role["skill_map"]["skills"] if s["status"] == "rising")
            skill["evidence"] = [s for s in skill["evidence"] if s["family"] == "P"]
            skill["source_links"] = ["P"]
            skill["status"] = "early"
            (root / "role.json").write_text(RoleRecord.from_dict(role).to_json())
            def bind(bundle):
                row = next(r for r in bundle["records"] if r["soc"].split(".")[0] == "15-1252")
                return [{"signal_id": row["id"], "role_id": role["id"], "skill_id": skill["id"], "confidence": "close", "rationale": "Synthetic binding for status-engine boundary test, not a real equivalence."}]
            for kind, expected in (("hot-technologies", "early"), ("emerging-tasks", "rising")):
                bundle = sample_import(kind)
                output = Path(d) / kind
                merge_signals(root, [bundle], output, bindings=bind(bundle))
                actual = next(s for s in RoleCatalogStore(output).role(role["id"])["skill_map"]["skills"] if s["id"] == skill["id"])
                self.assertEqual(actual["status"], expected)
                self.assertEqual(actual["label"], skill["label"])
            emerging = sample_import("emerging-tasks")
            exact = bind(emerging)
            exact[0]["confidence"] = "exact"
            merge_signals(root, [emerging], Path(d) / "exact", bindings=exact)
            self.assertEqual(next(s for s in RoleCatalogStore(Path(d) / "exact").role(role["id"])["skill_map"]["skills"] if s["id"] == skill["id"])["status"], "rising")
            invalid = bind(emerging)
            invalid[0]["confidence"] = "guess"
            with self.assertRaisesRegex(ValueError, "confidence"):
                merge_signals(root, [emerging], Path(d) / "invalid-confidence", bindings=invalid)
            loose = bind(emerging)
            loose[0]["confidence"] = "loose"
            merge_signals(root, [emerging], Path(d) / "loose", bindings=loose)
            actual = next(s for s in RoleCatalogStore(Path(d) / "loose").role(role["id"])["skill_map"]["skills"] if s["id"] == skill["id"])
            self.assertEqual(actual["status"], "early")
            self.assertEqual(actual["source_links"], ["P"])
            bls = sample_import("bls-industry")
            with self.assertRaisesRegex(ValueError, "context"):
                merge_signals(root, [bls], Path(d) / "bad", bindings=bind(bls))
            future = copy.deepcopy(sample_import("emerging-tasks"))
            future["retrieved_at"] = "2026-09-30T23:30:00-02:00"
            row = future["records"][0]
            row["signal"]["retrieved_at"] = future["retrieved_at"]
            row["id"] = "signal." + digest({k: v for k, v in row.items() if k != "id"})[:24]
            with self.assertRaisesRegex(ValueError, "after"):
                merge_signals(root, [future], Path(d) / "future")

    def test_retrieval_metadata_roundtrip_and_bad_timing_or_calendar_rejected(self):
        record = RoleCatalogStore.fixtures().role("account-executive")
        signal = record["foundation"]["sources"][0]
        signal["retrieved_at"] = STAMP
        self.assertEqual(RoleRecord.from_dict(record).to_dict(), record)
        for field, value in (("retrieved_at", "2026-02-30T00:00:00Z"), ("retrieved_at", "2026-09-01"), ("timing", "leading"), ("family", "M")):
            bad = copy.deepcopy(record)
            bad["foundation"]["sources"][0][field] = value
            with self.assertRaises(RoleValidationError):
                RoleRecord.from_dict(bad)
        original = RoleCatalogStore.fixtures().role("account-executive")
        self.assertEqual(RoleRecord.from_dict(original).to_dict(), original)
        self.assertNotIn("retrieved_at", original["foundation"]["sources"][0])

    def test_artifact_writer_never_replaces_inputs_or_different_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "artifact.json"
            write_json(path, {"sample": True})
            write_json(path, {"sample": True})
            with self.assertRaises(ValueError):
                write_json(path, {"sample": False})
            with self.assertRaises(ValueError):
                write_json(ROOT / "preview/release/current.json", {})
            with self.assertRaises(ValueError):
                write_json(path, {}, replace=True, inputs=(path,))


if __name__ == "__main__":
    unittest.main()
