"""M9-r1: retain native employer text, omit only contact sentences at emission."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from skills_vector.market.posting_volume import VolumePolicy, extract_sections, native_description, write_snapshot
from skills_vector.market.role_artifacts import read_json
from skills_vector.market.role_pipeline import EvidenceInputs
from skills_vector.market.role_quotes import project
from skills_vector.market.roster_probe import probe
from skills_vector.market.structured_career import CONTACT, job_nodes, local_transport, no_contacts

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'tests/samples/jsonld'
STAMP = '2026-10-15T00:00:00Z'


def employer_sentence():
    # A synthetic employer address is constructed in memory, never a repo quote.
    return 'For accommodations, write to ' + 'synthetic' + chr(64) + 'example.invalid.'


class ReviewTests(unittest.TestCase):
    def test_all_board_kinds_emit_clean_sentences_with_original_offsets(self):
        sentence = employer_sentence()
        phone = 'For accommodations call ' + '555' + '-' + '010' + '-' + '0123.'
        text = '<h2>Responsibilities</h2><li>Support onboarding. ' + sentence + ' ' + phone + ' Analyze workforce data.</li>'
        samples = {
            'greenhouse': {'content': text}, 'lever': {'description': text},
            'ashby': {'descriptionHtml': text}, 'jsonld': {'description': text},
            'smartrecruiters': {'jobAd': {'sections': {'jobDescription': {'text': text}}}},
            'workable': {'description': text},
        }
        for kind, native in samples.items():
            raw, is_html = native_description(kind, native)
            for method in ('rules/1', 'rules/2'):
                with self.subTest(kind=kind, method=method):
                    result = extract_sections(raw, is_html=is_html, method=method)
                    self.assertIn(sentence, result['description'])
                    self.assertEqual(result['contact_spans_omitted'], 2)
                    self.assertTrue(result['has_responsibilities'])
                    quotes = [e['quote'] for e in result['expectations']]
                    self.assertIn('Support onboarding.', quotes)
                    self.assertIn('Analyze workforce data.', quotes)
                    self.assertTrue(all(CONTACT.search(q) is None for q in quotes))
                    self.assertEqual(result['source_sha256'], hashlib.sha256(result['description'].encode()).hexdigest())
                    for e in result['expectations']:
                        self.assertEqual(result['description'][e['start']:e['end']], e['quote'])
            legacy = extract_sections(raw, is_html=is_html, omit_contacts=False)
            self.assertTrue(any(CONTACT.search(e['quote']) for e in legacy['expectations']))
            self.assertNotIn('contact_spans_omitted', legacy)

    def test_projection_filters_legacy_expectations_without_copying_omitted_text(self):
        text = 'Support onboarding. ' + employer_sentence() + ' Analyze data.'
        sha = hashlib.sha256(text.encode()).hexdigest()
        posting = {'source_id': 'test', 'expectations': [{'source_wording': text, 'start': 0, 'end': len(text),
                   'source_sha256': sha, 'expectation_id': 'parent'}]}
        result = project(posting, text)
        self.assertEqual([e['source_wording'] for e in result], ['Support onboarding.', 'Analyze data.'])
        self.assertEqual(posting['contact_spans_omitted'], 1)
        self.assertNotIn('quote', posting['contact_omissions'][0])
        self.assertTrue(no_contacts(posting['contact_omissions']))
        for e in result:
            self.assertEqual(text[e['start']:e['end']], e['source_wording'])

    def test_capture_admits_contact_only_postings_and_keeps_full_native_description(self):
        value = read_json(SAMPLES / 'policy.sample.json')
        value.update(user_agent='Mozilla/5.0 ExampleCrawler/1', robots_agent='ExampleCrawler')
        policy = VolumePolicy.from_value(value, SAMPLES / 'policy.sample.json')
        base = {b['origin'].rstrip('/'): local_transport(SAMPLES, b['payload']) for b in policy.value['boards']}
        sentence = employer_sentence()

        def transport(url):
            origin = next(o for o in base if url.startswith(o + '/'))
            status, headers, body = base[origin](url)
            if url.endswith('/robots.txt'):
                body = b'User-agent: *\nDisallow: /\nUser-agent: ExampleCrawler\nAllow: /'
            if '/careers/HR-Generalist' in url:
                node = deepcopy(job_nodes(body.decode())[0])
                node['description'] = '<h2>Responsibilities</h2><li>' + sentence + '</li>'
                node['hiringOrganization']['name'] = '100 ' + node['hiringOrganization']['name'] + ', Inc.'
                body = ('<script type="application/ld+json">' + json.dumps(node) + '</script>').encode()
            return status, headers, body

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_snapshot(policy, root / 'capture', '2026-Q4', payload_root=SAMPLES, retrieved_at=STAMP, transport=transport)
            snapshot = root / 'capture/2026-Q4'
            manifest = read_json(snapshot / 'manifest.json')
            self.assertEqual(sum(r['admitted'] for r in manifest['roles'].values()), 10)
            self.assertEqual(manifest['contact_spans_omitted'], 5)
            self.assertEqual(manifest['roles']['hr-generalist']['contact_spans_omitted'], 5)
            self.assertEqual(manifest['policy']['robots_agent'], 'ExampleCrawler')
            config = root / 'policy.json'
            config.write_text(json.dumps(value))
            probed = probe(config, root / 'probe.json', payload_root=SAMPLES, transport=transport)
            self.assertTrue(all(b['sample_parseable'] and b['robots_allow'] for b in probed['boards']))
            for p in read_json(snapshot / 'postings.json'):
                if p['occupation_slug'] == 'hr-generalist':
                    self.assertEqual(p['expectations'], [])
                    self.assertEqual(p['contact_spans_omitted'], 1)
                    self.assertIn(sentence, (snapshot / p['raw_description_path']).read_text())
                    self.assertIn(sentence, (snapshot / p['description_path']).read_text())
            inputs = EvidenceInputs.from_snapshots([snapshot], ROOT / 'preview/release')
            records, audit = inputs.build()
            self.assertEqual(audit['roles']['hr-generalist']['current_postings'], 5)
            self.assertEqual(audit['roles']['hr-generalist']['contact_spans_omitted'], 5)
            self.assertEqual(len(next(r for r in records if r.id == 'role.hr-generalist').skill_map.skills), 0)
            inputs.write(root / 'a')
            inputs.write(root / 'b')
            for p in (root / 'a').rglob('*.json'):
                self.assertEqual(p.read_bytes(), (root / 'b' / p.relative_to(root / 'a')).read_bytes())
                self.assertTrue(no_contacts(read_json(p)))


if __name__ == '__main__':
    unittest.main()
