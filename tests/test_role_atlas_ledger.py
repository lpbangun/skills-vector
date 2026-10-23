"""Synthetic research-shaped snapshots in tempdirs; hand-computed math, never market findings."""
from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from skills_vector.market.core import CatalogStore
from skills_vector.market.role_artifacts import canonical, read_json, write_json
from skills_vector.market.role_atlas import FIXTURE_ROOT, RoleCatalogStore, RoleRecord
from skills_vector.market.role_ledger import (NO_ARCHIVE, append_ledger, backtest_ledger, create_ledger,
                                            read_ledger, resolve_ledger, score_pairs, track_record, validate_ledger)
from skills_vector.market.role_reads import RoleAtlasReads, role_atlas_from_environment
from skills_vector.market.web import create_market_app
from skills_vector.market_cli import run

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research/role-atlas/m4/roles"


def sample_record(window: str, share: float, *, group: str = "synthetic-sample-capture-v1") -> dict:
    """Schema-shaped sample, with explicit synthetic labels, used only in tests."""
    source = RoleCatalogStore(RESEARCH).role("account-executive")
    original_window = source["demand_window"]["id"]
    def shift(value):
        if isinstance(value, dict):
            return {k: shift(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shift(v) for v in value]
        return window if value == original_window else value
    record = shift(source)
    record["label"] = "Synthetic ledger sample — not research observations"
    record["limits"] = ["Synthetic arithmetic test only; never publish or use as research."]
    record["release_id"] = "synthetic_" + window.replace("-", "_")
    record["demand_window"].update(postings=100, employers=10, window_length="quarterly" if "Q" in window else "monthly")
    record["observations"] = []
    skill = record["skill_map"]["skills"][0]
    record["skill_map"].update(skills=[skill], asked_together=[], builds_on=[])
    skill["windows"] = [{"window": window, "share": share, "employers": 5 if share else 0,
                         "max_employer_share": 0.2 if share else 0, "comparison_group": group,
                         "levels": [1, 2, 3, 4, 5, "unassigned"], "timing": "coincident"}]
    skill["status"] = "early"
    forecast = skill["forecast"]
    forecast.update(created=window, resolves=str(int(window[:4]) + 1) + window[4:], basis="none", probability=None, base_rate=None,
                    claim="In the resolving window, at least 20% of this fixed recorded scope mentions the sample skill.",
                    scored_against="Synthetic fixed-scope postings, arithmetic tests only.", reason="Synthetic test: no estimated probability.")
    return RoleRecord.from_dict(record).to_dict()


def sample_event(record: dict, *, kind: str = "share_at_least", threshold: float = 0.2) -> list[dict]:
    skill = record["skill_map"]["skills"][0]
    return [{"role_id": record["id"], "skill_id": skill["id"], "claim": skill["forecast"]["claim"],
             "event": {"kind": kind, "threshold": threshold},
             "rationale": "Synthetic exact-claim binding, not prose inference or research."}]


def archive_sample(path: Path, *, monthly: bool = False) -> None:
    # Three matured 12-month pairs: outcomes [0,1,1]. Predictions [0,1/2]
    # at the latter two origins, from strictly earlier targets: Brier=(1+1/4)/2=5/8.
    rows = [("2020-Q1", 0.0), ("2021-Q1", 0.1), ("2021-Q2", 0.1),
            ("2022-Q2", 0.3), ("2022-Q3", 0.1), ("2023-Q3", 0.4)]
    path.mkdir()
    for window, share in rows:
        if monthly:
            window = window[:5] + {"Q1": "01", "Q2": "04", "Q3": "07"}[window[5:]]
        record = sample_record(window, share)
        (path / (record["release_id"] + ".json")).write_text(canonical(record))


class LedgerMathTests(unittest.TestCase):
    def test_brier_and_calibration_hand_values_and_endpoints(self):
        report = score_pairs([(0.8, 1), (0.4, 0)])
        self.assertEqual((report["scored_count"], report["brier_score"]), (2, 0.1))  # (.04+.16)/2
        self.assertEqual(report["calibration_bins"][8], {"lower": 0.8, "upper": 0.9, "midpoint": 0.85, "n": 1, "mean_probability": 0.8, "observed_rate": 1.0})
        report = score_pairs([(0, 0), (0.1, 1), (1, 1), (0.19, 0)])
        self.assertEqual([report["calibration_bins"][i]["n"] for i in (0, 1, 9)], [1, 2, 1])
        self.assertEqual(report["calibration_bins"][1]["mean_probability"], 0.145)
        self.assertEqual(report["calibration_bins"][1]["observed_rate"], 0.5)
        self.assertEqual(score_pairs([]), {"scored_count": 0, "brier_score": None, "calibration_bins": []})
        for values in ([(None, 1)], [(-0.01, 0)], [(1.01, 0)], [(float("nan"), 1)], [(float("inf"), 0)], [(0.8, 2)], [(True, 1)], [(0.2, True)]):
            with self.assertRaises(ValueError):
                score_pairs(values)

    def test_research_always_starts_none_and_bad_event_bindings_rejected(self):
        record = sample_record("2024-Q1", 0.2)
        record["skill_map"]["skills"][0]["forecast"].update(basis="backtest", probability=0.9, base_rate=0.8, reason=None)
        ledger = create_ledger([record], sample_event(record))
        forecast = ledger["entries"][0]["forecast"]
        self.assertEqual((forecast["basis"], forecast["probability"], forecast["base_rate"]), ("none", None, None))
        self.assertTrue(forecast["reason"])
        for change in ({"claim": "different claim"}, {"event": {"kind": "guess", "threshold": 0.2}},
                       {"event": {"kind": "share_at_least", "threshold": 1.1}}, {"rationale": " "}, {"skill_id": "missing"}):
            spec = sample_event(record)
            spec[0].update(change)
            with self.assertRaises(ValueError):
                create_ledger([record], spec)

    def test_none_resolves_but_is_never_scored_even_at_threshold(self):
        origin, later = sample_record("2024-Q1", 0.1), sample_record("2025-Q1", 0.2)
        ledger = create_ledger([origin], sample_event(origin))
        resolved = resolve_ledger(ledger, [later])["ledger"]
        self.assertEqual(resolved["entries"][0]["resolution"]["outcome"], 1)
        stats = track_record([origin], resolved)
        self.assertEqual((stats["resolved_count"], stats["unscored_count"], stats["scored_count"]), (1, 1, 0))
        self.assertIsNone(stats["brier_score"])
        self.assertEqual(stats["calibration_bins"], [])
        below = create_ledger([origin], sample_event(origin, kind="share_below"))
        self.assertEqual(resolve_ledger(below, [later])["ledger"]["entries"][0]["resolution"]["outcome"], 0)
        later["skill_map"]["skills"][0]["windows"][-1]["share"] = 0.19
        self.assertEqual(resolve_ledger(below, [later])["ledger"]["entries"][0]["resolution"]["outcome"], 1)

    def test_resolver_rejects_missing_wrong_window_changed_scope_and_conflicts(self):
        origin = sample_record("2024-Q1", 0.1)
        ledger = create_ledger([origin], sample_event(origin))
        for later, reason in ((sample_record("2024-Q4", 0.4), "exact resolving"),
                              (sample_record("2025-Q2", 0.4), "exact resolving"),
                              (sample_record("2025-Q1", 0.4, group="changed-sampling"), "sampling group")):
            result = resolve_ledger(ledger, [later])
            self.assertIsNone(result["ledger"]["entries"][0]["resolution"])
            self.assertIn(reason, result["deferred"][0]["reason"])
        later = sample_record("2025-Q1", 0.4)
        changed_levels = copy.deepcopy(later)
        changed_levels["skill_map"]["skills"][0]["windows"][-1]["levels"] = [1]
        self.assertIn("level scope", resolve_ledger(ledger, [changed_levels])["deferred"][0]["reason"])
        empty = copy.deepcopy(later)
        empty["demand_window"].update(postings=0, employers=0)
        empty["skill_map"]["skills"][0]["windows"][-1].update(share=0, employers=0, max_employer_share=0)
        self.assertIn("no postings", resolve_ledger(ledger, [empty])["deferred"][0]["reason"])
        missing = copy.deepcopy(later)
        missing["skill_map"]["skills"] = []
        result = resolve_ledger(ledger, [missing])
        self.assertIn("absence is not", result["deferred"][0]["reason"])
        resolved = resolve_ledger(ledger, [later])["ledger"]
        self.assertEqual(resolve_ledger(resolved, [later])["ledger"], resolved)
        later["skill_map"]["skills"][0]["windows"][-1]["share"] = 0.3
        with self.assertRaisesRegex(ValueError, "immutable"):
            resolve_ledger(resolved, [later])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_ledger(ledger, [later, copy.deepcopy(later)])
        unbound = create_ledger([origin])
        self.assertIn("structured event", resolve_ledger(unbound, [later])["deferred"][0]["reason"])

    def test_causal_backtest_rate_scores_monthly_and_resolution_hand_values(self):
        for monthly in (False, True):
            with self.subTest(monthly=monthly), tempfile.TemporaryDirectory() as d:
                archive = Path(d) / "archive"
                archive_sample(archive, monthly=monthly)
                origin = sample_record("2024-01" if monthly else "2024-Q1", 0.1)
                ledger = create_ledger([origin], sample_event(origin))
                result = backtest_ledger(ledger, archive)
                self.assertEqual(result["report"]["status"], "run")
                entry = result["ledger"]["entries"][0]
                self.assertEqual((entry["forecast"]["probability"], entry["forecast"]["base_rate"]), (2 / 3, 2 / 3))
                self.assertEqual(entry["backtest"]["brier_score"], 0.625)
                self.assertEqual(entry["backtest"]["scored_count"], 2)
                self.assertEqual(len(entry["backtest"]["historical_pairs"]), 3)
                self.assertEqual(backtest_ledger(result["ledger"], archive)["ledger"], result["ledger"])
                later = sample_record("2025-01" if monthly else "2025-Q1", 0.3)
                resolved = resolve_ledger(result["ledger"], [later])["ledger"]
                stats = track_record([origin], resolved)
                self.assertAlmostEqual(stats["brier_score"], 1 / 9)
                self.assertEqual((stats["scored_count"], stats["unscored_count"]), (1, 0))
                self.assertEqual(stats["calibration_bins"][6]["observed_rate"], 1)
                self.assertEqual(track_record([origin], ledger)["brier_score"], None)
                # Never retrofit a probability after observing a resolution.
                none_resolved = resolve_ledger(ledger, [later])["ledger"]
                self.assertEqual(backtest_ledger(none_resolved, archive)["ledger"]["entries"][0]["forecast"]["basis"], "none")

    def test_no_archive_no_scores_insufficient_history_and_lookahead_rejected(self):
        record = sample_record("2024-Q1", 0.1)
        ledger = create_ledger([record], sample_event(record))
        for archive in (None, Path("/tmp/role-atlas-deliberately-absent-archive-for-test")):
            result = backtest_ledger(ledger, archive)
            self.assertEqual(result["report"]["status"], NO_ARCHIVE)
            self.assertTrue(result["report"]["reason"])
            self.assertIsNone(result["report"]["brier_score"])
            self.assertEqual(result["report"]["calibration_bins"], [])
            self.assertEqual(result["ledger"]["entries"][0]["forecast"]["basis"], "none")
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "archive"
            archive.mkdir()
            for window in ("2024-Q2", "2025-Q2"):
                (archive / (window + ".json")).write_text(canonical(sample_record(window, 0.4)))
            result = backtest_ledger(ledger, archive)
            self.assertIn("insufficient", result["report"]["status"])
            self.assertIsNone(result["report"]["brier_score"])
            self.assertEqual(track_record([record], result["ledger"])["backtest"]["status"], result["report"]["status"])
            (archive / "fixture.json").write_text(canonical(RoleCatalogStore.fixtures().list_roles()[0]))
            with self.assertRaisesRegex(ValueError, "fixture"):
                backtest_ledger(ledger, archive)

    def test_receipt_corruption_and_duplicate_archive_window_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "archive"
            archive_sample(archive)
            origin = sample_record("2024-Q1", 0.1)
            ledger = backtest_ledger(create_ledger([origin], sample_event(origin)), archive)["ledger"]
            for mutate in (lambda e: e["forecast"].update(probability=0.9),
                           lambda e: e["backtest"].update(brier_score=0.01),
                           lambda e: e["backtest"]["historical_pairs"][0].update(target="2025-Q1"),
                           lambda e: e["backtest"]["historical_pairs"][0].update(outcome=1)):
                changed = copy.deepcopy(ledger)
                mutate(changed["entries"][0])
                with self.assertRaises(ValueError):
                    validate_ledger(changed)
            future = sample_record("2025-Q1", 1.0)
            (archive / "future.json").write_text(canonical(future))
            unchanged = backtest_ledger(create_ledger([origin], sample_event(origin)), archive)["ledger"]["entries"][0]
            self.assertEqual((unchanged["forecast"]["probability"], unchanged["backtest"]["brier_score"]), (2 / 3, 0.625))
            one = next(archive.glob("*.json"))
            (archive / "duplicate.json").write_bytes(one.read_bytes())
            with self.assertRaisesRegex(ValueError, "duplicate comparable"):
                backtest_ledger(create_ledger([origin], sample_event(origin)), archive)

    def test_fixture_numeric_calls_excluded_and_append_preserves_issued_calls(self):
        fixtures = RoleCatalogStore.fixtures().list_roles()
        ledger = create_ledger(fixtures)
        self.assertEqual(len(ledger["entries"]), 9)
        self.assertEqual(track_record(fixtures, ledger)["basis_counts"]["fixture"], 9)
        self.assertIsNone(track_record(fixtures, ledger)["brier_score"])
        origin = sample_record("2024-Q1", 0.1)
        first = create_ledger([origin], sample_event(origin))
        self.assertEqual(append_ledger(first, first), first)
        later = create_ledger([sample_record("2024-Q2", 0.1)])
        self.assertEqual(len(append_ledger(first, later)["entries"]), 2)
        other = copy.deepcopy(first)
        other["entries"][0]["event"]["threshold"] = 0.3
        with self.assertRaisesRegex(ValueError, "immutable"):
            append_ledger(first, other)
        with self.assertRaises(ValueError):
            append_ledger(first, ledger)
        altered = copy.deepcopy(origin)
        altered["label"] += " altered"
        with self.assertRaisesRegex(ValueError, "source record mismatch"):
            track_record([altered], first)


class LedgerOperatorAndReadTests(unittest.TestCase):
    def test_cli_create_backtest_resolve_show_and_reproducible_files(self):
        with tempfile.TemporaryDirectory() as d:
            root, archive, roles, later_roles = Path(d), Path(d) / "archive", Path(d) / "roles", Path(d) / "later"
            roles.mkdir(); later_roles.mkdir()
            origin, later = sample_record("2024-Q1", 0.1), sample_record("2025-Q1", 0.3)
            (roles / "role.json").write_text(canonical(origin))
            (later_roles / "role.json").write_text(canonical(later))
            archive_sample(archive)
            events, ledger, report = root / "events.json", root / "ledger.json", root / "backtest.json"
            events.write_text(canonical(sample_event(origin)))
            def call(*args):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(run(["forecast-ledger", *args]), 0)
                return json.loads(out.getvalue())
            call("create", "--roles-root", str(roles), "--events", str(events), "--ledger", str(ledger))
            original = ledger.read_bytes()
            call("create", "--roles-root", str(roles), "--events", str(events), "--ledger", str(ledger))
            self.assertEqual(original, ledger.read_bytes())
            empty = call("backtest", "--ledger", str(ledger))
            self.assertEqual(empty["status"], NO_ARCHIVE)
            real = call("backtest", "--ledger", str(ledger), "--archive", str(archive), "--output", str(report))
            self.assertEqual(real["per_forecast"][0]["brier_score"], 0.625)
            self.assertEqual(read_json(report), real)
            call("resolve", "--ledger", str(ledger), "--roles-root", str(later_roles))
            stats = call("score", "--ledger", str(ledger), "--roles-root", str(roles))
            self.assertAlmostEqual(stats["brier_score"], 1 / 9)
            frozen = ledger.read_bytes()
            call("create", "--roles-root", str(roles), "--events", str(events), "--ledger", str(ledger))
            self.assertEqual(frozen, ledger.read_bytes())
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                call("create", "--roles-root", str(roles), "--ledger", str(ROOT / "preview/release/current.json"))

    def test_api_empty_selected_pinned_scores_and_corrupt_ledger_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture_file = root / "fixture-ledger.json"
            write_json(fixture_file, create_ledger(RoleCatalogStore.fixtures().list_roles()))
            fixtures = RoleAtlasReads.configured(fixtures=True, ledger_path=fixture_file)
            self.assertEqual(fixtures.track_record()["track_record"]["tracked_count"], 9)
            self.assertEqual(fixtures.role("account-executive")["track_record"]["tracked_count"], 4)
            research = RoleAtlasReads.configured(RESEARCH, ledger_path=fixture_file)
            self.assertEqual(research.track_record()["status"], "role_catalog_unavailable")
            self.assertEqual(RoleAtlasReads.configured(fixtures=True, ledger_path=root / "missing").role("account-executive")["status"], "role_catalog_unavailable")
            empty = RoleAtlasReads.configured(fixtures=True).track_record()["track_record"]
            self.assertFalse(empty["ledger_selected"])
            self.assertEqual((empty["scored_count"], empty["brier_score"], empty["calibration_bins"]), (0, None, []))
            with patch.dict(os.environ, {"SKILLS_VECTOR_ROLE_ATLAS_FIXTURES": "1", "SKILLS_VECTOR_ROLE_ATLAS_LEDGER": str(fixture_file)}, clear=True):
                self.assertEqual(role_atlas_from_environment().track_record(), fixtures.track_record())
            app = create_market_app(CatalogStore(root / "legacy"), role_catalog=fixtures)
            with TestClient(app) as client:
                payload = client.get("/v1/track-record?role=account-executive").json()
                self.assertEqual(payload, fixtures.track_record("account-executive"))
                for path, status in (("/v1/track-record?release=unknown", 404), ("/v1/track-record?role=missing", 404),
                                     ("/v1/track-record?role=x&role=y", 400)):
                    self.assertEqual(client.get(path).status_code, status)
                self.assertEqual(client.post("/v1/track-record").status_code, 405)
                broken = read_ledger(fixture_file)
                broken["entries"][0]["forecast"]["basis"] = "bogus"
                fixture_file.write_text(canonical(broken))
                self.assertEqual(client.get("/v1/track-record").status_code, 503)

    def test_resolved_research_rest_and_sdk_mcp_parity_and_release_pins(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as d:
                root, roles, archive = Path(d), Path(d) / "roles", Path(d) / "archive"
                roles.mkdir(); archive_sample(archive)
                origin, later = sample_record("2024-Q1", 0.1), sample_record("2025-Q1", 0.3)
                (roles / "role.json").write_text(canonical(origin))
                ledger = backtest_ledger(create_ledger([origin], sample_event(origin)), archive)["ledger"]
                ledger = resolve_ledger(ledger, [later])["ledger"]
                path = root / "ledger.json"
                write_json(path, ledger)
                reads = RoleAtlasReads.configured(roles, ledger_path=path)
                app = create_market_app(CatalogStore(root / "legacy"), role_catalog=reads)
                async with app.router.lifespan_context(app), httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as http:
                    async with streamable_http_client("http://127.0.0.1:8000/api/mcp", http_client=http) as (incoming, outgoing):
                        async with ClientSession(incoming, outgoing) as session:
                            await session.initialize()
                            result = await session.call_tool("get_track_record", {"role": "account-executive", "release_id": origin["release_id"]})
                            rest = (await http.get("/v1/track-record?role=account-executive&release=" + origin["release_id"])).json()
                            self.assertEqual(result.structured_content, rest)
                            self.assertAlmostEqual(rest["track_record"]["brier_score"], 1 / 9)
                            role = (await http.get("/v1/roles/account-executive?release=" + origin["release_id"])).json()
                            self.assertEqual(role["track_record"], rest["track_record"])
                            self.assertEqual(role["role"]["skill_map"]["skills"][0]["forecast"]["basis"], "none")
        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
