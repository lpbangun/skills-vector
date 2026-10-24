"""Offline SEC primary-employer SIC evidence and reviewed Census sector concordance.

Only explicitly allowlisted fields leave the input documents. Transport metadata
and unrelated submission fields are never serialized or included in diagnostics.
"""
from __future__ import annotations

import csv
from datetime import date
import hashlib
import re
from pathlib import Path

from .role_artifacts import read_json, require, write_json

MAPPINGS = Path(__file__).with_name('role_atlas_mappings')
XLS_SHA = 'fcb8ad7e4162a4d95c39ebe51c12e8ec20dfa6a31f2e81bf8fb1de08fbedc937'
BLOCKED = {'linear', 'mercury', 'sierra', 'vanta', 'waymo', 'zoox'}
DECISIONS = {
    '7372': ('51', 'Software publishing, NAICS 511210. Mass reproduction 334611 does not describe these registrants.'),
    '7370': ('54', 'SEC aggregate absent from the 1987 concordance; children 7371/7373 map to 5415xx.'),
    '6199': ('52', 'SEC code absent from the concordance; SIC major group 61 nondepository credit falls in 52.'),
    '5812': ('72', 'Restaurant rows apply to the registrant, not dinner theater 711110.'),
    '5500': ('44–45', 'SEC aggregate for major group 55; children map to 441xxx/447xxx.'),
    '7389': (None, 'SIC 7389 is ambiguous across sectors'),
    '7340': (None, 'SIC 7340 is ambiguous across sectors 56 and 53'),
}


def sector(code: str) -> str:
    require(code.isdigit() and len(code) == 6, 'invalid concordance NAICS')
    prefix = code[:2]
    return '31–33' if prefix in ('31', '32', '33') else '44–45' if prefix in ('44', '45') else '48–49' if prefix in ('48', '49') else prefix


def concordance(path: Path) -> dict:
    rows = {}
    with path.open(encoding='utf-8-sig', newline='') as handle:
        for r in csv.DictReader(handle):
            raw_sic = r['SIC'].strip()
            sic = raw_sic.zfill(4); naics = r['2002 NAICS'].strip()
            if not raw_sic or not sic.isdigit():
                continue
            require(len(sic) == 4, 'invalid concordance SIC')
            row = {'sic': sic, 'naics': naics, 'sector': sector(naics)}
            if row not in rows.setdefault(sic, []): rows[sic].append(row)
    return {sic: sorted(data, key=lambda r: r['naics']) for sic, data in rows.items()}


def sic_rule(sic: str, rows: dict) -> dict:
    matches = rows.get(sic, [])
    sectors = sorted({r['sector'] for r in matches})
    if sic in DECISIONS:
        target, note = DECISIONS[sic]
        return {'kind': 'reviewed_decision', 'naics': target, 'note': note,
                'review': 'docs/role-atlas/BRIEF.md M9 Part A', 'rows': matches}
    if len(sectors) == 1:
        return {'kind': 'concordance_unambiguous', 'naics': sectors[0],
                'note': 'Every concordance row for this SIC falls in the same broad sector.', 'rows': matches}
    return {'kind': 'unresolved', 'naics': None, 'note': 'No unambiguous concordance or reviewed decision.', 'rows': matches}


def validate_sector(row: dict, table: dict) -> None:
    require(all(isinstance(row.get(k), str) and re.fullmatch(pattern, row[k]) for k,pattern in
                (('cik',r'\d{10}'),('sic',r'\d{4}'),('submission_sha256',r'[a-f0-9]{64}'),('retrieved_at',r'\d{4}-\d{2}-\d{2}'))), 'invalid SEC proof fields')
    require(row.get('source_url') == 'https://data.sec.gov/submissions/CIK' + row['cik'] + '.json', 'invalid SEC proof URL')
    require(row.get('source') == 'sec_edgar' and row.get('basis') == 'primary_employer_sector', 'sector needs primary SEC evidence')
    proof = next((r for r in table['employers'] if r['employer'].casefold() == row['employer'].casefold()), None)
    require(proof is not None and all(row.get(k) == proof.get(k) for k in proof), 'SEC employer proof mismatch')
    require(row['employer'].casefold() not in BLOCKED, 'excluded employer cannot have a sector')
    rule = table['rules'].get(row['sic'])
    require(rule and row['rule'] == rule and rule['naics'] == row['naics'], 'sector rule mismatch')
    if row['status'] == 'verified':
        require(row['naics'] is not None and row['sic'] not in ('7389', '7340'), 'unresolved SIC cannot verify a sector')
        if rule['kind'] == 'concordance_unambiguous':
            require(rule['rows'] and {r['sector'] for r in rule['rows']} == {row['naics']}
                    and all(r['sic']==row['sic'] and sector(r['naics'])==row['naics'] for r in rule['rows']), 'ambiguous concordance')
        else:
            require(rule['kind'] == 'reviewed_decision' and row['sic'] in DECISIONS
                    and DECISIONS[row['sic']] == (row['naics'], rule['note']), 'unreviewed sector decision')
    else:
        require(row['naics'] is None, 'unverified employer cannot have a sector')
    require(len(row['submission_sha256']) == 64 and row['submission_file'] == 'submissions/CIK' + row['cik'] + '.json', 'missing SEC submission binding')


def import_sectors(sec: Path, csv_path: Path, mapping_root: Path = MAPPINGS,
                   roster: Path = Path('config/role-atlas-posting-volume.json')) -> dict:
    index = read_json(sec / 'employer_sic.json')
    retrieved = date.fromisoformat(index['retrieved']).isoformat()
    xls = csv_path.with_suffix('.xls')
    if xls.exists():require(hashlib.sha256(xls.read_bytes()).hexdigest()==XLS_SHA, 'source concordance XLS hash mismatch')
    rows = concordance(csv_path)
    rules, proofs = {}, []
    for item in index['rows']:
        name = item['employer']
        if name.casefold() in BLOCKED: continue
        cik, sic = str(item['cik']).zfill(10), str(item['sic']).zfill(4)
        require(cik.isdigit() and len(cik) == 10 and sic.isdigit() and len(sic) == 4, 'invalid SEC identifiers')
        path = sec / 'submissions' / ('CIK' + cik + '.json')
        native = read_json(path)
        require(str(native['cik']).zfill(10) == cik and str(native['sic']).zfill(4) == sic
                and native['sicDescription'] == item['sic_description'] and native['name'] == item['registrant'], 'SEC submission/index mismatch')
        require(item['source'] == 'https://data.sec.gov/submissions/CIK' + cik + '.json', 'invalid SEC source URL')
        rule = sic_rule(sic, rows); rules[sic] = rule
        proof = {'employer': name, 'status': 'verified' if rule['naics'] else 'unverified', 'naics': rule['naics'],
                 'source': 'sec_edgar', 'basis': 'primary_employer_sector', 'cik': cik, 'sic': sic,
                 'sic_description': native['sicDescription'], 'registrant': native['name'],
                 'retrieved_at': retrieved, 'source_url': item['source'], 'submission_file': 'submissions/' + path.name,
                 'submission_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'rule': rule}
        proofs.append(proof)
    table = {'schema': 'skills-vector.sic-sectors/1', 'concordance_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest(),
             'supplied_xls_sha256': XLS_SHA, 'rules': dict(sorted(rules.items())), 'employers': sorted(proofs, key=lambda r: r['employer'].casefold())}
    existing = {r['employer'].casefold(): r for r in read_json(mapping_root / 'employers.json')}
    for b in read_json(roster)['boards']:
        existing.setdefault(b['employer'].casefold(), {'employer': b['employer'], 'status': 'unverified', 'naics': None,
                            'source_note': 'No verified primary-employer sector supplied; excluded.'})
    for proof in proofs:
        key = proof['employer'].casefold()
        old = existing.get(key, {})
        existing[key] = {**old, **proof, 'source_note': 'SEC primary-employer SIC; ' + proof['rule']['note']}
        validate_sector(existing[key], table)
    for key, row in existing.items():
        if key in BLOCKED:
            row.update(status='unverified', naics=None)
    # Only the above allowlist can be exported; input root metadata is never copied.
    require(all('@' not in str(v) for v in (table, list(existing.values()))), 'non-data metadata rejected')
    protected = (sec, csv_path, roster)
    write_json(mapping_root / 'sic_sectors.json', table, replace=True, inputs=protected)
    write_json(mapping_root / 'employers.json', sorted(existing.values(), key=lambda r: r['employer'].casefold()), replace=True, inputs=protected)
    return {'employers': len(existing), 'sec_matches': len(proofs), 'verified': sum(r['status'] == 'verified' for r in existing.values()),
            'unverified': sum(r['status'] == 'unverified' for r in existing.values()), 'concordance_sha256': table['concordance_sha256']}
