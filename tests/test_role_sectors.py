"""Local primary-sector evidence, reviewed ambiguity and floor boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_sectors import BLOCKED, MAPPINGS, concordance, import_sectors, sic_rule, validate_sector


class SectorTests(unittest.TestCase):
    def test_checked_registry_proof_and_blocked_employers(self):
        table = read_json(MAPPINGS / 'sic_sectors.json')
        rows = read_json(MAPPINGS / 'employers.json')
        self.assertEqual(sum(r['status']=='verified' for r in rows), 26)
        for row in rows:
            if row['status']=='verified': validate_sector(row, table)
            if row['employer'].casefold() in BLOCKED or row.get('sic') in ('7389','7340'):
                self.assertEqual(row['status'], 'unverified'); self.assertIsNone(row['naics'])
        row = next(r for r in rows if r['status']=='verified')
        for key,value in [('cik','0000000000'),('naics','99'),('source','job_text'),('submission_sha256','bad')]:
            bad=copy.deepcopy(row);bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_sector(bad,table)
        bad=copy.deepcopy(row);bad.pop('rule')
        with self.assertRaises(ValueError):validate_sector(bad,table)

    def test_sector_release_actual_counts_profiles_and_unverified_denominators(self):
        from skills_vector.market.role_artifacts import load_roles
        root=Path(__file__).resolve().parents[1]/'research/role-atlas/2026-Q4-sectors'
        records=load_roles(root/'roles')
        expected={'role.account-executive':{'51':(205,12,True),'52':(1,1,False),'54':(42,1,False)},
                  'role.forward-deployed-engineer':{'51':(5,4,False),'52':(3,2,False)},
                  'role.growth-manager':{'51':(3,3,False)},'role.hr-generalist':{'51':(1,1,False)}}
        for r in records:
            seen={i['naics']:(i['evidence']['postings'],i['evidence']['employers'],i['profile'] is not None) for i in r['industries'] if i['evidence']['postings']}
            self.assertEqual(seen,expected[r['id']])
            unknown=next(o for o in r['observations'] if o['id']=='sector.unverified')['windows'][0]
            self.assertEqual(sum(i['evidence']['postings'] for i in r['industries'])+unknown['mentions'],r['demand_window']['postings'])
            for i in r['industries']:
                if i['profile'] is not None:
                    self.assertGreaterEqual(i['evidence']['postings'],30);self.assertGreaterEqual(i['evidence']['employers'],8)
                    self.assertEqual(i['profile']['domain_knowledge'],[])
                else:self.assertTrue(i['reason'])
            self.assertTrue(all(s['forecast']['basis']=='none' for s in r['skill_map']['skills']))

    def test_unambiguous_broad_ranges_and_reviewed_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'c.csv';p.write_text('SIC,SIC Title (and note),2002 NAICS,2002 NAICS Title\n3949,Test,339920,Test\n7372,Test,511210,Test\n7372,Test,334611,Test\n9999,Test,441110,Test\n9999,Test,451110,Test\n8888,Test,511210,Test\n8888,Test,541512,Test\n,,,\n')
            c=concordance(p)
            self.assertEqual(sic_rule('3949',c)['naics'],'31–33')
            self.assertEqual(sic_rule('9999',c)['naics'],'44–45')
            self.assertEqual(sic_rule('7372',c)['kind'],'reviewed_decision')
            self.assertEqual(sic_rule('7372',c)['naics'],'51')
            self.assertIsNone(sic_rule('8888',c)['naics'])
            self.assertIsNone(sic_rule('7777',c)['naics'])
            self.assertEqual(sic_rule('5500',c)['naics'],'44–45')
            for sic in ('7389','7340'):self.assertIsNone(sic_rule(sic,c)['naics'])

    def test_import_whitelist_native_binding_and_determinism(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);sec=root/'sec';(sec/'submissions').mkdir(parents=True);maps=root/'maps';maps.mkdir()
            (maps/'employers.json').write_text('[]');roster=root/'roster.json';roster.write_text('{"boards":[]}')
            csv=root/'c.csv';csv.write_text('SIC,SIC Title (and note),2002 NAICS,2002 NAICS Title\n7374,Data,518210,Data\n')
            native={'cik':'0000000001','sic':'7374','sicDescription':'Data','name':'Authored Corporation','ignored_private_field':'not exported'}
            p=sec/'submissions/CIK0000000001.json';p.write_text(json.dumps(native))
            index={'retrieved':'2026-10-24','ignored_transport_metadata':'not exported','rows':[{'employer':'Authored','registrant':'Authored Corporation','cik':'0000000001','sic':'7374','sic_description':'Data','source':'https://data.sec.gov/submissions/CIK0000000001.json'}]}
            (sec/'employer_sic.json').write_text(json.dumps(index))
            self.assertEqual(import_sectors(sec,csv,maps,roster)['verified'],1)
            before=(maps/'employers.json').read_bytes();import_sectors(sec,csv,maps,roster);self.assertEqual(before,(maps/'employers.json').read_bytes())
            self.assertNotIn('ignored_private_field',before.decode());self.assertNotIn('ignored_transport_metadata',(maps/'sic_sectors.json').read_text())
            native['sic']='7389';p.write_text(json.dumps(native))
            with self.assertRaisesRegex(ValueError,'submission/index'):import_sectors(sec,csv,maps,roster)
            self.assertEqual(before,(maps/'employers.json').read_bytes())


if __name__=='__main__':unittest.main()
