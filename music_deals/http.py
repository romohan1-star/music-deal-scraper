"""Auditable requests, RFC-style robots wildcard matching and fail-closed policy."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin, unquote
import httpx
from .model import now

AGENT='MusicDealInventory/0.1 (+https://github.com/romohan1-star/music-deal-scraper)'


class AccessBlocked(RuntimeError): pass
class FetchError(RuntimeError): pass


class Robots:
    def __init__(self,text,agent='musicdealinventory'):
        groups=[]; agents=[]; rules=[]; delay=0; started=False
        for raw in text.splitlines()+['User-agent: end-of-input']:
            line=raw.split('#',1)[0].strip()
            if ':' not in line: continue
            k,v=(s.strip() for s in line.split(':',1)); k=k.lower()
            if k=='user-agent':
                if started:
                    groups.append((agents,rules,delay)); agents=[]; rules=[]; delay=0; started=False
                agents.append(v.lower())
            elif agents and k in ('allow','disallow','crawl-delay'):
                started=True
                if k=='crawl-delay':
                    try: delay=float(v)
                    except ValueError: pass
                elif v: rules.append((k,v))
        specific=[g for g in groups if any(a!='*' and a in agent for a in g[0])]
        selected=specific or [g for g in groups if '*' in g[0]]
        self.rules=[r for g in selected for r in g[1]]
        self.delay=max([g[2] for g in selected]+[0])

    def allowed(self,url):
        p=urlsplit(url); target=unquote(p.path+('?' + p.query if p.query else ''))
        matches=[]
        for kind,rule in self.rules:
            rule=unquote(rule)
            end=rule.endswith('$'); rule=rule[:-1] if end else rule
            pattern='^'+re.escape(rule).replace(r'\*','.*')+('$' if end else '')
            if re.search(pattern,target): matches.append((len(rule.replace('*','')),kind=='allow'))
        return max(matches,default=(0,True))[1]


class Client:
    def __init__(self,evidence_dir,delay=1.5,retries=2,timeout=30):
        self.client=httpx.Client(timeout=timeout,headers={'User-Agent':AGENT},follow_redirects=False)
        self.evidence=Path(evidence_dir); self.evidence.mkdir(parents=True,exist_ok=True)
        self.delay=delay; self.retries=retries; self.policies={}; self.last={}; self.blocked={}
        self.last_result={}

    def _request(self,url,delay=None):
        host=urlsplit(url).netloc
        for attempt in range(self.retries+1):
            time.sleep(max(0,(delay or self.delay)-(time.monotonic()-self.last.get(host,0))))
            self.last[host]=time.monotonic()
            try:
                r=self.client.get(url)
                fetched_at=now(); digest=hashlib.sha256(r.content).hexdigest()
                stem=hashlib.sha256((url+fetched_at+str(attempt)).encode()).hexdigest()[:24]
                path=self.evidence/(stem+'.txt'); path.write_text(r.text,encoding='utf-8')
                result={'url':url,'status':r.status_code,'attempts':attempt+1,'sha256':digest,'evidence_path':str(path),'fetched_at':fetched_at,'text':r.text,'headers':dict(r.headers)}
                self.last_result=result
                (self.evidence/(stem+'.json')).write_text(json.dumps({k:v for k,v in result.items() if k not in ('text','headers')},indent=2))
                if r.status_code==429 or r.status_code in (500,502,503,504):
                    if attempt<self.retries:
                        retry=r.headers.get('Retry-After','')
                        wait=float(retry) if retry.isdigit() else 2**(attempt+1)
                        if wait>60: raise FetchError(f'Retry-After {wait}s exceeds bounded retry budget; resume later')
                        time.sleep(wait); continue
                return result
            except httpx.RequestError as e:
                self.last_result={'url':url,'attempts':attempt+1,'fetched_at':now()}
                if attempt==self.retries: raise FetchError(str(e)) from e
                time.sleep(2**(attempt+1))
        raise FetchError('Retry budget exhausted')

    def policy(self,url):
        s=urlsplit(url); origin=f'{s.scheme}://{s.netloc}'
        if origin in self.blocked: raise AccessBlocked(self.blocked[origin])
        if origin not in self.policies:
            r=self._request(origin+'/robots.txt')
            if r['status']==404:
                self.policies[origin]=Robots('')
            elif r['status']==200 and re.search(r'(?im)^\s*(user-agent|sitemap)\s*:',r['text']):
                self.policies[origin]=Robots(r['text'])
            else:
                msg=f"robots policy unavailable (HTTP {r['status']}); fail closed"
                self.blocked[origin]=msg; raise AccessBlocked(msg)
        return self.policies[origin]

    def get(self,url):
        for _ in range(6):
            s=urlsplit(url)
            if s.scheme!='https': raise AccessBlocked('Only HTTPS retailer requests allowed')
            policy=self.policy(url)
            if not policy.allowed(url): raise AccessBlocked('robots.txt disallows this URL')
            r=self._request(url,max(self.delay,policy.delay))
            if r['status'] in (301,302,303,307,308):
                target=urljoin(url,r['headers'].get('location',''))
                if urlsplit(target).hostname not in (s.hostname, (s.hostname or '').removeprefix('www.'), 'www.'+(s.hostname or '')):
                    raise AccessBlocked('Cross-domain redirect requires configuration review')
                url=target; continue
            challenge=bool(re.search(r'<title>[^<]*(access denied|just a moment|captcha)|cf-chl-|px-captcha',r['text'],re.I))
            if r['status'] in (401,403) or challenge:
                msg=f"Access control/challenge (HTTP {r['status']}); no bypass attempted"
                self.blocked[f'{s.scheme}://{s.netloc}']=msg; raise AccessBlocked(msg)
            if r['status']==429:
                msg='Rate limited after bounded retries; stop this domain for the run and resume later'
                self.blocked[f'{s.scheme}://{s.netloc}']=msg
                raise AccessBlocked(msg)
            if r['status']!=200: raise FetchError(f"HTTP {r['status']} for {url}")
            return r
        raise FetchError('Redirect limit reached')

    def close(self): self.client.close()
