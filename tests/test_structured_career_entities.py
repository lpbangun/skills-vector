"""M9-r3: tenant legal entities are provenance, not brand identity."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from skills_vector.market.posting_volume import VolumePolicy, write_snapshot
from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.roster_probe import probe
from skills_vector.market.structured_career import (
    job_nodes, local_transport, normalize_node, validate_board,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/samples/jsonld"
STAMP = "2026-10-15T00:00:00Z"
BOARD = {
    "ats": "jsonld", "employer": "Authored",
    "origin": "https://authored.myworkdayjobs.com",
    "note": "Authored employer-owned Workday tenant.",
    "organization_rule": "tenant_entity",
}
OTHER = {"ats": "greenhouse", "employer": "Other Employer", "employer_aliases": ["Rival"]}
ENTITY = "020 Example Indemnity Co"


def fixture(name):
    return job_nodes((SAMPLES / (name + ".sample.html")).read_text())[0]


class TenantEntityTests(unittest.TestCase):
    def normalize(self, node, board=BOARD, roster=(BOARD, OTHER)):
        return normalize_node(node, board["origin"] + "/HR-Generalist", board, STAMP, roster=roster)

    def test_rule_validation_default_workday_and_explicit_exception(self):
        validate_board(BOARD)
        invalid = read_json(SAMPLES / "tenant-invalid-board.sample.json")
        with self.assertRaisesRegex(ValueError, "tenant_entity requires"):
            validate_board(invalid)
        for host in ("myworkdayjobs.com", "fake.myworkdayjobs.com.example.org"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "tenant_entity requires"):
                validate_board({**BOARD, "origin": "https://" + host})
        validate_board({**invalid, "tenant_entity_note": "Reviewed synthetic provider entity exception."})
        for note in (None, "", " ", 42):
            with self.subTest(note=note), self.assertRaisesRegex(ValueError, "tenant_entity requires"):
                validate_board({**invalid, "tenant_entity_note": note})
        for rule in ("unknown", None, []):
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, "unknown organization_rule"):
                validate_board({**BOARD, "organization_rule": rule})
        default = {k: v for k, v in invalid.items() if k != "organization_rule"}
        validate_board(default)
        for board in (default, {**default, "organization_rule": "name"}):
            for fixture_name in ("tenant-entity", "tenant-missing-entity"):
                self.assertEqual(self.normalize(fixture(fixture_name), board), (None, "organization_mismatch"))

    def test_workday_entity_and_missing_entity_admitted_without_aliases(self):
        for name, expected in (("tenant-entity", ENTITY), ("tenant-missing-entity", None)):
            node = fixture(name)
            admitted, reason = self.normalize(node)
            self.assertIsNone(reason)
            self.assertEqual(admitted["hiring_entity"], expected)
            self.assertEqual(admitted["hiringOrganization"]["name"], expected)
            self.assertEqual(admitted["description"], node["description"])
        empty = fixture("tenant-missing-entity")
        empty["hiringOrganization"] = {"name": ""}
        self.assertEqual(self.normalize(empty)[0]["hiring_entity"], "")
        for org in (None, {}, {"name": None}):
            with self.subTest(org=org):
                self.assertIsNone(self.normalize({**empty, "hiringOrganization": org})[0]["hiring_entity"])
        foreign = fixture("tenant-entity")
        foreign["jobLocation"]["address"]["addressCountry"] = "CA"
        self.assertEqual(self.normalize(foreign), (None, "not_explicit_US"))

    def test_other_roster_name_or_alias_rejected_but_own_alias_allowed(self):
        node = fixture("tenant-other-employer")
        for entity in (node["hiringOrganization"]["name"], "100 Other Employer, Inc."):
            node["hiringOrganization"]["name"] = entity
            self.assertEqual(self.normalize(node), (None, "organization_other_roster_employer"))
        own = {**BOARD, "employer_aliases": ["Rival"]}
        node = fixture("tenant-other-employer")
        self.assertIsNotNone(self.normalize(node, own, (own,))[0])
        # Another board for the same roster employer is not a different employer.
        self.assertIsNotNone(self.normalize(node, own, (own, {**own, "ats": "greenhouse"}))[0])
        # A conflicting alias on a genuinely different employer still fails closed.
        self.assertEqual(self.normalize(node, own), (None, "organization_other_roster_employer"))
        # Do not merge distinct roster identities merely because legal-name normalization agrees.
        node["hiringOrganization"]["name"] = "100 Authored, Inc."
        distinct = {"employer": "Authored Inc.", "employer_aliases": []}
        self.assertEqual(self.normalize(node, roster=(BOARD, distinct)),
                         (None, "organization_other_roster_employer"))
        node = fixture("tenant-other-employer")
        allowed = {**read_json(SAMPLES / "tenant-invalid-board.sample.json"),
                   "tenant_entity_note": "Reviewed synthetic provider entity exception."}
        self.assertIsNotNone(self.normalize(fixture("tenant-entity"), allowed)[0])
        self.assertEqual(self.normalize(node, allowed), (None, "organization_other_roster_employer"))

    def test_capture_probe_counts_raw_entities_and_replay(self):
        value = read_json(SAMPLES / "policy.sample.json")
        for board in value["boards"]:
            board.update(organization_rule="tenant_entity",
                         tenant_entity_note="Reviewed authored non-Workday fixture exception.")
        value["boards"][2]["employer_aliases"] = ["Rival"]
        policy = VolumePolicy.from_value(value, SAMPLES / "policy.sample.json")
        base = {b["origin"].rstrip("/"): local_transport(SAMPLES, b["payload"])
                for b in policy.value["boards"]}
        probe_entity = [None]

        def transport(url):
            origin = next(o for o in base if url.startswith(o + "/"))
            status, headers, body = base[origin](url)
            if "/careers/" in url:
                node = deepcopy(job_nodes(body.decode())[0])
                if "/HR-Generalist" in url:
                    node["hiringOrganization"] = {"name": ENTITY}
                    nodes = [node]
                    if origin.endswith("careers0.example.org"):
                        nodes.extend([deepcopy(node), fixture("tenant-other-employer")])
                else:
                    node.pop("hiringOrganization")
                    if probe_entity[0] is not None:
                        node["hiringOrganization"] = {"name": probe_entity[0]}
                    nodes = [node]
                body = ('<script type="application/ld+json">' + json.dumps(nodes) + '</script>').encode()
            return status, headers, body

        with tempfile.TemporaryDirectory() as d, patch.object(
                socket.socket, "connect", side_effect=AssertionError("network forbidden")):
            root = Path(d)
            write_snapshot(policy, root / "capture", "2026-Q4", payload_root=SAMPLES,
                           retrieved_at=STAMP, transport=transport)
            snapshot = root / "capture/2026-Q4"
            manifest = read_json(snapshot / "manifest.json")
            self.assertEqual(sum(r["admitted"] for r in manifest["roles"].values()), 10)
            first = next(b["structured"] for b in manifest["boards"] if b["employer"] == "Authored Employer 0")
            self.assertEqual(first["counts"]["organization_other_roster_employer"], 1)
            self.assertEqual(first["counts"]["duplicate_jobposting"], 1)
            self.assertEqual(first["hiring_entity_counts"], [
                {"hiring_entity": None, "count": 1}, {"hiring_entity": ENTITY, "count": 2},
                {"hiring_entity": "100 Rival, Inc.", "count": 1},
            ])
            rows = read_json(snapshot / "postings.json")
            self.assertEqual(len(rows), 10)
            for row in rows:
                self.assertEqual(row["hiring_entity"], ENTITY if row["occupation_slug"] == "hr-generalist" else None)
                self.assertIn(row["employer"], {b["employer"] for b in value["boards"]})
            inputs = EvidenceInputs.from_snapshots([snapshot], ROOT / "preview/release")
            inputs.write(root / "a")
            inputs.write(root / "b")
            for path in (root / "a").rglob("*.json"):
                self.assertEqual(path.read_bytes(), (root / "b" / path.relative_to(root / "a")).read_bytes())
            candidates = root / "policy.json"
            candidates.write_text(json.dumps(value))
            for entity in (None, ENTITY, "100 Rival, Inc."):
                probe_entity[0] = entity
                draft = probe(candidates, root / ("probe-" + str(entity is None) + str(entity == ENTITY) + ".json"),
                              payload_root=SAMPLES, transport=transport)
                self.assertFalse(draft["reviewed"])
                for board in draft["boards"]:
                    self.assertFalse(board["reviewed"])
                    self.assertEqual(board["counts"]["fetched_pages"], 1)
                    self.assertEqual(board["hiring_entity"], entity)
                    self.assertEqual(board["hiring_entity_counts"], [{"hiring_entity": entity, "count": 1}])
                    if entity == "100 Rival, Inc." and board["employer"] != "Authored Employer 2":
                        self.assertEqual(board["counts"]["organization_other_roster_employer"], 1)


if __name__ == "__main__":
    unittest.main()
