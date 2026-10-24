"""M8-r1 lossless storage, lazy read parity, fail-closed pins and budget gates."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from skills_vector.market.api import handle_request
from skills_vector.market.core import CatalogStore
from skills_vector.market.role_artifacts import digest, load_roles, read_json
from skills_vector.market.role_ledger import read_ledger, track_record, validate_ledger, append_ledger, backtest_ledger, resolve_ledger
from skills_vector.market.role_ledger_storage import pack, unpack
from skills_vector.market.role_overview import overview
from skills_vector.market.role_publication import read_audit, publish
from skills_vector.market.role_reads import RoleAtlasReads
from skills_vector.market.web import create_market_app

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'research/role-atlas/2026-Q4'


class WeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_roles(RELEASE / 'roles')
        cls.reads = RoleAtlasReads.configured(RELEASE / 'roles', ledger_path=RELEASE / 'ledger.json')

    def test_lossless_compact_ledger_size_id_math_and_rejections(self):
        # The issued M8 calls remain byte-for-value identical to their original history.
        original = read_ledger(RELEASE / 'ledger.json')
        self.assertEqual(digest(original), '1cd8855c212fb4e969d754c4b3ccd449dbc6e58ceac0332b2f93d56247f5e692')
        self.assertEqual(unpack(pack(original)), original)
        stored = read_json(RELEASE / 'ledger.json')
        self.assertEqual(unpack(stored), original)
        self.assertEqual(read_ledger(RELEASE / 'ledger.json'), original)
        self.assertLessEqual((RELEASE / 'ledger.json').stat().st_size, 300_000)
        self.assertEqual(track_record(self.records, stored), track_record(self.records, original))
        self.assertEqual(append_ledger(stored, original), original)
        self.assertEqual(backtest_ledger(stored, None), backtest_ledger(original, None))
        self.assertEqual(resolve_ledger(stored, []), resolve_ledger(original, []))
        for mutate in ('status', 'id', 'source', 'forecast'):
            bad = deepcopy(stored)
            if mutate == 'status': bad['pending'][0]['calls'][0][4] = 'resolved'
            if mutate == 'id': bad['pending'][0]['calls'][0][0] = 'call.changed'
            if mutate == 'source': bad['pending'][0]['shared']['source_sha256'] = 'bad'
            if mutate == 'forecast': bad['pending'][0]['shared']['forecast']['probability'] = .5
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): validate_ledger(bad)
        changed = deepcopy(self.records); changed[0]['label'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'source record mismatch'): track_record(changed, stored)
        for index in (0, -1):
            bad = deepcopy(original); bad['entries'][index]['forecast']['id'] = '  '
            with self.assertRaises(ValueError): validate_ledger(bad)
        bad = deepcopy(original); bad['entries'][-1]['forecast']['claim'] = ''
        with self.assertRaises(ValueError): validate_ledger(bad)

    def test_overview_exact_tails_deferred_expectations_and_full_skill(self):
        for record in self.records:
            role = record['id']
            full = self.reads.role(role)
            self.assertEqual(full, self.reads.role(role, view='full'))
            light = self.reads.role(role, view='overview')
            self.assertEqual({k: light[k] for k in ('role', 'overview', 'view')}, overview(record))
            self.assertEqual(light['track_record'], full['track_record'])
            common = {s['id']: s for s in light['role']['skill_map']['skills']}
            tail = {s['id']: s for g in light['overview']['tails'] for s in g['skills']}
            self.assertEqual(set(common) | set(tail), {s['id'] for s in record['skill_map']['skills']})
            self.assertFalse(set(common) & set(tail))
            self.assertTrue(all(set(s) == {'id', 'label'} for s in tail.values()))
            for s in record['skill_map']['skills']:
                if s['id'] in common: self.assertEqual(s, common[s['id']])
            for sid in [*common][:1] + [*tail][:1]:
                detail = self.reads.skill(role, sid, record['release_id'])
                self.assertEqual(detail['skill'], next(s for s in record['skill_map']['skills'] if s['id'] == sid))
            light['role']['label'] = 'mutated'
            self.assertEqual(self.reads.role(role, view='overview')['role']['label'], record['label'])
        response = handle_request('GET', '/v1/roles/account-executive?view=overview', role_catalog=self.reads)
        self.assertLessEqual(len(response.body), 400_000)
        self.assertLessEqual(sum(p.stat().st_size for p in RELEASE.rglob('*') if p.is_file()), 6_000_000)
        self.assertFalse((RELEASE / 'audit.json').exists())
        self.assertLess((RELEASE / 'release.json').stat().st_size, 32_000)
        for path in ('/v1/roles/account-executive?view=bad', '/v1/roles/account-executive?view=overview&view=full'):
            self.assertEqual(handle_request('GET', path, role_catalog=self.reads).status, 400)
        self.assertEqual(self.reads.skill('account-executive', 'missing')['status'], 'unknown_skill')
        self.assertEqual(self.reads.skill('account-executive', 'missing', 'unknown')['status'], 'unknown_release')
        self.assertEqual(RoleAtlasReads.configured().skill('account-executive', 'missing')['status'], 'role_catalog_unavailable')
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / 'ledger.json'; bad.write_text('[]')
            reads = RoleAtlasReads.configured(RELEASE / 'roles', ledger_path=bad)
            self.assertEqual(reads.role('account-executive', view='overview')['status'], 'role_catalog_unavailable')

    def test_manifest_audit_missing_present_corrupt_and_deterministic_publication(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); capture = root / 'capture'; capture.mkdir()
            (capture / 'manifest.json').write_text('{"snapshot_id":"authored-test-capture"}')
            record = RoleAtlasReads.configured(fixtures=True).role('account-executive')['role']
            audit = {'release_id': record['release_id'], 'input_release_id': 'authored-test-capture', 'recipe': 'authored-test', 'input_hashes': {}}
            evidence = root / 'evidence'
            for out in (root / 'one', root / 'two'): publish([record], audit, (capture,), out, evidence)
            for p in (root / 'one').rglob('*.json'): self.assertEqual(p.read_bytes(), (root / 'two' / p.relative_to(root / 'one')).read_bytes())
            self.assertEqual(read_audit(root / 'one', evidence), (audit, 'audit verified locally'))
            self.assertEqual(read_audit(root / 'one', root / 'absent'), (None, 'audit not available locally'))
            audit_path = evidence / record['release_id'] / 'audit.json'; audit_path.write_bytes(audit_path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'hash/size'): read_audit(root / 'one', evidence)
            with self.assertRaisesRegex(ValueError, 'outside the repository'): publish([record], audit, (capture,), root / 'unused', ROOT / 'not-written')

    def test_light_view_binds_source_and_view_bytes_and_selection(self):
        for target in ('roles/account-executive.json', 'views/account-executive.json'):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / 'release'; shutil.copytree(RELEASE, root)
                reads = RoleAtlasReads.configured(root / 'roles')
                self.assertEqual(reads.role('account-executive', view='overview')['status'], 'ok')
                path = root / target; path.write_bytes(path.read_bytes()+b' ')
                self.assertEqual(reads.role('account-executive', view='overview')['status'], 'role_catalog_unavailable')

    def test_checker_budgets_reject_any_overrun_missing_nan_or_negative(self):
        tree = ast.parse((ROOT / 'scripts/check_role_atlas.py').read_text())
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('require', 'enforce_budget')]
        ns = {}; exec(compile(ast.Module(body=selected, type_ignores=[]), '<budget-functions>', 'exec'), ns)
        for limit in (400_000, 2_000, 1_000):
            ns['enforce_budget']('test', limit, limit)
            for value in (limit+1, None, float('nan'), float('inf'), -1):
                with self.assertRaises(AssertionError): ns['enforce_budget']('test', value, limit)

    def test_research_sdk_overview_skill_and_errors_have_rest_parity(self):
        record = self.records[0]; role = record['id']; sid = record['skill_map']['skills'][-1]['id']; pin = record['release_id']
        pairs = [(f'/v1/roles/{role}?view=overview&release={pin}', 'get_role', {'role': role, 'view': 'overview', 'release_id': pin}),
                 (f'/v1/roles/{role}/skills/{sid}?release={pin}', 'get_role_skill', {'role': role, 'skill_id': sid, 'release_id': pin}),
                 (f'/v1/roles/{role}/skills/missing', 'get_role_skill', {'role': role, 'skill_id': 'missing'}),
                 (f'/v1/roles/{role}/skills/{sid}?release=missing', 'get_role_skill', {'role': role, 'skill_id': sid, 'release_id': 'missing'})]
        async def run():
            with tempfile.TemporaryDirectory() as d:
                app = create_market_app(CatalogStore(Path(d)), role_catalog=self.reads)
                async with app.router.lifespan_context(app), httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url='http://127.0.0.1:8000') as http:
                    async with streamable_http_client('http://127.0.0.1:8000/api/mcp', http_client=http) as (incoming, outgoing):
                        async with ClientSession(incoming, outgoing) as session:
                            await session.initialize()
                            for path, tool, args in pairs:
                                response = await http.get(path); result = await session.call_tool(tool, args)
                                self.assertEqual(response.json(), result.structured_content)
        anyio.run(run)


if __name__ == '__main__': unittest.main()
