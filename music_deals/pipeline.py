import hashlib
import json
import random
import re
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .collectors import ADAPTERS, ShopifyCollector, promo_links, json_objects
from .http import Client, AccessBlocked
from .model import canonical, money, now
from .store import Store


def terms_text(html):
    s=BeautifulSoup(html,'html.parser')
    body=s.select_one('.shopify-policy__body,.column.main,main') or s
    return re.sub(r'\s+',' ',body.get_text(' ',strip=True)).strip()


def terms_hash(html): return hashlib.sha256(terms_text(html).encode()).hexdigest()


def collect(config,db_path='data/deals.sqlite',out='reports',run_id=None,retailers=None,max_pages=None):
    store=Store(db_path)
    run_id=run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    # Prevent path injection from workflow input / command line.
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id): raise ValueError('Invalid run ID')
    raw=json.dumps(config,sort_keys=True)
    existing=store.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if existing and existing['config_json']!=raw: raise ValueError('Resume requires identical configuration; start a new run after config changes')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO runs(id,started_at,config_json) VALUES(?,?,?)',(run_id,now(),raw))
    print(f'RUN_ID={run_id}',flush=True)
    try:
        for cfg in sorted(config['retailers'],key=lambda x:x['tier']):
            if retailers and cfg['id'] not in retailers: continue
            print(f"START {cfg['id']}",flush=True)
            client=Client(f'data/evidence/{run_id}/{cfg["id"]}',config.get('delay_seconds',1.5))
            try: run_retailer(store,client,run_id,cfg,config,max_pages)
            except Exception as e:
                store.error(run_id,cfg['id'],cfg['base'],'retailer',repr(e))
                with store.db:
                    store.db.execute("UPDATE collections SET status='PARTIAL',reason=? WHERE run_id=? AND retailer=? AND status!='BLOCKED'",(f'Retailer exception: {e}',run_id,cfg['id']))
            finally:
                client.close()
                # A report checkpoint after EVERY retailer, including failures.
                from .report import export
                export(store,run_id,out)
            print(f"FINISH {cfg['id']} offers={len([o for o in store.current(run_id) if o['retailer']==cfg['id']])}",flush=True)
        with store.db: store.db.execute('UPDATE runs SET finished_at=? WHERE id=?',(now(),run_id))
        from .report import export
        export(store,run_id,out)
    finally: store.close()
    return run_id


def run_retailer(store,client,run,cfg,config,max_pages):
    retailer=cfg['id']; seeds=[canonical(urljoin(cfg['base'],x)) for x in cfg['seeds']]
    for seed in seeds: store.collection(run,retailer,seed)
    if cfg.get('policy')=='blocked':
        reason=cfg['policy_reason']+' Source: '+urljoin(cfg['base'],cfg.get('terms','/'))
        with store.db: store.db.execute("UPDATE collections SET status='BLOCKED',reason=? WHERE run_id=? AND retailer=?",(reason,run,retailer))
        store.error(run,retailer,cfg['base'],'terms_policy',reason); return
    try:
        home=client.get(cfg['base']+'/')
        if cfg.get('policy')!='reviewed':
            raise AccessBlocked('Site became accessible but terms and retailer parser require review before collection')
        if cfg.get('terms'):
            terms=client.get(urljoin(cfg['base'],cfg['terms']))
            if terms_hash(terms['text'])!=cfg.get('terms_sha256'):
                raise AccessBlocked('Terms changed since recorded review; inspect terms before updating reviewed hash')
    except Exception as e:
        status='BLOCKED' if isinstance(e,AccessBlocked) else 'PARTIAL'
        for seed in seeds:
            store.save_page(run,retailer,seed,client.last_result.get('url',cfg['base']+'/'),0,client.last_result,[],[],status='failed',error=str(e))
        with store.db: store.db.execute('UPDATE collections SET status=?,reason=? WHERE run_id=? AND retailer=?',(status,str(e),run,retailer))
        store.error(run,retailer,cfg['base'],'access',str(e)); return
    adapter=ADAPTERS[cfg['adapter']](cfg); adapter.metadata(home['text'])
    queue=deque(seeds+promo_links(BeautifulSoup(home['text'],'html.parser'),cfg['base']+'/'))
    seen=set()
    # Restore discovered collections on resume, not just original seeds.
    queue.extend(r[0] for r in store.db.execute('SELECT url FROM collections WHERE run_id=? AND retailer=?',(run,retailer)))
    for url in set(queue): store.collection(run,retailer,canonical(url))
    limit=config.get('max_collections_per_retailer',100)
    while queue and len(seen)<limit:
        collection=canonical(queue.popleft())
        if collection in seen: continue
        seen.add(collection); store.collection(run,retailer,collection)
        children=crawl_collection(store,client,run,cfg,adapter,collection,max_pages or config.get('max_pages_per_collection',500),config.get('sample_products',3))
        for url in children: store.collection(run,retailer,url)
        queue.extend(children)
    if queue:
        for url in sorted(set(queue)-seen):
            store.collection(run,retailer,url)
            with store.db: store.db.execute("UPDATE collections SET status='PARTIAL',reason='Collection discovery cap reached; queued collection not visited' WHERE run_id=? AND retailer=? AND url=?",(run,retailer,url))
        store.error(run,retailer,cfg['base'],'discovery','Collection cap reached; rerun with higher cap')
    store.error(run,retailer,cfg['base'],'scope_note','Discovery follows same-site promotion links from homepage and collected landing pages. Unlinked campaigns, email coupons, login/cart offers and entire-site completeness are unverified.'+((' '+cfg['policy_reason']) if cfg.get('policy_reason') else ''))


def crawl_collection(store,client,run,cfg,adapter,collection,page_limit,samples):
    retailer=cfg['id']; queue=deque([collection]); visited=set(); fingerprints=set(); children=set(); terminal=False; count=None; reliable=False; failed=False; blocked=False; capped=False
    product_pages=[]; all_offers=[]; reason=''
    existing=store.db.execute('SELECT * FROM collections WHERE run_id=? AND retailer=? AND url=?',(run,retailer,collection)).fetchone()
    completed_checkpoint=bool(existing and existing['terminal'] and existing['status'] in ('VERIFIED COMPLETE','UNVERIFIABLE'))
    while queue:
        if len(visited)>=page_limit: capped=True; reason='Page cap reached; queued pages remain'; break
        url=queue.popleft()
        if url in visited: continue
        if urlsplit(url).netloc!=urlsplit(cfg['base']).netloc: raise ValueError('Pagination left configured retailer')
        visited.add(url); index=len(visited)
        old=store.page(run,retailer,collection,url)
        result=None
        try:
            if old and old['status']=='completed' and old['evidence_path'] and Path(old['evidence_path']).exists():
                result={'text':Path(old['evidence_path']).read_text(),'fetched_at':old['fetched_at'],'sha256':old['sha256'],'evidence_path':old['evidence_path'],'status':old['http_status'],'attempts':0}
                cached=True
            else: result=client.get(url); cached=False
            parsed=adapter.parse(result['text'],url,collection)
            if cached:
                # Preserve the original pagination route within a resumed run.
                parsed.next_urls=json.loads(old['next_json'] or '[]')
            if index>1 and not parsed.offers and not parsed.next_urls and not parsed.terminal:
                raise ValueError('Expected pagination page contains no supported listings or explicit end marker: '+url)
            for o in parsed.offers: o.collected_at=result['fetched_at']
            children.update(parsed.collections)
            if parsed.advertised_count is not None:
                if count is not None and count!=parsed.advertised_count:
                    if 'changed during pagination' not in reason:
                        store.error(run,retailer,url,'count_drift',f'Advertised count changed from {count} to {parsed.advertised_count}')
                    failed=True; reason='Advertised count changed during pagination'
                count=parsed.advertised_count; reliable=parsed.count_reliable
                with store.db:
                    store.db.execute('UPDATE collections SET advertised_count=?,count_reliable=?,count_unit=? WHERE run_id=? AND retailer=? AND url=?',(count,int(reliable),'products (not variants)',run,retailer,collection))
            ids=tuple(sorted(set(parsed.listing_ids)))
            if ids and ids in fingerprints:
                raise ValueError('Repeated product page: pagination may be ignored or looping')
            if ids: fingerprints.add(ids); product_pages.append((url,ids,result)); all_offers.extend(parsed.offers)
            store.save_page(run,retailer,collection,url,index,result,parsed.offers,parsed.next_urls)
            terminal=terminal or parsed.terminal
            if any(u in visited for u in parsed.next_urls): raise ValueError('Pagination loops to an already visited page')
            queue.extend(parsed.next_urls)
            if not parsed.offers and not parsed.next_urls and not parsed.terminal:
                reason=reason or parsed.reason or 'No supported listing data or pagination found'
        except Exception as e:
            failed=True; blocked=isinstance(e,AccessBlocked); reason=(reason+'; ' if reason else '')+str(e)
            store.save_page(run,retailer,collection,url,index,result or client.last_result,[],[],status='failed',error=str(e))
            store.error(run,retailer,url,'collection',str(e))
            break
    observed=store.db.execute('SELECT COUNT(DISTINCT listing_id) FROM membership WHERE run_id=? AND retailer=? AND collection_url=?',(run,retailer,collection)).fetchone()[0]
    if isinstance(adapter,ShopifyCollector) and product_pages and not failed and not capped:
        if completed_checkpoint and existing['count_reliable']:
            count=existing['advertised_count']; reliable=True
        else:
            metadata_count=check_collection_count(store,client,run,retailer,collection)
            if metadata_count is not None: count=metadata_count; reliable=True
    validation_ok=False
    if completed_checkpoint and not failed and not capped:
        # Same immutable run snapshot: retain completed verification on resume.
        validation_ok=existing['status']=='VERIFIED COMPLETE'
    elif product_pages and not failed and not capped:
        validation_ok=validate_pages(store,client,run,retailer,collection,adapter,product_pages)
        validate_samples(store,client,run,cfg,collection,all_offers,samples)
    if blocked: status='PARTIAL' if observed else 'BLOCKED'
    elif failed or capped: status='PARTIAL'
    elif count is not None and reliable and terminal and observed==count and validation_ok: status='VERIFIED COMPLETE'; reason='Known collection count reconciled; first/middle/final product pages rechecked'
    elif count is not None and reliable and observed!=count: status='PARTIAL'; reason=f'Count mismatch: advertised {count}, collected {observed}'
    else:
        status='UNVERIFIABLE'
        reason=reason or ('Pagination exhausted; no reliable advertised denominator' if terminal else 'Landing page only or unsupported pagination; child collections reported separately')
    with store.db:
        store.db.execute('UPDATE collections SET status=?,advertised_count=?,count_unit=?,count_reliable=?,terminal=?,reason=? WHERE run_id=? AND retailer=? AND url=?',(status,count,'products (not variants)',int(reliable),int(terminal),reason,run,retailer,collection))
    print(f'  {retailer} {collection} {status} listings={observed} pages={len(visited)}',flush=True)
    return sorted(children)


def validation(store,run,retailer,collection,url,kind,status,detail):
    with store.db:
        store.db.execute('DELETE FROM validations WHERE run_id=? AND retailer=? AND collection_url=? AND source_url=? AND check_type=?',(run,retailer,collection,url,kind))
        store.db.execute('INSERT INTO validations(run_id,retailer,collection_url,source_url,check_type,status,detail) VALUES(?,?,?,?,?,?,?)',(run,retailer,collection,url,kind,status,detail))


def check_collection_count(store,client,run,retailer,collection):
    url=collection.rstrip('/')+'.json'
    try:
        r=client.get(url)
        count=json.loads(r['text']).get('collection',{}).get('products_count')
        if isinstance(count,bool) or not isinstance(count,int) or count<0: raise ValueError('No explicit collection products_count')
        validation(store,run,retailer,collection,url,'collection_count','PASS',f'Explicit retailer collection products_count={count}; evidence={r["evidence_path"]}')
        return count
    except Exception as e:
        validation(store,run,retailer,collection,url,'collection_count','UNVERIFIABLE',str(e))
        return None


def validate_pages(store,client,run,retailer,collection,adapter,pages):
    ok=True
    for i in sorted({0,len(pages)//2,len(pages)-1}):
        url,ids,_=pages[i]
        try:
            r=client.get(url); p=adapter.parse(r['text'],url,collection)
            stable=tuple(sorted(set(p.listing_ids)))==ids
            validation(store,run,retailer,collection,url,'boundary_page','PASS' if stable else 'FAIL',f'Product page {i+1}/{len(pages)}; IDs stable={stable}; evidence={r["evidence_path"]}')
            ok=ok and stable
        except Exception as e:
            ok=False; validation(store,run,retailer,collection,url,'boundary_page','UNVERIFIABLE',str(e))
    return ok


def validate_samples(store,client,run,cfg,collection,offers,n):
    # Deterministic random sample, no product detail sweep.
    unique={o.offer_id:o for o in offers}
    chosen=random.Random(run+collection).sample(list(unique.values()),min(n,len(unique)))
    for o in chosen:
        try:
            r=client.get(o.product_url); soup=BeautifulSoup(r['text'],'html.parser')
            prices=[]; titles=[]
            normalize=lambda t: re.sub(r'\s+',' ',t or '').strip().casefold()
            for obj in json_objects(soup):
                if obj.get('@type')=='Product':
                    name=obj.get('name',''); titles.append(name)
                    if normalize(name)!=normalize(o.product_title): continue
                    product_offers=obj.get('offers',[])
                    if isinstance(product_offers,dict): product_offers=[product_offers]
                    for product_offer in product_offers:
                        if isinstance(product_offer,dict):
                            price=money(product_offer.get('price'))
                            if price is not None: prices.append(price)
            if not titles:
                h1=soup.select_one('h1.page-title,h1[itemprop="name"]')
                if h1: titles.append(h1.get_text(' ',strip=True))
            if not prices:
                # Restrict to the detail's main price block, excluding suggestions.
                main=soup.select_one('.product-info-main')
                if main:
                    prices=[money(x.get('data-price-amount')) for x in main.select('[data-price-type="finalPrice"]')]
                    prices=[p for p in prices if p is not None]
            # Shopify product JSON is often embedded in HTML even without JSON-LD.
            title_ok=any(normalize(t)==normalize(o.product_title) for t in titles)
            price_ok=o.promotional_price is not None and o.promotional_price in prices
            status='PASS' if title_ok and price_ok else ('FAIL' if titles and prices else 'UNVERIFIABLE')
            validation(store,run,cfg['id'],collection,o.product_url,'random_product',status,f'title_match={title_ok}; price_match={price_ok}; detail_prices={prices}; evidence={r["evidence_path"]}')
        except Exception as e: validation(store,run,cfg['id'],collection,o.product_url,'random_product','UNVERIFIABLE',str(e))
