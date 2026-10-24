"""Roster-only schema.org career pages, no open-web discovery.

Ported from general-jobs-index structured-career-page.ts/public-web.ts and
jobsss discovery.js jobPostingJsonLd (MIT). Copyright (c) 2026 Logani Paguh
Bangun. general-jobs-index credits oh-shi (MIT). No semantic HTML fallback:
only sitemap-discovered employer-published JobPosting nodes enter this lane.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from .fetch import fetch_url, scoped_transport
from .role_artifacts import canonical, read_json, require
from .sources import normalize_ws, us_location_ok

MAX_BYTES = 2_000_000
MAX_PAGES = 600
MAX_DEPTH = 2
MAX_SITEMAPS = 64
DENYLIST = Path(__file__).with_name('role_atlas_mappings') / 'jsonld_denylist.json'
CONTACT = re.compile(r'(?:[\w.+%\-]+@[\w.\-]+\.[A-Za-z]{2,}|mailto:|tel:|\b(?:phone|telephone|fax)\s*[:=]|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\(\d{3}\)[-. ]?\d{3}[-. ]\d{4})', re.I)


def no_contacts(value) -> bool:
    if isinstance(value, str): return CONTACT.search(value) is None
    if isinstance(value, dict): return all(no_contacts(k) and no_contacts(v) for k,v in value.items())
    if isinstance(value, (list, tuple)): return all(no_contacts(v) for v in value)
    return True


def safe_url(value: str, origin: str | None = None) -> str:
    require(isinstance(value, str) and no_contacts(unquote(value)), 'non-data structured URL')
    p = urlsplit(value)
    require(p.scheme == 'https' and p.hostname and p.username is None and p.password is None
            and p.port in (None,443) and not p.fragment, 'structured URLs require public HTTPS')
    host = p.hostname.lower().rstrip('.')
    require('.' in host and not host.endswith(('.local','.internal','.localhost')) and host != 'localhost', 'non-public structured host')
    try: ipaddress.ip_address(host)
    except ValueError: pass
    else: raise ValueError('IP literal structured host rejected')
    require(not set(host.split('.')) & set(read_json(DENYLIST)['labels']), 'aggregator host rejected')
    if origin:
        require(host == urlsplit(origin).hostname, 'structured URL leaves the roster site')
    return urlunsplit(('https',host,p.path or '/',p.query,''))


def validate_board(board: dict) -> None:
    origin = safe_url(board['origin'])
    require(urlsplit(origin).path == '/' and not urlsplit(origin).query, 'career origin must not contain a path or query')
    require(isinstance(board.get('note',board.get('source_note')),str) and board.get('note',board.get('source_note')).strip(), 'career origin needs an ownership note')
    if board.get('sitemap'): safe_url(board['sitemap'], origin)
    require(type(board.get('max_job_pages',MAX_PAGES)) is int and 1 <= board.get('max_job_pages',MAX_PAGES) <= MAX_PAGES, 'structured page cap exceeds 600')
    require(no_contacts(board), 'non-data board metadata')


def title_matches(value: str, hints: list[str]) -> bool:
    text = re.sub(r'[^\w]+|_', ' ', unquote(value)).casefold()
    return any(re.search(r'(?<!\w)'+re.escape(re.sub(r'[^\w]+|_', ' ', h).casefold())+r'(?!\w)',text) for h in hints)


class Robots:
    def __init__(self, text: str, user_agent: str):
        groups, current = [], None
        self.sitemaps = []
        for raw in text.splitlines():
            line=raw.split('#',1)[0].strip()
            if ':' not in line: continue
            field,value=line.split(':',1);field=field.strip().lower();value=value.strip()
            if field=='sitemap': self.sitemaps.append(value);continue
            if field=='user-agent':
                if current is None or current['seen']:
                    current={'agents':[],'rules':[], 'seen':False, 'delay':1.0};groups.append(current)
                current['agents'].append(value.lower())
            elif current is not None:
                current['seen']=True
                if field in ('allow','disallow'):current['rules'].append((field,value))
                if field=='crawl-delay':
                    try:current['delay']=max(1.0,float(value))
                    except ValueError:pass
        ua=user_agent.split()[0].lower()
        lengths=[len(a) for g in groups for a in g['agents'] if a!='*' and a in ua]
        longest=max(lengths,default=0)
        self.groups=[g for g in groups if any((a!='*' and len(a)==longest and a in ua) if longest else a=='*' for a in g['agents'])]
        self.delay=max([1.0]+[g['delay'] for g in self.groups])

    def allows(self, url: str) -> bool:
        p=urlsplit(url);path=p.path+('?' + p.query if p.query else '')
        winner=(0,True)
        for group in self.groups:
            for field,rule in group['rules']:
                if not rule: continue
                pattern='^'+re.escape(rule.rstrip('$')).replace(r'\*','.*')+('$' if rule.endswith('$') else '')
                if re.search(pattern,path): winner=max(winner,(len(rule.replace('*','').rstrip('$')),field=='allow'))
        return winner[1]


class Session:
    """One physical request/second/host, including redirects and retries."""
    def __init__(self, user_agent: str, *, transport=None, clock=time.monotonic, sleep=time.sleep, reserve=None, local=False):
        require(isinstance(user_agent,str) and user_agent.strip() and '\n' not in user_agent and '\r' not in user_agent, 'operator User-Agent required')
        self.user_agent=user_agent;self.transport=transport;self.clock=clock;self.sleep=sleep;self.reserve=reserve;self.local=local
        self.last={};self.requests=0

    def fetch(self, url: str, origin: str, robots: Robots | None = None):
        for _ in range(5):
            url=safe_url(url,origin)
            require(robots is None or robots.allows(url), 'robots disallow')
            def before():
                if self.reserve: self.reserve()
                host=urlsplit(url).hostname
                wait=self.last.get(host,-1e9)+(robots.delay if robots else 1)-self.clock()
                if wait>0 and not self.local:self.sleep(wait)
                self.last[host]=self.clock();self.requests+=1
            def check(candidate):
                try:safe_url(candidate,origin)
                except ValueError:return 'out-of-scope structured URL'
                return None
            transport=self.transport or scoped_transport(check,self.user_agent,MAX_BYTES,20)
            result=fetch_url(url,transport=transport,max_bytes=MAX_BYTES,retries=0 if self.local else 2,
                             url_check=check,before_request=before,redacted_errors=True)
            if result.status and 300<=result.status<400:
                require(result.location is not None,'redirect missing destination')
                url=safe_url(urljoin(url,result.location),origin)
                continue
            require(not result.truncated, 'structured response exceeds 2 MB')
            return result
        raise ValueError('structured redirect cap exceeded')


def local_transport(root: Path, index_path: str):
    path=(root/index_path).resolve();require(path.is_relative_to(root.resolve()),'local index escapes root')
    index=read_json(path)['responses']
    def retrieve(url):
        entry=index.get(url)
        if entry is None:return 404,{},b''
        if isinstance(entry,str):entry={'path':entry,'status':200}
        data=b''
        if entry.get('path'):
            p=(root/entry['path']).resolve();require(p.is_relative_to(root.resolve()) and not any(s.startswith('.env') for s in p.parts),'local response escapes root')
            with p.open('rb') as handle:data=handle.read(MAX_BYTES+1)
        return entry.get('status',200),entry.get('headers',{}),data
    return retrieve


class _Scripts(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=False);self.active=False;self.parts=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('type','').lower()=='application/ld+json':self.active=True;self.parts=[]
    def handle_data(self,data):
        if self.active:self.parts.append(data)
    def handle_entityref(self,name):
        if self.active:self.parts.append('&'+name+';')
    def handle_endtag(self,tag):
        if tag=='script' and self.active:self.scripts.append(''.join(self.parts));self.active=False


def job_nodes(page: str) -> list[dict]:
    parser=_Scripts();parser.feed(page);nodes=[]
    def visit(value):
        if isinstance(value,list):
            for v in value:visit(v)
        elif isinstance(value,dict):
            visit(value.get('@graph',[]))
            types=value.get('@type',[]);types=[types] if isinstance(types,str) else types
            if isinstance(types,list) and any(str(t).rsplit('/',1)[-1].casefold()=='jobposting' for t in types):nodes.append(value)
    for script in parser.scripts:
        try:visit(json.loads(script))
        except (ValueError,RecursionError):continue
    return nodes


def location(node: dict) -> str:
    parts=[]
    for key in ('jobLocation','applicantLocationRequirements'):
        values=node.get(key,[]);values=values if isinstance(values,list) else [values]
        for value in values:
            if not isinstance(value,dict):continue
            address=value.get('address',value)
            if not isinstance(address,dict):continue
            country=address.get('addressCountry','')
            if isinstance(country,dict):country=country.get('name','')
            # Explicit country fields disambiguate CA (Canada) from the US state.
            # US eligibility is still decided by the existing rule on US/unknown-country entries.
            if country and str(country).strip().casefold() not in ('us','usa','united states','united states of america','u.s.','u.s.a.'):
                continue
            text=', '.join(str(v) for v in (address.get('addressLocality'),address.get('addressRegion'),country,value.get('name')) if v)
            if text:parts.append(text)
    return '; '.join(sorted(set(parts)))


def normalize_node(node: dict, page_url: str, board: dict, as_of: str) -> tuple[dict | None,str | None]:
    title=node.get('title');description=node.get('description')
    if not isinstance(title,str) or not title.strip() or not isinstance(description,str) or not description.strip():return None,'missing_description_or_title'
    org=node.get('hiringOrganization',{})
    names=[board['employer'],*board.get('employer_aliases',[])]
    if not isinstance(org,dict) or normalize_ws(str(org.get('name',''))).casefold() not in {normalize_ws(n).casefold() for n in names}:return None,'organization_mismatch'
    expiry=node.get('validThrough')
    if expiry:
        try:
            expires=datetime.fromisoformat(str(expiry).replace('Z','+00:00')) if 'T' in str(expiry) else None
            now=datetime.fromisoformat(as_of.replace('Z','+00:00'))
            expired=(expires<=now) if expires and expires.tzinfo and now.tzinfo else date.fromisoformat(str(expiry)[:10])<now.date()
            if expired:return None,'expired'
        except ValueError:return None,'invalid_expiry'
    place=location(node)
    if not us_location_ok(place):return None,'not_explicit_US'
    identifier=node.get('identifier','')
    if isinstance(identifier,dict):identifier=identifier.get('value','')
    identifier=str(identifier) if isinstance(identifier,(str,int)) else ''
    identity=[board['employer'].casefold(), normalize_ws(title).casefold(), place.casefold(), identifier or page_url]
    selected={'id': hashlib.sha256(canonical(identity).encode()).hexdigest()[:24], 'identifier':identifier,
              'title':normalize_ws(html.unescape(title)), 'description':description, 'location':place,
              'url':safe_url(page_url,board['origin']), 'hiringOrganization':{'name':org['name']},
              'datePosted':node.get('datePosted') if isinstance(node.get('datePosted'),str) else None,
              'validThrough':expiry if isinstance(expiry,str) else None,
              'employmentType':node.get('employmentType') if isinstance(node.get('employmentType'),(str,list)) else None}
    if not no_contacts(selected):return None,'non_data_content_omitted'
    return selected,None


def crawl(board: dict, roles: dict, session: Session, as_of: str, *, probe=False) -> tuple[dict,dict]:
    validate_board(board);origin=safe_url(board['origin']);counts=Counter();problems=[]
    first=session.fetch(urljoin(origin,'/robots.txt'),origin)
    require(first.status in (200,404,410) and first.error is None,'robots not available; crawl denied')
    robots=Robots(first.body.decode('utf-8-sig') if first.status==200 else '',session.user_agent)
    roots=[board['sitemap']] if board.get('sitemap') else robots.sitemaps
    queue=[(safe_url(u,origin),0) for u in sorted(set(roots))];seen=set();urls=set()
    while queue and len(seen)<MAX_SITEMAPS:
        url,depth=queue.pop(0)
        if url in seen:continue
        seen.add(url)
        if not robots.allows(url):problems.append('robots denied sitemap');continue
        result=session.fetch(url,origin,robots)
        if not result.ok:problems.append('sitemap fetch failed');continue
        require(b'<!DOCTYPE' not in result.body.upper() and b'<!ENTITY' not in result.body.upper(),'unsafe sitemap XML')
        try:tree=ET.fromstring(result.body)
        except ET.ParseError:problems.append('invalid sitemap');continue
        kind=tree.tag.rsplit('}',1)[-1]
        require(kind in ('sitemapindex','urlset'),'unsupported sitemap root')
        for element in tree.iter():
            if element.tag.rsplit('}',1)[-1]!='loc' or not element.text:continue
            try:target=safe_url(element.text.strip(),origin)
            except ValueError:counts['offsite_urls_rejected']+=1;continue
            if kind=='sitemapindex':
                if depth>=MAX_DEPTH:counts['sitemap_depth_rejected']+=1;problems.append('sitemap depth limit')
                else:queue.append((target,depth+1))
            else:urls.add(target)
    if queue:problems.append('sitemap count limit')
    matched={role:[u for u in sorted(urls) if title_matches(urlsplit(u).path,hints)] for role,hints in roles.items()}
    selected=sorted({u for values in matched.values() for u in values})
    counts['job_urls']=len(urls);counts['skipped_title']=len(urls)-len(selected)
    maximum=1 if probe else board.get('max_job_pages',MAX_PAGES)
    counts['skipped_page_cap']=max(0,len(selected)-maximum)
    if counts['skipped_page_cap'] and not probe:problems.append('job page cap')
    jobs=[];parseable=False
    for url in selected[:maximum]:
        if not robots.allows(url):counts['robots_denied_pages']+=1;continue
        try:result=session.fetch(url,origin,robots)
        except ValueError:problems.append('page scope or response bound rejected');break
        counts['fetched_pages']+=1
        if not result.ok:problems.append('job page fetch failed');break
        nodes=job_nodes(result.body.decode('utf-8-sig'));parseable=bool(nodes) or parseable
        if not nodes:counts['no_jobposting']+=1
        for node in nodes:
            try:item,reason=normalize_node(node,url,board,as_of)
            except (ValueError,TypeError):item,reason=None,'invalid_jobposting'
            if item:
                item['page_sha256']=result.sha256;jobs.append(item)
            else:counts[reason]+=1
    unique={}
    for job in jobs:
        if job['id'] in unique:counts['duplicate_jobposting']+=1
        else:unique[job['id']]=job
    jobs=list(unique.values())
    summary={'robots_allow':robots.allows(origin), 'sitemap_found':bool(seen), 'job_url_count':len(urls),
             'matched_titles':{role:len(values) for role,values in matched.items()},'sample_parseable':parseable,
             'counts':dict(sorted(counts.items())), 'problems':sorted(set(problems)), 'listing_complete':not problems,
             'robots_sha256':first.sha256}
    # No raw HTML/robots/transport metadata is exported. The selected native fields
    # retain the unchanged description, and original page hashes provide provenance.
    return {'schema':'skills-vector.jsonld-board/1','jobs':jobs,'summary':summary},summary


def parse_collection(body: bytes) -> dict:
    value=json.loads(body)
    require(value.get('schema')=='skills-vector.jsonld-board/1' and no_contacts(value),'invalid structured board collection')
    return {'postings':[{'job_id':n['id'],'title':n['title'],'url':n['url'],'location':n['location']} for n in value['jobs']],
            'listing_complete':value['summary']['listing_complete'], 'problems':value['summary']['problems'], 'board_total':len(value['jobs'])}


def cross_kind_dedup(postings: list[dict]) -> tuple[list[dict],list[dict]]:
    kept, excluded = [], []
    def key(row):
        place = re.sub(r'(?i)\b(?:united states(?: of america)?|usa|us)\b', '', row['location'])
        return (normalize_ws(row['employer']).casefold(),
                re.sub(r'[^\w]+', ' ', row['title']).strip().casefold(),
                re.sub(r'[^\w]+', ' ', place).strip().casefold())
    for row in sorted(postings, key=lambda p:(p['ats']=='jsonld',p['dedup_key'])):
        identifier = row.get('identifier', row['job_id'])
        duplicate = next((p for p in kept if p['ats'] != row['ats'] and key(p)==key(row)
                          and (not identifier or not p.get('identifier',p['job_id']) or identifier==p.get('identifier',p['job_id']))),None)
        if duplicate:
            excluded.append({'posting_id':row['dedup_key'],'reason':'cross_board_duplicate','retained_posting_id':duplicate['dedup_key']})
        else:kept.append(row)
    return kept,excluded
