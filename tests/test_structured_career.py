"""Offline structured career lane: policy, politeness, provenance and replay."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from skills_vector.market.fetch import FetchResult
from skills_vector.market.posting_volume import VolumePolicy, capture, write_snapshot
from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.roster_probe import probe
from skills_vector.market.structured_career import (DENYLIST, MAX_BYTES, Robots, Session, crawl, cross_kind_dedup,
    job_nodes, local_transport, no_contacts, normalize_node, safe_url, validate_board)

ROOT=Path(__file__).resolve().parents[1]
SAMPLES=ROOT/'tests/samples/jsonld'
STAMP='2026-10-15T00:00:00Z'
ORIGIN='https://careers.example.org'
BOARD={'ats':'jsonld','employer':'Authored','origin':ORIGIN,'note':'Synthetic employer-owned career site.'}
NODE={'@type':'JobPosting','title':'HR Generalist','hiringOrganization':{'name':'Authored'},
      'description':'<h2>Responsibilities</h2><ul><li>Support onboarding under direct supervision.</li></ul>',
      'jobLocation':{'address':{'addressCountry':'US'}},'identifier':{'value':'REQ1'},'validThrough':'2027-01-01'}


def page(node=NODE): return '<script type="application/ld+json">'+json.dumps(node)+'</script>'


class Clock:
    def __init__(self):self.now=0;self.waits=[]
    def clock(self):return self.now
    def sleep(self,n):self.waits.append(n);self.now+=n


class StructuredTests(unittest.TestCase):
    def test_allowlist_ownership_aggregators_https_and_site(self):
        validate_board(BOARD)
        self.assertEqual(safe_url(ORIGIN+'/careers/HR-Generalist',ORIGIN),ORIGIN+'/careers/HR-Generalist')
        for label in read_json(DENYLIST)['labels']:
            with self.subTest(label=label),self.assertRaises(ValueError):safe_url('https://careers.'+label+'.com/job')
        for url in ('http://careers.example.org/job','https://127.0.0.1/job','https://localhost/job','https://other.example.org/job'):
            with self.subTest(url=url),self.assertRaises(ValueError):safe_url(url,ORIGIN)
        for mutate in ({'note':''},{'max_job_pages':601},{'origin':ORIGIN+'/jobs'},{'sitemap':'https://other.example.org/map.xml'}):
            with self.assertRaises(ValueError):validate_board({**BOARD,**mutate})

    def test_robots_specific_agent_wildcard_and_longest_allow(self):
        r=Robots('User-agent: *\nDisallow: /\nAllow: /careers/\nDisallow: /careers/private/*\nAllow: /careers/private/public$','RoleAtlasSynthetic')
        self.assertFalse(r.allows(ORIGIN+'/anything'));self.assertTrue(r.allows(ORIGIN+'/careers/job'))
        self.assertFalse(r.allows(ORIGIN+'/careers/private/job'));self.assertTrue(r.allows(ORIGIN+'/careers/private/public'))
        r=Robots('User-agent: *\nAllow: /\nUser-agent: RoleAtlasSynthetic\nDisallow: /careers/','RoleAtlasSynthetic/1')
        self.assertFalse(r.allows(ORIGIN+'/careers/job'));self.assertTrue(r.allows(ORIGIN+'/jobs'))
        calls=[];session=Session('RoleAtlasSynthetic',transport=lambda u:(calls.append(u) or (200,{},b'ok')),local=True)
        with self.assertRaisesRegex(ValueError,'robots'):session.fetch(ORIGIN+'/careers/job',ORIGIN,r)
        self.assertEqual(calls,[])

    def test_rate_and_same_site_redirect_rejection_before_target_request(self):
        clock=Clock();times=[]
        def get(url):times.append(clock.now);return 200,{},b'ok'
        s=Session('RoleAtlasSynthetic',transport=get,clock=clock.clock,sleep=clock.sleep)
        s.fetch(ORIGIN+'/a',ORIGIN);s.fetch(ORIGIN+'/b',ORIGIN);s.fetch(ORIGIN+'/c',ORIGIN)
        self.assertEqual(times,[0,1,2]);self.assertEqual(clock.waits,[1,1])
        calls=[]
        def redirect(url):calls.append(url);return (302,{'location':'/target'},b'') if url.endswith('/start') else (200,{},b'ok')
        s=Session('RoleAtlasSynthetic',transport=redirect,clock=clock.clock,sleep=clock.sleep)
        self.assertTrue(s.fetch(ORIGIN+'/start',ORIGIN).ok);self.assertEqual(calls,[ORIGIN+'/start',ORIGIN+'/target'])
        calls.clear();s=Session('RoleAtlasSynthetic',transport=lambda u:(calls.append(u) or (302,{'location':'https://other.example.org/target'},b'')),local=True)
        with self.assertRaisesRegex(ValueError,'roster site'):s.fetch(ORIGIN+'/start',ORIGIN)
        self.assertEqual(calls,[ORIGIN+'/start'])
        with self.assertRaises(ValueError):Session('')

    def test_size_budget_redirect_loop_and_robots_redirect(self):
        s=Session('RoleAtlasSynthetic',transport=lambda u:(200,{},b'x'*MAX_BYTES),local=True)
        self.assertEqual(len(s.fetch(ORIGIN,ORIGIN).body),MAX_BYTES)
        s=Session('RoleAtlasSynthetic',transport=lambda u:(200,{},b'x'*(MAX_BYTES+1)),local=True)
        with self.assertRaisesRegex(ValueError,'2 MB'):s.fetch(ORIGIN,ORIGIN)
        s=Session('RoleAtlasSynthetic',transport=lambda u:(302,{'location':'/loop'},b''),local=True)
        with self.assertRaisesRegex(ValueError,'redirect cap'):s.fetch(ORIGIN,ORIGIN)
        self.assertEqual(s.requests,5)
        def deny():raise ValueError('request budget exhausted')
        s=Session('RoleAtlasSynthetic',transport=lambda u:(_ for _ in ()).throw(AssertionError('must not fetch')),reserve=deny,local=True)
        self.assertFalse(s.fetch(ORIGIN,ORIGIN).ok);self.assertEqual(s.requests,0)
        robots=Robots('User-agent: *\nDisallow: /private','RoleAtlasSynthetic')
        s=Session('RoleAtlasSynthetic',transport=lambda u:(302,{'location':'/private'},b''),local=True)
        with self.assertRaisesRegex(ValueError,'robots'):s.fetch(ORIGIN+'/a',ORIGIN,robots)
        self.assertEqual(s.requests,1)

    def test_existing_transport_operator_agent_and_declared_size(self):
        from skills_vector.market.fetch import scoped_transport
        seen=[]
        class Response:
            status=200;headers={"content-length":"2"}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):seen.append(n);return b'ok'
        class Opener:
            def open(self,request,timeout):seen.append(request.get_header('User-agent'));return Response()
        with patch('skills_vector.market.fetch.urllib.request.build_opener',return_value=Opener()):
            call=scoped_transport(lambda u:None,'ConfiguredSynthetic',MAX_BYTES,20)
            self.assertEqual(call(ORIGIN)[2],b'ok');self.assertEqual(seen[0],'ConfiguredSynthetic')
            Response.headers={"content-length":str(MAX_BYTES+1)};seen.clear()
            with self.assertRaisesRegex(ValueError,'too large'):call(ORIGIN)
            self.assertEqual(seen,['ConfiguredSynthetic'])

    def test_array_graph_type_list_expiry_organization_and_us_scope(self):
        n={**NODE,'@type':['Thing','JobPosting']}
        self.assertEqual(job_nodes(page([{'@graph':[n]}])),[n]);self.assertEqual(job_nodes('<p>No structured job.</p>'),[])
        self.assertEqual(job_nodes('<script type="application/ld+json">broken</script>'),[])
        good,why=normalize_node(n,ORIGIN+'/careers/HR-Generalist',BOARD,STAMP)
        self.assertIsNone(why);self.assertEqual(good['description'],NODE['description']);self.assertEqual(good['identifier'],'REQ1');self.assertEqual(len(good['id']),24)
        other={**n,'jobLocation':{'address':{'addressLocality':'Austin','addressRegion':'TX','addressCountry':'US'}}}
        self.assertNotEqual(good['id'],normalize_node(other,ORIGIN+'/careers/HR-Generalist',BOARD,STAMP)[0]['id'])
        for change,reason in [({'validThrough':'2026-09-30'},'expired'),({'validThrough':'bad'},'invalid_expiry'),({'description':''},'missing_description_or_title'),({'hiringOrganization':{'name':'Different Employer'}},'organization_mismatch'),({'jobLocation':{'address':{'addressCountry':'CA'}}},'not_explicit_US')]:
            item,why=normalize_node({**n,**change},ORIGIN+'/careers/HR-Generalist',BOARD,STAMP)
            self.assertIsNone(item);self.assertEqual(why,reason)
        remote={**NODE,'jobLocation':{},'jobLocationType':'TELECOMMUTE','applicantLocationRequirements':{'@type':'Country','name':'United States'}}
        self.assertIsNotNone(normalize_node(remote,ORIGIN+'/careers/HR-Generalist',BOARD,STAMP)[0])
        private={**NODE,'description':'Reach '+ 'synthetic'+chr(64)+'example.invalid'}
        self.assertEqual(normalize_node(private,ORIGIN+'/careers/HR-Generalist',BOARD,STAMP)[1],'non_data_content_omitted')

    def crawler(self, maps, maximum=600):
        requests=[]
        class Saved:
            user_agent='RoleAtlasSynthetic'
            def fetch(self,url,origin,robots=None):
                requests.append(url);data=maps.get(url,b'')
                return FetchResult(url=url,status=200,body=data,sha256=hashlib.sha256(data).hexdigest())
        board={**BOARD,'sitemap':ORIGIN+'/index.xml','max_job_pages':maximum}
        result,summary=crawl(board,{'hr-generalist':['HR Generalist']},Saved(),STAMP)
        return result,summary,requests

    def test_sitemap_prefilter_page_cap_and_depth(self):
        urls=[ORIGIN+'/careers/HR-Generalist_'+str(i) for i in range(601)]
        unrelated=ORIGIN+'/careers/Engineer'
        maps={ORIGIN+'/robots.txt':b'User-agent: *\nAllow: /',ORIGIN+'/index.xml':b'<sitemapindex><sitemap><loc>'+ (ORIGIN+'/child.xml').encode()+b'</loc></sitemap></sitemapindex>',
              ORIGIN+'/child.xml':('<urlset>'+''.join('<url><loc>'+u+'</loc></url>' for u in urls+[unrelated])+'</urlset>').encode()}
        maps.update({u:page().encode() for u in urls})
        result,summary,requests=self.crawler(maps)
        self.assertEqual(summary['counts']['fetched_pages'],600);self.assertEqual(summary['counts']['skipped_page_cap'],1)
        self.assertEqual(summary['counts']['skipped_title'],1);self.assertNotIn(unrelated,requests);self.assertFalse(summary['listing_complete'])
        maps[ORIGIN+'/child.xml']=('<sitemapindex><sitemap><loc>'+ORIGIN+'/grand.xml</loc></sitemap></sitemapindex>').encode()
        maps[ORIGIN+'/grand.xml']=('<urlset><url><loc>'+urls[0]+'</loc></url></urlset>').encode()
        self.assertEqual(self.crawler(maps)[1]['counts']['fetched_pages'],1)
        maps[ORIGIN+'/grand.xml']=('<sitemapindex><sitemap><loc>'+ORIGIN+'/too-deep.xml</loc></sitemap></sitemapindex>').encode()
        _,summary,requests=self.crawler(maps)
        self.assertEqual(summary['counts']['sitemap_depth_rejected'],1);self.assertNotIn(ORIGIN+'/too-deep.xml',requests)

    def test_cross_board_dedup_identifier_and_location_boundaries(self):
        a={'ats':'greenhouse','employer':'Authored','title':'HR Generalist','location':'Boston, MA, US','job_id':'REQ1','dedup_key':'a'}
        b={**a,'ats':'jsonld','location':'Boston, MA','identifier':'REQ1','dedup_key':'b'}
        rows,excluded=cross_kind_dedup([b,a]);self.assertEqual(rows,[a]);self.assertEqual(excluded[0]['reason'],'cross_board_duplicate')
        self.assertEqual(len(cross_kind_dedup([a,{**b,'identifier':'REQ2'}])[0]),2)
        self.assertEqual(len(cross_kind_dedup([a,{**b,'location':'Austin, TX'}])[0]),2)
        self.assertEqual(len(cross_kind_dedup([a,{**b,'employer':'Another'}])[0]),2)

    def test_offline_capture_native_quotes_privacy_probe_and_double_projection(self):
        policy=VolumePolicy.read(SAMPLES/'policy.sample.json')
        with tempfile.TemporaryDirectory() as d,patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            root=Path(d)
            write_snapshot(policy,root/'snapshots','2026-Q4',payload_root=SAMPLES,retrieved_at=STAMP)
            manifest=read_json(root/'snapshots/2026-Q4/manifest.json')
            self.assertNotIn('user_agent',manifest['policy']);self.assertIn('user_agent_sha256',manifest['policy'])
            self.assertEqual(sum(r['admitted'] for r in manifest['roles'].values()),10)
            self.assertTrue(all(b['structured']['counts']['fetched_pages']==2 and b['structured']['counts']['skipped_title']==1 for b in manifest['boards']))
            snapshot=root/'snapshots/2026-Q4';rows=read_json(snapshot/'postings.json')
            for row in rows:
                self.assertEqual(row['source_kind'],'jsonld');self.assertEqual(len(row['page_sha256']),64)
                text=(snapshot/row['description_path']).read_text()
                for q in row['expectations']:self.assertEqual(text[q['start']:q['end']],q['source_wording'])
            self.assertTrue(all(no_contacts(p.read_text()) for p in snapshot.rglob('*') if p.is_file()))
            inputs=EvidenceInputs.from_snapshots([snapshot],ROOT/'preview/release');inputs.write(root/'a');inputs.write(root/'b')
            for p in (root/'a').rglob('*'):
                if p.is_file():self.assertEqual(p.read_bytes(),(root/'b'/p.relative_to(root/'a')).read_bytes())
            result=probe(SAMPLES/'policy.sample.json',root/'probe.json',payload_root=SAMPLES)
            self.assertEqual(len(result['boards']),5)
            self.assertTrue(all(b['sample_parseable'] and b['sitemap_found'] and b['counts']['fetched_pages']==1 for b in result['boards']))
            changed=read_json(snapshot/'postings.json');changed[0]['page_sha256']='bad'
            (snapshot/'postings.json').write_text(json.dumps(changed))
            with self.assertRaises(ValueError):EvidenceInputs.from_snapshots([snapshot],ROOT/'preview/release')

    def test_mixed_capture_dedup_and_no_output_for_private_policy_metadata(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);samples=root/'samples';shutil.copytree(SAMPLES,samples)
            value=read_json(samples/'policy.sample.json')
            value['boards'].append({'ats':'greenhouse','token':'authored','employer':'Authored Employer 0',
                'source_note':'Authored board, duplicate of structured requirement.','payload':'greenhouse.sample.json'})
            (samples/'greenhouse.sample.json').write_text(json.dumps({'jobs':[{'id':'REQ1','title':'HR Generalist',
                'absolute_url':'https://boards.greenhouse.io/authored/jobs/REQ1','location':{'name':'Boston, MA, US'},
                'content':NODE['description']}],'meta':{'total':1}}))
            policy=VolumePolicy.from_value(value,samples/'policy.sample.json')
            manifest,_=capture(policy,'2026-Q4',payload_root=samples,retrieved_at=STAMP)
            self.assertEqual(sum(r['admitted'] for r in manifest['roles'].values()),10)
            self.assertEqual(sum(r['reason']=='cross_board_duplicate' for r in manifest['exclusions']),1)
            value['unrelated_metadata']='synthetic'+chr(64)+'example.invalid'
            policy=VolumePolicy.from_value(value,samples/'policy.sample.json')
            with self.assertRaisesRegex(ValueError,'non-data content'):write_snapshot(policy,root/'not-written','2026-Q4',payload_root=samples,retrieved_at=STAMP)
            self.assertFalse((root/'not-written').exists())

    def test_fresh_cli_has_no_model_or_extra_http_client(self):
        code='''import builtins,socket,sys
original=builtins.__import__
def guarded(name,*a,**kw):
 if name.startswith(('openai','anthropic','httpx','requests','skills_vector.market.agent','skills_vector.market.remote')):raise AssertionError('forbidden dependency')
 return original(name,*a,**kw)
builtins.__import__=guarded
socket.socket.connect=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('network'))
from skills_vector.market.posting_volume import VolumePolicy,capture
from pathlib import Path
p=Path('tests/samples/jsonld')
m,_=capture(VolumePolicy.read(p/'policy.sample.json'),'2026-Q4',payload_root=p,retrieved_at='2026-10-15T00:00:00Z')
assert sum(r['admitted'] for r in m['roles'].values())==10
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
